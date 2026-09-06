"""Fsync-safe single-head journal for host runtime transitions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator

from apps.host_control.atomic import atomic_write, durable_unlink, read_bounded_regular
from apps.host_control.lock import HostTransitionLock
from apps.host_control.policy import MaterializedHostPolicy
from packages.domain import canonical_json_sha256
from packages.domain._base import ContractModel, NonEmptyText, Sha256Hex, ShortReason

_MAX_RECORD_BYTES = 128 * 1024
_MAX_EVENT_BYTES = 128 * 1024


class HostTransitionState(StrEnum):
    CHECKING = "checking"
    RETRY_WAIT = "retry_wait"
    READY_TO_START = "ready_to_start"
    RESUME_DEGRADED = "resume_degraded"
    RESUME_BLOCKED = "resume_blocked"
    RESOLVED = "resolved"


class HostOperation(StrEnum):
    RUNTIME_START = "runtime_start"
    OFFLINE_RULES = "offline_rules"


_ALLOWED_TRANSITIONS = {
    HostTransitionState.CHECKING: {
        HostTransitionState.CHECKING,
        HostTransitionState.RETRY_WAIT,
        HostTransitionState.READY_TO_START,
        HostTransitionState.RESUME_BLOCKED,
        HostTransitionState.RESUME_DEGRADED,
    },
    HostTransitionState.RETRY_WAIT: {HostTransitionState.CHECKING},
    HostTransitionState.READY_TO_START: {
        HostTransitionState.CHECKING,
        HostTransitionState.RESOLVED,
        HostTransitionState.RESUME_DEGRADED,
    },
    HostTransitionState.RESUME_DEGRADED: {HostTransitionState.CHECKING},
    HostTransitionState.RESUME_BLOCKED: {HostTransitionState.CHECKING},
    HostTransitionState.RESOLVED: set(),
}


class HostJournalError(RuntimeError):
    """Base class for a rejected host-journal operation."""


class HostTransitionInProgressError(HostJournalError):
    """A second long-running host operation attempted to acquire the active head."""


class HostJournalInconsistentError(HostJournalError):
    """The local head, record or immutable event prefix is inconsistent."""


class HostPolicyChangeInProgressError(HostJournalError):
    """Host recovery policy installation serializes all host transitions."""


class HostPolicySnapshot(ContractModel):
    schema_version: Literal[1]
    source_kind: Literal["packaged", "override"]
    source_path: NonEmptyText
    source_file_sha256: Sha256Hex
    canonical_sha256: Sha256Hex
    values: dict[str, int | float]

    @classmethod
    def from_materialized(cls, value: MaterializedHostPolicy) -> HostPolicySnapshot:
        return cls(
            schema_version=value.policy.schema_version,
            source_kind=value.source_kind,
            source_path=str(value.source_path),
            source_file_sha256=value.source_file_sha256,
            canonical_sha256=value.canonical_sha256,
            values=value.policy.canonical_material(),
        )

    @model_validator(mode="after")
    def verify_canonical_hash(self) -> HostPolicySnapshot:
        if canonical_json_sha256(self.values) != self.canonical_sha256:
            raise ValueError("materialized host policy hash does not match")
        return self


class HostTransitionSnapshot(ContractModel):
    state: HostTransitionState
    attempts_total: int = Field(ge=0)
    current_attempt_seq: int = Field(ge=0)
    consecutive_unclassified_failures: int = Field(ge=0)
    backoff_step: int = Field(ge=0)
    error_class: str | None = Field(default=None, max_length=128)
    last_probe_boot_id: UUID | None = None
    last_probe_started_at: AwareDatetime | None = None
    last_probe_classified_at: AwareDatetime | None = None
    next_attempt_at: AwareDatetime | None = None
    dispatch_id: UUID | None = None
    dispatch_deadline: AwareDatetime | None = None

    @model_validator(mode="after")
    def validate_tuples(self) -> HostTransitionSnapshot:
        if (self.dispatch_id is None) != (self.dispatch_deadline is None):
            raise ValueError("dispatch ID and deadline must be set together")
        if self.state in {
            HostTransitionState.RETRY_WAIT,
            HostTransitionState.RESUME_DEGRADED,
        } and self.next_attempt_at is None:
            raise ValueError("retry states require next_attempt_at")
        if self.state is HostTransitionState.RESUME_BLOCKED and not self.error_class:
            raise ValueError("resume_blocked requires an error class")
        if self.state is HostTransitionState.READY_TO_START and self.dispatch_id is None:
            raise ValueError("ready_to_start requires a dispatch tuple")
        if self.state in {
            HostTransitionState.RESUME_BLOCKED,
            HostTransitionState.RESOLVED,
        } and (self.next_attempt_at is not None or self.dispatch_id is not None):
            raise ValueError("terminal-like host states cannot retain retry or dispatch data")
        return self


class HostTransitionEvent(ContractModel):
    schema_version: Literal["host-transition-event/v1"] = "host-transition-event/v1"
    attempt_id: UUID
    operation: HostOperation
    event_seq: int = Field(ge=1)
    from_state: HostTransitionState | None
    to_state: HostTransitionState
    actor: ShortReason
    reason: NonEmptyText
    error_class: str | None = Field(default=None, max_length=128)
    occurred_at: AwareDatetime
    snapshot: HostTransitionSnapshot

    @model_validator(mode="after")
    def state_matches_snapshot(self) -> HostTransitionEvent:
        if self.snapshot.state is not self.to_state:
            raise ValueError("event destination must match its resulting snapshot")
        if self.snapshot.error_class != self.error_class:
            raise ValueError("event error class must match its resulting snapshot")
        return self


class HostTransitionRecord(ContractModel):
    schema_version: Literal["host-transition/v1"] = "host-transition/v1"
    attempt_id: UUID
    operation: HostOperation
    created_at: AwareDatetime
    creation_boot_id: UUID
    initial_event_sha256: Sha256Hex
    candidate_snapshot_id: UUID | None = None
    base_snapshot_id: UUID | None = None
    observed_active_snapshot_id: UUID | None = None
    policy: HostPolicySnapshot
    snapshot: HostTransitionSnapshot
    last_event_seq: int = Field(ge=1)
    replayed_through_seq: int = Field(default=0, ge=0)
    replayed_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def replay_cursor_is_bounded(self) -> HostTransitionRecord:
        if self.replayed_through_seq > self.last_event_seq:
            raise ValueError("host event replay cursor exceeds local history")
        if (self.replayed_through_seq == 0) != (self.replayed_at is None):
            raise ValueError("host event replay timestamp must match a nonzero cursor")
        return self

    @property
    def state(self) -> HostTransitionState:
        return self.snapshot.state

    @property
    def identity_sha256(self) -> str:
        return canonical_json_sha256(
            {
                "schema_version": self.schema_version,
                "attempt_id": str(self.attempt_id),
                "operation": self.operation.value,
                "created_at": _timestamp(self.created_at),
                "initial_event_sha256": self.initial_event_sha256,
            }
        )


class HostTransitionHead(ContractModel):
    schema_version: Literal["host-transition-head/v1"] = "host-transition-head/v1"
    attempt_id: UUID
    record_identity_sha256: Sha256Hex
    created_at: AwareDatetime
    creation_boot_id: UUID


class HostJournalPaths:
    def __init__(self, root: Path, *, lock_path: Path | None = None) -> None:
        self.root = root
        self.records = root / "host-transitions"
        self.events = root / "host-transition-events"
        self.head = root / "host-transition-head.json"
        self.policy_change_head = root / "host-policy-change-head.json"
        self.policy_events = root / "host-policy-events"
        self.lock = lock_path or root / "host-transition.lock"


class HostTransitionJournal:
    def __init__(
        self,
        paths: HostJournalPaths,
        *,
        lock: HostTransitionLock | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self.paths = paths
        self._lock = lock or HostTransitionLock(paths.lock)
        self._failpoint = failpoint or (lambda _name: None)

    def create(
        self,
        *,
        operation: HostOperation,
        boot_id: UUID,
        policy: MaterializedHostPolicy,
        actor: str,
        reason: str,
        occurred_at: datetime,
        candidate_snapshot_id: UUID | None = None,
        base_snapshot_id: UUID | None = None,
        observed_active_snapshot_id: UUID | None = None,
        attempt_id: UUID | None = None,
        lock_deadline_seconds: float = 2.0,
        initial_snapshot_updates: dict[str, object] | None = None,
    ) -> HostTransitionRecord:
        timestamp = _aware(occurred_at)
        with self._lock.acquire(deadline_seconds=lock_deadline_seconds):
            if _present(self.paths.policy_change_head):
                raise HostPolicyChangeInProgressError("host policy change is in progress")
            active = self._reconcile_locked()
            if active is not None:
                raise HostTransitionInProgressError(str(active.attempt_id))
            identifier = attempt_id or uuid4()
            snapshot_values: dict[str, object] = {
                "state": HostTransitionState.CHECKING,
                "attempts_total": 0,
                "current_attempt_seq": 0,
                "consecutive_unclassified_failures": 0,
                "backoff_step": 0,
                "error_class": None,
                "last_probe_boot_id": None,
                "last_probe_started_at": None,
                "last_probe_classified_at": None,
                "next_attempt_at": None,
                "dispatch_id": None,
                "dispatch_deadline": None,
            }
            snapshot_values.update(initial_snapshot_updates or {})
            snapshot_values["state"] = HostTransitionState.CHECKING
            snapshot = HostTransitionSnapshot.model_validate(snapshot_values)
            event = HostTransitionEvent(
                attempt_id=identifier,
                operation=operation,
                event_seq=1,
                from_state=None,
                to_state=HostTransitionState.CHECKING,
                actor=actor,
                reason=reason,
                occurred_at=timestamp,
                snapshot=snapshot,
            )
            event_bytes = _encode(event)
            initial_sha256 = hashlib.sha256(event_bytes).hexdigest()
            record = HostTransitionRecord(
                attempt_id=identifier,
                operation=operation,
                created_at=timestamp,
                creation_boot_id=boot_id,
                initial_event_sha256=initial_sha256,
                candidate_snapshot_id=candidate_snapshot_id,
                base_snapshot_id=base_snapshot_id,
                observed_active_snapshot_id=observed_active_snapshot_id,
                policy=HostPolicySnapshot.from_materialized(policy),
                snapshot=snapshot,
                last_event_seq=1,
                replayed_through_seq=0,
                replayed_at=None,
            )
            self._write_event(event, event_bytes)
            self._failpoint("after_initial_event")
            self._write_record(record)
            self._failpoint("after_initial_record")
            self._write_head(record)
            self._failpoint("after_initial_head")
            return record

    def active(self) -> HostTransitionRecord | None:
        with self._lock.acquire():
            return self._reconcile_locked()

    def events(self, attempt_id: UUID) -> tuple[HostTransitionEvent, ...]:
        with self._lock.acquire():
            return self._load_events(attempt_id)

    def transition(
        self,
        attempt_id: UUID,
        *,
        to_state: HostTransitionState,
        actor: str,
        reason: str,
        occurred_at: datetime,
        error_class: str | None = None,
        snapshot_updates: dict[str, object] | None = None,
    ) -> HostTransitionRecord:
        timestamp = _aware(occurred_at)
        with self._lock.acquire():
            record = self._require_active_locked(attempt_id)
            if to_state not in _ALLOWED_TRANSITIONS[record.state]:
                raise HostJournalError(f"invalid host transition: {record.state} -> {to_state}")
            updates = dict(snapshot_updates or {})
            updates.update(state=to_state, error_class=error_class)
            snapshot = HostTransitionSnapshot.model_validate(
                {**record.snapshot.model_dump(mode="python"), **updates}
            )
            event = HostTransitionEvent(
                attempt_id=record.attempt_id,
                operation=record.operation,
                event_seq=record.last_event_seq + 1,
                from_state=record.state,
                to_state=to_state,
                actor=actor,
                reason=reason,
                error_class=error_class,
                occurred_at=timestamp,
                snapshot=snapshot,
            )
            self._write_event(event)
            self._failpoint("after_transition_event")
            updated = record.model_copy(
                update={"snapshot": snapshot, "last_event_seq": event.event_seq}
            )
            self._write_record(updated)
            self._failpoint("after_transition_record")
            return updated

    def mark_replayed(
        self,
        attempt_id: UUID,
        *,
        through_seq: int,
        replayed_at: datetime,
    ) -> HostTransitionRecord:
        with self._lock.acquire():
            record = self._require_active_locked(attempt_id)
            if not record.replayed_through_seq <= through_seq <= record.last_event_seq:
                raise HostJournalError("host replay cursor must advance within local history")
            updated = record.model_copy(
                update={
                    "replayed_through_seq": through_seq,
                    "replayed_at": _aware(replayed_at),
                }
            )
            self._write_record(updated)
            if (
                updated.state is HostTransitionState.RESOLVED
                and updated.replayed_through_seq == updated.last_event_seq
            ):
                durable_unlink(self.paths.head)
            return updated

    def _reconcile_locked(self) -> HostTransitionRecord | None:
        try:
            head = _load(HostTransitionHead, self.paths.head, _MAX_RECORD_BYTES)
        except FileNotFoundError:
            unresolved: list[HostTransitionRecord] = []
            if self.paths.records.exists():
                for path in sorted(self.paths.records.glob("*.json")):
                    record = _load(HostTransitionRecord, path, _MAX_RECORD_BYTES)
                    if (
                        record.state is not HostTransitionState.RESOLVED
                        or record.replayed_through_seq != record.last_event_seq
                    ):
                        unresolved.append(record)
            if len(unresolved) > 1:
                raise HostJournalInconsistentError("multiple unresolved host records exist")
            if not unresolved:
                return None
            record = self._repair_from_events(unresolved[0])
            self._write_head(record)
            return record
        except (OSError, ValueError) as exc:
            raise HostJournalInconsistentError("active host-transition head is invalid") from exc

        record_path = self.paths.records / f"{head.attempt_id}.json"
        try:
            record = _load(HostTransitionRecord, record_path, _MAX_RECORD_BYTES)
            if record.identity_sha256 != head.record_identity_sha256:
                raise ValueError("head identity does not match current record")
            if (
                record.created_at != head.created_at
                or record.creation_boot_id != head.creation_boot_id
            ):
                raise ValueError("head immutable header does not match current record")
            record = self._repair_from_events(record)
        except (OSError, ValueError) as exc:
            raise HostJournalInconsistentError("active host transition is inconsistent") from exc
        if (
            record.state is HostTransitionState.RESOLVED
            and record.replayed_through_seq == record.last_event_seq
        ):
            durable_unlink(self.paths.head)
            return None
        return record

    def _repair_from_events(self, record: HostTransitionRecord) -> HostTransitionRecord:
        events = self._load_events(record.attempt_id)
        _validate_event_prefix(record, events)
        if record.last_event_seq > len(events):
            raise ValueError("current record points past immutable history")
        latest = events[-1]
        if record.last_event_seq == len(events) and record.snapshot != latest.snapshot:
            raise ValueError("current record disagrees with immutable history")
        if record.last_event_seq < len(events):
            record = record.model_copy(
                update={"snapshot": latest.snapshot, "last_event_seq": latest.event_seq}
            )
            self._write_record(record)
        return record

    def _load_events(self, attempt_id: UUID) -> tuple[HostTransitionEvent, ...]:
        directory = self.paths.events / str(attempt_id)
        if not directory.exists():
            return ()
        paths: list[tuple[int, Path]] = []
        for path in directory.glob("*.json"):
            try:
                sequence = int(path.stem)
            except ValueError as exc:
                raise HostJournalInconsistentError("host event filename is invalid") from exc
            paths.append((sequence, path))
        paths.sort(key=lambda item: item[0])
        return tuple(_load(HostTransitionEvent, path, _MAX_EVENT_BYTES) for _, path in paths)

    def _require_active_locked(self, attempt_id: UUID) -> HostTransitionRecord:
        record = self._reconcile_locked()
        if record is None or record.attempt_id != attempt_id:
            raise HostJournalError("host transition no longer owns the active head")
        return record

    def _write_event(
        self,
        event: HostTransitionEvent,
        encoded: bytes | None = None,
    ) -> None:
        path = self.paths.events / str(event.attempt_id) / f"{event.event_seq:020d}.json"
        if path.exists():
            existing = read_bounded_regular(path, maximum_bytes=_MAX_EVENT_BYTES)
            if existing != (encoded or _encode(event)):
                raise HostJournalInconsistentError("immutable host event identity conflict")
            return
        atomic_write(path, encoded or _encode(event))

    def _write_record(self, record: HostTransitionRecord) -> None:
        atomic_write(self.paths.records / f"{record.attempt_id}.json", _encode(record))

    def _write_head(self, record: HostTransitionRecord) -> None:
        head = HostTransitionHead(
            attempt_id=record.attempt_id,
            record_identity_sha256=record.identity_sha256,
            created_at=record.created_at,
            creation_boot_id=record.creation_boot_id,
        )
        atomic_write(self.paths.head, _encode(head))


def read_active_transition(head_path: Path) -> HostTransitionRecord | None:
    """Read and verify a complete head/current/event tuple without mutation."""

    try:
        head = _load(HostTransitionHead, head_path, _MAX_RECORD_BYTES)
    except FileNotFoundError:
        return None
    record = _load(
        HostTransitionRecord,
        head_path.parent / "host-transitions" / f"{head.attempt_id}.json",
        _MAX_RECORD_BYTES,
    )
    if (
        record.identity_sha256 != head.record_identity_sha256
        or record.created_at != head.created_at
        or record.creation_boot_id != head.creation_boot_id
    ):
        raise HostJournalInconsistentError("head does not identify its current host record")
    events = _read_event_stream(
        head_path.parent / "host-transition-events" / str(record.attempt_id)
    )
    try:
        _validate_event_prefix(record, events)
    except ValueError as exc:
        raise HostJournalInconsistentError("host event prefix is inconsistent") from exc
    if len(events) != record.last_event_seq or not events or events[-1].snapshot != record.snapshot:
        raise HostJournalInconsistentError("current record is not synchronized with host events")
    return record


def read_transition_events(
    head_path: Path,
    attempt_id: UUID,
) -> tuple[HostTransitionEvent, ...]:
    """Read immutable events adjacent to a previously verified active head."""

    return _read_event_stream(
        head_path.parent / "host-transition-events" / str(attempt_id)
    )


def _read_event_stream(directory: Path) -> tuple[HostTransitionEvent, ...]:
    if not directory.exists():
        return ()
    paths: list[tuple[int, Path]] = []
    for path in directory.glob("*.json"):
        try:
            sequence = int(path.stem)
        except ValueError as exc:
            raise HostJournalInconsistentError("host event filename is invalid") from exc
        paths.append((sequence, path))
    paths.sort(key=lambda item: item[0])
    return tuple(_load(HostTransitionEvent, path, _MAX_EVENT_BYTES) for _, path in paths)


def _validate_event_prefix(
    record: HostTransitionRecord,
    events: tuple[HostTransitionEvent, ...],
) -> None:
    if not events:
        raise ValueError("host transition has no initial event")
    if hashlib.sha256(_encode(events[0])).hexdigest() != record.initial_event_sha256:
        raise ValueError("initial host event fingerprint mismatch")
    if events[0].occurred_at != record.created_at:
        raise ValueError("initial host event timestamp differs from its record")
    previous: HostTransitionState | None = None
    for expected_seq, event in enumerate(events, start=1):
        if (
            event.attempt_id != record.attempt_id
            or event.operation is not record.operation
            or event.event_seq != expected_seq
            or event.from_state is not previous
        ):
            raise ValueError("host event prefix is not continuous")
        if previous is not None and event.to_state not in _ALLOWED_TRANSITIONS[previous]:
            raise ValueError("host event contains an invalid state transition")
        previous = event.to_state


def _load(model: type[ContractModel], path: Path, maximum_bytes: int):
    return model.model_validate_json(read_bounded_regular(path, maximum_bytes=maximum_bytes))


def _encode(model: ContractModel) -> bytes:
    return json.dumps(
        model.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("host journal timestamps must be timezone-aware")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return _aware(value).isoformat().replace("+00:00", "Z")
