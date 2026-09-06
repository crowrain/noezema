"""Fsync-safe installer journal for root-owned host recovery policy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, model_validator
from sqlalchemy.orm import Session

from apps.host_control.atomic import atomic_write, durable_unlink, read_bounded_regular
from apps.host_control.journal import (
    HostJournalError,
    HostJournalInconsistentError,
    HostJournalPaths,
    HostPolicyChangeInProgressError,
    HostTransitionInProgressError,
)
from apps.host_control.lock import HostTransitionLock
from apps.host_control.policy import (
    HostPolicyInvalidError,
    MaterializedHostPolicy,
    load_host_recovery_policy,
)
from packages.domain import EventType
from packages.domain._base import ContractModel, NonEmptyText, Sha256Hex, ShortReason
from packages.persistence import append_global_audit
from packages.persistence.models import HostEventReplayRecord

_MAX_EVENT_BYTES = 64 * 1024
_MAX_HEAD_BYTES = 32 * 1024


class PolicyChangeState(StrEnum):
    PREPARED = "prepared"
    INCONSISTENT = "inconsistent"
    RESOLUTION_PREPARED = "resolution_prepared"
    COMMITTED = "committed"
    ABORTED = "aborted"
    RESOLVED = "resolved"

    @property
    def terminal(self) -> bool:
        return self in {
            PolicyChangeState.COMMITTED,
            PolicyChangeState.ABORTED,
            PolicyChangeState.RESOLVED,
        }


class PolicyChangeEvent(ContractModel):
    schema_version: Literal["host-policy-event/v1"] = "host-policy-event/v1"
    change_id: UUID
    event_seq: int = Field(ge=1)
    state: PolicyChangeState
    actor: ShortReason
    reason: NonEmptyText
    occurred_at: AwareDatetime
    old_canonical_sha256: Sha256Hex
    proposed_canonical_sha256: Sha256Hex
    old_source_file_sha256: Sha256Hex
    proposed_source_file_sha256: Sha256Hex
    observed_sha256: Sha256Hex | None = None
    effective_canonical_sha256: Sha256Hex | None = None
    replacement_canonical_sha256: Sha256Hex | None = None
    replacement_source_file_sha256: Sha256Hex | None = None

    @model_validator(mode="after")
    def validate_outcome(self) -> PolicyChangeEvent:
        if self.state is PolicyChangeState.PREPARED and self.event_seq != 1:
            raise ValueError("prepared must be the first policy event")
        if self.state.terminal != (self.effective_canonical_sha256 is not None):
            raise ValueError("terminal policy events require the effective canonical hash")
        if self.state is PolicyChangeState.INCONSISTENT and self.observed_sha256 is None:
            raise ValueError("inconsistent policy events require the observed hash")
        resolution = self.state in {
            PolicyChangeState.RESOLUTION_PREPARED,
            PolicyChangeState.RESOLVED,
        }
        if resolution != (
            self.replacement_canonical_sha256 is not None
            and self.replacement_source_file_sha256 is not None
        ):
            raise ValueError("policy resolution states require a complete replacement tuple")
        return self


class PolicyChangeHead(ContractModel):
    schema_version: Literal["host-policy-change-head/v1"] = "host-policy-change-head/v1"
    change_id: UUID
    prepared_event_sha256: Sha256Hex
    created_at: AwareDatetime


@dataclass(frozen=True, slots=True)
class PolicyInstallResult:
    changed: bool
    canonical_sha256: str
    change_id: UUID | None


class PolicyChangeJournal:
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

    def begin(
        self,
        *,
        old: MaterializedHostPolicy,
        proposed: MaterializedHostPolicy,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyChangeEvent:
        now = _aware(occurred_at)
        with self._lock.acquire():
            if _present(self.paths.head):
                raise HostTransitionInProgressError("host transition is active")
            active = self._active_locked()
            if active is not None:
                raise HostPolicyChangeInProgressError(str(active.change_id))
            event = PolicyChangeEvent(
                change_id=uuid4(),
                event_seq=1,
                state=PolicyChangeState.PREPARED,
                actor=actor,
                reason=reason,
                occurred_at=now,
                old_canonical_sha256=old.canonical_sha256,
                proposed_canonical_sha256=proposed.canonical_sha256,
                old_source_file_sha256=old.source_file_sha256,
                proposed_source_file_sha256=proposed.source_file_sha256,
                observed_sha256=None,
                effective_canonical_sha256=None,
                replacement_canonical_sha256=None,
                replacement_source_file_sha256=None,
            )
            encoded = _encode(event)
            self._write_event(event, encoded)
            self._failpoint("after_policy_prepared_event")
            self._write_head(event, hashlib.sha256(encoded).hexdigest())
            self._failpoint("after_policy_change_head")
            return event

    def active(self) -> PolicyChangeEvent | None:
        with self._lock.acquire():
            return self._active_locked()

    def terminal_streams(self) -> tuple[tuple[PolicyChangeEvent, ...], ...]:
        with self._lock.acquire():
            streams: list[tuple[PolicyChangeEvent, ...]] = []
            if not self.paths.policy_events.exists():
                return ()
            for directory in sorted(self.paths.policy_events.iterdir()):
                if not directory.is_dir():
                    raise HostJournalInconsistentError("invalid policy event entry")
                try:
                    change_id = UUID(directory.name)
                except ValueError as exc:
                    raise HostJournalInconsistentError(
                        "invalid policy event directory"
                    ) from exc
                events = self._events(change_id)
                if events and events[-1].state.terminal:
                    streams.append(events)
            return tuple(streams)

    def reconcile(
        self,
        *,
        observed_canonical_sha256: str | None,
        observed_source_file_sha256: str,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyChangeEvent | None:
        with self._lock.acquire():
            active = self._active_locked()
            if active is None:
                return None
            prepared = self._events(active.change_id)[0]
            if active.state is PolicyChangeState.RESOLUTION_PREPARED:
                return active
            if observed_canonical_sha256 == prepared.proposed_canonical_sha256:
                state = PolicyChangeState.COMMITTED
                effective = prepared.proposed_canonical_sha256
            elif observed_canonical_sha256 == prepared.old_canonical_sha256:
                state = PolicyChangeState.ABORTED
                effective = prepared.old_canonical_sha256
            else:
                state = PolicyChangeState.INCONSISTENT
                effective = None
            if active.state is state and not state.terminal:
                return active
            event = PolicyChangeEvent(
                change_id=prepared.change_id,
                event_seq=active.event_seq + 1,
                state=state,
                actor=actor,
                reason=reason,
                occurred_at=_aware(occurred_at),
                old_canonical_sha256=prepared.old_canonical_sha256,
                proposed_canonical_sha256=prepared.proposed_canonical_sha256,
                old_source_file_sha256=prepared.old_source_file_sha256,
                proposed_source_file_sha256=prepared.proposed_source_file_sha256,
                observed_sha256=observed_source_file_sha256,
                effective_canonical_sha256=effective,
                replacement_canonical_sha256=None,
                replacement_source_file_sha256=None,
            )
            self._write_event(event)
            self._failpoint("after_policy_terminal_event")
            if event.state.terminal:
                durable_unlink(self.paths.policy_change_head)
            return event

    def prepare_resolution(
        self,
        *,
        replacement: MaterializedHostPolicy,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyChangeEvent:
        with self._lock.acquire():
            active = self._active_locked()
            if active is None or active.state is not PolicyChangeState.INCONSISTENT:
                raise HostJournalError("policy change is not awaiting explicit resolution")
            prepared = self._events(active.change_id)[0]
            event = PolicyChangeEvent(
                change_id=active.change_id,
                event_seq=active.event_seq + 1,
                state=PolicyChangeState.RESOLUTION_PREPARED,
                actor=actor,
                reason=reason,
                occurred_at=_aware(occurred_at),
                old_canonical_sha256=prepared.old_canonical_sha256,
                proposed_canonical_sha256=prepared.proposed_canonical_sha256,
                old_source_file_sha256=prepared.old_source_file_sha256,
                proposed_source_file_sha256=prepared.proposed_source_file_sha256,
                observed_sha256=active.observed_sha256,
                effective_canonical_sha256=None,
                replacement_canonical_sha256=replacement.canonical_sha256,
                replacement_source_file_sha256=replacement.source_file_sha256,
            )
            self._write_event(event)
            return event

    def resolve_replacement(
        self,
        *,
        observed: MaterializedHostPolicy,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyChangeEvent:
        with self._lock.acquire():
            active = self._active_locked()
            if active is None or active.state is not PolicyChangeState.RESOLUTION_PREPARED:
                raise HostJournalError("policy replacement has not been prepared")
            if observed.canonical_sha256 != active.replacement_canonical_sha256:
                raise HostJournalError("effective policy does not match prepared replacement")
            prepared = self._events(active.change_id)[0]
            event = PolicyChangeEvent(
                change_id=active.change_id,
                event_seq=active.event_seq + 1,
                state=PolicyChangeState.RESOLVED,
                actor=actor,
                reason=reason,
                occurred_at=_aware(occurred_at),
                old_canonical_sha256=prepared.old_canonical_sha256,
                proposed_canonical_sha256=prepared.proposed_canonical_sha256,
                old_source_file_sha256=prepared.old_source_file_sha256,
                proposed_source_file_sha256=prepared.proposed_source_file_sha256,
                observed_sha256=observed.source_file_sha256,
                effective_canonical_sha256=observed.canonical_sha256,
                replacement_canonical_sha256=active.replacement_canonical_sha256,
                replacement_source_file_sha256=active.replacement_source_file_sha256,
            )
            self._write_event(event)
            durable_unlink(self.paths.policy_change_head)
            return event

    def _active_locked(self) -> PolicyChangeEvent | None:
        try:
            head = _load(PolicyChangeHead, self.paths.policy_change_head, _MAX_HEAD_BYTES)
        except FileNotFoundError:
            nonterminal: list[PolicyChangeEvent] = []
            if self.paths.policy_events.exists():
                for directory in sorted(self.paths.policy_events.iterdir()):
                    if not directory.is_dir():
                        raise HostJournalInconsistentError("invalid policy event entry")
                    try:
                        change_id = UUID(directory.name)
                    except ValueError as exc:
                        raise HostJournalInconsistentError(
                            "invalid policy event directory"
                        ) from exc
                    events = self._events(change_id)
                    if events and not events[-1].state.terminal:
                        nonterminal.append(events[-1])
            if len(nonterminal) > 1:
                raise HostJournalInconsistentError("multiple nonterminal policy changes exist")
            if not nonterminal:
                return None
            prepared = self._events(nonterminal[0].change_id)[0]
            self._write_head(prepared, hashlib.sha256(_encode(prepared)).hexdigest())
            return nonterminal[0]
        except (OSError, ValueError) as exc:
            raise HostJournalInconsistentError("policy change head is invalid") from exc

        events = self._events(head.change_id)
        if not events:
            raise HostJournalInconsistentError("policy change head has no event stream")
        prepared = events[0]
        if (
            prepared.state is not PolicyChangeState.PREPARED
            or hashlib.sha256(_encode(prepared)).hexdigest() != head.prepared_event_sha256
            or prepared.occurred_at != head.created_at
        ):
            raise HostJournalInconsistentError("policy change head identity mismatch")
        latest = events[-1]
        if latest.state.terminal:
            durable_unlink(self.paths.policy_change_head)
            return None
        return latest

    def _events(self, change_id: UUID) -> tuple[PolicyChangeEvent, ...]:
        directory = self.paths.policy_events / str(change_id)
        if not directory.exists():
            return ()
        paths: list[tuple[int, Path]] = []
        for path in directory.glob("*.json"):
            try:
                sequence = int(path.stem)
            except ValueError as exc:
                raise HostJournalInconsistentError("policy event filename is invalid") from exc
            paths.append((sequence, path))
        paths.sort(key=lambda item: item[0])
        events = tuple(_load(PolicyChangeEvent, path, _MAX_EVENT_BYTES) for _, path in paths)
        for sequence, event in enumerate(events, start=1):
            if event.change_id != change_id or event.event_seq != sequence:
                raise HostJournalInconsistentError("policy event sequence is not continuous")
            if sequence > 1 and event.state is PolicyChangeState.PREPARED:
                raise HostJournalInconsistentError("policy event stream repeats prepared")
            if sequence > 1 and (
                event.old_canonical_sha256 != events[0].old_canonical_sha256
                or event.proposed_canonical_sha256 != events[0].proposed_canonical_sha256
                or event.old_source_file_sha256 != events[0].old_source_file_sha256
                or event.proposed_source_file_sha256 != events[0].proposed_source_file_sha256
            ):
                raise HostJournalInconsistentError("policy event identity tuple changed")
        return events

    def _write_event(self, event: PolicyChangeEvent, encoded: bytes | None = None) -> None:
        path = self.paths.policy_events / str(event.change_id) / f"{event.event_seq:020d}.json"
        content = encoded or _encode(event)
        if path.exists():
            if read_bounded_regular(path, maximum_bytes=_MAX_EVENT_BYTES) != content:
                raise HostJournalInconsistentError("immutable policy event conflicts")
            return
        atomic_write(path, content)

    def _write_head(self, prepared: PolicyChangeEvent, event_sha256: str) -> None:
        atomic_write(
            self.paths.policy_change_head,
            _encode(
                PolicyChangeHead(
                    change_id=prepared.change_id,
                    prepared_event_sha256=event_sha256,
                    created_at=prepared.occurred_at,
                )
            ),
        )


class HostPolicyInstaller:
    def __init__(
        self,
        journal: PolicyChangeJournal,
        *,
        baseline_path: Path,
        override_path: Path,
        enforce_root_metadata: bool | None = None,
        failpoint: Callable[[str], None] | None = None,
    ) -> None:
        self._journal = journal
        self._baseline_path = baseline_path
        self._override_path = override_path
        self._enforce_root_metadata = enforce_root_metadata
        self._failpoint = failpoint or (lambda _name: None)

    def install(
        self,
        candidate_path: Path,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyInstallResult:
        proposed = load_host_recovery_policy(
            candidate_path,
            enforce_root_metadata=False,
        )
        old = load_host_recovery_policy(
            self._baseline_path,
            override_path=self._override_path,
            enforce_root_metadata=self._enforce_root_metadata,
        )
        active = self._journal.active()
        if active is not None:
            reconciled = self._journal.reconcile(
                observed_canonical_sha256=old.canonical_sha256,
                observed_source_file_sha256=old.source_file_sha256,
                actor=actor,
                reason="reconcile interrupted policy installation",
                occurred_at=occurred_at,
            )
            if reconciled is not None and not reconciled.state.terminal:
                raise HostJournalError("policy file matches neither prepared hash")
            old = load_host_recovery_policy(
                self._baseline_path,
                override_path=self._override_path,
                enforce_root_metadata=self._enforce_root_metadata,
            )
        if old.canonical_sha256 == proposed.canonical_sha256:
            return PolicyInstallResult(False, old.canonical_sha256, None)
        prepared = self._journal.begin(
            old=old,
            proposed=proposed,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        raw = read_bounded_regular(candidate_path, maximum_bytes=32 * 1024)
        atomic_write(self._override_path, raw, mode=0o644)
        self._failpoint("after_policy_override_replace")
        effective = load_host_recovery_policy(
            self._baseline_path,
            override_path=self._override_path,
            enforce_root_metadata=self._enforce_root_metadata,
        )
        terminal = self._journal.reconcile(
            observed_canonical_sha256=effective.canonical_sha256,
            observed_source_file_sha256=effective.source_file_sha256,
            actor=actor,
            reason="policy installation committed",
            occurred_at=occurred_at,
        )
        if terminal is None or terminal.state is not PolicyChangeState.COMMITTED:
            raise HostJournalError("policy installation did not reach committed")
        return PolicyInstallResult(True, effective.canonical_sha256, prepared.change_id)

    def resolve_with_file(
        self,
        replacement_path: Path,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyInstallResult:
        replacement = load_host_recovery_policy(
            replacement_path,
            enforce_root_metadata=False,
        )
        active = self._journal.active()
        if active is None:
            raise HostJournalError("there is no active policy change to resolve")
        if active.state is PolicyChangeState.PREPARED:
            observed, observed_sha256 = observe_effective_policy(
                self._baseline_path,
                self._override_path,
                enforce_root_metadata=self._enforce_root_metadata,
            )
            active = self._journal.reconcile(
                observed_canonical_sha256=(
                    observed.canonical_sha256 if observed is not None else None
                ),
                observed_source_file_sha256=observed_sha256,
                actor=actor,
                reason="classify policy before explicit resolution",
                occurred_at=occurred_at,
            )
            if active is None or active.state.terminal:
                raise HostJournalError("policy change already resolved to old or proposed hash")
        if active.state is PolicyChangeState.RESOLUTION_PREPARED:
            if active.replacement_canonical_sha256 != replacement.canonical_sha256:
                raise HostJournalError("replacement differs from the prepared resolution")
            try:
                current = load_host_recovery_policy(
                    self._baseline_path,
                    override_path=self._override_path,
                    enforce_root_metadata=self._enforce_root_metadata,
                )
            except ValueError:
                current = None
            if current is not None and current.canonical_sha256 == replacement.canonical_sha256:
                terminal = self._journal.resolve_replacement(
                    observed=current,
                    actor=actor,
                    reason=reason,
                    occurred_at=occurred_at,
                )
                return PolicyInstallResult(True, current.canonical_sha256, terminal.change_id)
        elif active.state is PolicyChangeState.INCONSISTENT:
            self._journal.prepare_resolution(
                replacement=replacement,
                actor=actor,
                reason=reason,
                occurred_at=occurred_at,
            )
        else:
            raise HostJournalError("policy change is not in a resolvable state")
        raw = read_bounded_regular(replacement_path, maximum_bytes=32 * 1024)
        atomic_write(self._override_path, raw, mode=0o644)
        self._failpoint("after_policy_resolution_replace")
        effective = load_host_recovery_policy(
            self._baseline_path,
            override_path=self._override_path,
            enforce_root_metadata=self._enforce_root_metadata,
        )
        terminal = self._journal.resolve_replacement(
            observed=effective,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        return PolicyInstallResult(True, effective.canonical_sha256, terminal.change_id)

    def resolve_accept_current(
        self,
        *,
        actor: str,
        reason: str,
        occurred_at: datetime,
    ) -> PolicyInstallResult:
        current = load_host_recovery_policy(
            self._baseline_path,
            override_path=self._override_path,
            enforce_root_metadata=self._enforce_root_metadata,
        )
        active = self._journal.active()
        if active is None:
            raise HostJournalError("there is no active policy change to resolve")
        if active.state in {
            PolicyChangeState.PREPARED,
            PolicyChangeState.INCONSISTENT,
        }:
            classified = self._journal.reconcile(
                observed_canonical_sha256=current.canonical_sha256,
                observed_source_file_sha256=current.source_file_sha256,
                actor=actor,
                reason="classify current policy before accepting it",
                occurred_at=occurred_at,
            )
            if classified is None:
                raise HostJournalError("policy change disappeared during resolution")
            if classified.state.terminal:
                return PolicyInstallResult(
                    classified.state is PolicyChangeState.COMMITTED,
                    current.canonical_sha256,
                    classified.change_id,
                )
            active = classified
        if active.state is PolicyChangeState.INCONSISTENT:
            active = self._journal.prepare_resolution(
                replacement=current,
                actor=actor,
                reason=reason,
                occurred_at=occurred_at,
            )
        if active.state is not PolicyChangeState.RESOLUTION_PREPARED:
            raise HostJournalError("policy change is not in a resolvable state")
        if active.replacement_canonical_sha256 != current.canonical_sha256:
            raise HostJournalError("current policy differs from the prepared resolution")
        terminal = self._journal.resolve_replacement(
            observed=current,
            actor=actor,
            reason=reason,
            occurred_at=occurred_at,
        )
        return PolicyInstallResult(True, current.canonical_sha256, terminal.change_id)


def observe_effective_policy(
    baseline_path: Path,
    override_path: Path,
    *,
    enforce_root_metadata: bool | None = None,
) -> tuple[MaterializedHostPolicy | None, str]:
    """Return a valid materialization or at least a stable hash of the bad file."""

    try:
        policy = load_host_recovery_policy(
            baseline_path,
            override_path=override_path,
            enforce_root_metadata=enforce_root_metadata,
        )
    except HostPolicyInvalidError:
        selected = override_path if _present(override_path) else baseline_path
        try:
            raw = read_bounded_regular(selected, maximum_bytes=32 * 1024)
        except (OSError, ValueError):
            raw = f"unreadable:{selected}".encode()
        return None, hashlib.sha256(raw).hexdigest()
    return policy, policy.source_file_sha256


def replay_terminal_policy_events(
    session_factory: Callable[[], Session],
    journal: PolicyChangeJournal,
    *,
    replayed_at: datetime,
    maximum_events: int = 100,
) -> int:
    """Replay terminal policy streams into the globally ordered DB audit."""

    if not 1 <= maximum_events <= 10_000:
        raise ValueError("policy replay batch size is out of range")
    candidates = [event for stream in journal.terminal_streams() for event in stream]
    replayed = 0
    with session_factory.begin() as db:  # type: ignore[attr-defined]
        for event in candidates:
            if db.get(HostEventReplayRecord, (event.change_id, event.event_seq)) is not None:
                continue
            if replayed >= maximum_events:
                break
            appended = append_global_audit(
                db,
                type=EventType.HOST_POLICY_CHANGED,
                occurred_at=event.occurred_at,
                actor=event.actor,
                public_summary=f"Host policy change: {event.state.value}",
                topic="audit.host_policy_change.v1",
                payload={
                    "change_id": str(event.change_id),
                    "event_seq": event.event_seq,
                    "state": event.state.value,
                    "reason": event.reason,
                    "old_canonical_sha256": event.old_canonical_sha256,
                    "proposed_canonical_sha256": event.proposed_canonical_sha256,
                    "observed_sha256": event.observed_sha256,
                    "effective_canonical_sha256": event.effective_canonical_sha256,
                    "replacement_canonical_sha256": event.replacement_canonical_sha256,
                },
            )
            db.add(
                HostEventReplayRecord(
                    attempt_id=event.change_id,
                    event_seq=event.event_seq,
                    audit_event_id=appended.audit_event.id.root,
                    replayed_at=_aware(replayed_at),
                )
            )
            replayed += 1
    return replayed


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
        raise ValueError("policy change timestamp must be timezone-aware")
    return value.astimezone(UTC)
