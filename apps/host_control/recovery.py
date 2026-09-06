"""Crash-idempotent host automaton that admits and starts the cognitive target."""

from __future__ import annotations

import logging
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from apps.host_control.journal import (
    HostJournalError,
    HostOperation,
    HostTransitionEvent,
    HostTransitionJournal,
    HostTransitionRecord,
    HostTransitionState,
)
from apps.host_control.policy import HostRecoveryPolicy, MaterializedHostPolicy
from apps.host_control.policy_change import (
    PolicyChangeJournal,
    replay_terminal_policy_events,
)
from apps.runtime.admission import (
    RuntimeAdmissionRejectedError,
    RuntimeAdmissionTemporaryError,
    check_runtime_database_admission,
)
from packages.domain import EventType
from packages.persistence import append_global_audit
from packages.persistence.models import (
    ConfigSnapshotRecord,
    HostEventReplayRecord,
    RuntimeConfigHeadRecord,
)

_LOGGER = logging.getLogger(__name__)
_REPLAY_BATCH_SIZE = 100

EXIT_OK = 0
EXIT_TEMPORARY = 75
EXIT_BLOCKED = 78


class HostRecoveryService:
    """Advance one durable recovery attempt as far as current state permits."""

    def __init__(
        self,
        journal: HostTransitionJournal,
        session_factory: Callable[[], Session],
        *,
        policy: MaterializedHostPolicy,
        boot_id: UUID,
        start_target: Callable[[], bool],
        clock: Callable[[], datetime] | None = None,
        create_if_absent: bool = True,
    ) -> None:
        self._journal = journal
        self._session_factory = session_factory
        self._policy = policy
        self._boot_id = boot_id
        self._start_target = start_target
        self._clock = clock or (lambda: datetime.now(UTC))
        self._create_if_absent = create_if_absent

    def run_once(self) -> int:
        now = _aware(self._clock())
        record = self._journal.active()
        created = False
        if record is None:
            if not self._create_if_absent:
                return EXIT_OK
            record = self._journal.create(
                operation=HostOperation.RUNTIME_START,
                boot_id=self._boot_id,
                policy=self._policy,
                actor="host-recovery",
                reason="runtime start recovery opened",
                occurred_at=now,
                initial_snapshot_updates={
                    "attempts_total": 1,
                    "current_attempt_seq": 1,
                    "last_probe_boot_id": self._boot_id,
                    "last_probe_started_at": now,
                },
            )
            created = True

        if record.state is HostTransitionState.RESOLVED:
            return self._finish_replay(record, now)
        if record.state is HostTransitionState.RESUME_BLOCKED:
            return EXIT_BLOCKED
        if record.state in {
            HostTransitionState.RETRY_WAIT,
            HostTransitionState.RESUME_DEGRADED,
        }:
            if (
                record.snapshot.last_probe_boot_id == self._boot_id
                and record.snapshot.next_attempt_at is not None
                and now < record.snapshot.next_attempt_at
            ):
                return EXIT_OK
            record = self._begin_next_probe(record, now)
        elif record.state is HostTransitionState.READY_TO_START:
            record = self._begin_next_probe(record, now)
        elif record.state is HostTransitionState.CHECKING and not created:
            record = self._begin_next_probe(record, now)

        return self._classify(record, now)

    def _begin_next_probe(
        self,
        record: HostTransitionRecord,
        now: datetime,
    ) -> HostTransitionRecord:
        return self._journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.CHECKING,
            actor="host-recovery",
            reason="scheduled recovery probe started",
            occurred_at=now,
            snapshot_updates={
                "attempts_total": record.snapshot.attempts_total + 1,
                "current_attempt_seq": record.snapshot.current_attempt_seq + 1,
                "last_probe_boot_id": self._boot_id,
                "last_probe_started_at": now,
                "last_probe_classified_at": None,
                "next_attempt_at": None,
                "dispatch_id": None,
                "dispatch_deadline": None,
            },
        )

    def _classify(self, record: HostTransitionRecord, now: datetime) -> int:
        try:
            self._check_operation_outcome(record)
            check_runtime_database_admission(self._session_factory)
            self._replay_policy_history(now)
        except RuntimeAdmissionTemporaryError:
            return self._defer(record, now)
        except RuntimeAdmissionRejectedError as exc:
            blocked = self._journal.transition(
                record.attempt_id,
                to_state=HostTransitionState.RESUME_BLOCKED,
                actor="host-recovery",
                reason=str(exc),
                occurred_at=now,
                error_class="runtime_admission_rejected",
                snapshot_updates={
                    "last_probe_classified_at": now,
                    "last_probe_boot_id": self._boot_id,
                    "next_attempt_at": None,
                },
            )
            self._replay_current_prefix(blocked, now)
            return EXIT_BLOCKED

        dispatch_id = uuid4()
        ready = self._journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.READY_TO_START,
            actor="host-recovery",
            reason="durable runtime admission accepted",
            occurred_at=now,
            snapshot_updates={
                "consecutive_unclassified_failures": 0,
                "backoff_step": 0,
                "last_probe_classified_at": now,
                "last_probe_boot_id": self._boot_id,
                "next_attempt_at": None,
                "dispatch_id": dispatch_id,
                "dispatch_deadline": now + timedelta(seconds=30),
            },
        )
        return self._dispatch(ready, now)

    def _replay_policy_history(self, now: datetime) -> None:
        try:
            replay_terminal_policy_events(
                self._session_factory,
                PolicyChangeJournal(self._journal.paths),
                replayed_at=now,
            )
        except SQLAlchemyError as exc:
            raise RuntimeAdmissionTemporaryError(
                "operational database is unavailable"
            ) from exc
        except HostJournalError as exc:
            raise RuntimeAdmissionRejectedError(
                "host policy history is inconsistent"
            ) from exc

    def _check_operation_outcome(self, record: HostTransitionRecord) -> None:
        if record.operation is not HostOperation.OFFLINE_RULES:
            return
        if (
            record.candidate_snapshot_id is None
            or record.base_snapshot_id is None
            or record.observed_active_snapshot_id != record.base_snapshot_id
        ):
            raise RuntimeAdmissionRejectedError("offline transition config tuple is incomplete")
        try:
            with self._session_factory() as db:
                head = db.get(RuntimeConfigHeadRecord, "global")
                candidate = db.get(ConfigSnapshotRecord, record.candidate_snapshot_id)
                base = db.get(ConfigSnapshotRecord, record.base_snapshot_id)
        except SQLAlchemyError as exc:
            raise RuntimeAdmissionTemporaryError(
                "operational database is unavailable"
            ) from exc
        if head is None or base is None:
            raise RuntimeAdmissionRejectedError("offline transition base tuple is missing")
        active_id = head.active_config_snapshot_id
        if record.candidate_snapshot_id == record.base_snapshot_id:
            if active_id != record.base_snapshot_id or base.activation_state != "active":
                raise RuntimeAdmissionRejectedError("offline no-op pointer tuple is inconsistent")
            return
        pre_publish = active_id == record.base_snapshot_id and base.activation_state == "active"
        published = (
            active_id == record.candidate_snapshot_id
            and candidate is not None
            and candidate.activation_state == "active"
            and base.activation_state == "superseded"
        )
        if not (pre_publish or published):
            raise RuntimeAdmissionRejectedError("offline activation pointer tuple is inconsistent")

    def _defer(self, record: HostTransitionRecord, now: datetime) -> int:
        policy = _record_policy(record)
        delay_ns = policy.retry_delay_ns(record.snapshot.backoff_step)
        elapsed_ns = int((now - record.created_at).total_seconds() * 1_000_000_000)
        if elapsed_ns >= policy.resume_retry_escalate_after_ns:
            _LOGGER.warning("runtime recovery is still waiting for the operational database")
        self._journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.RETRY_WAIT,
            actor="host-recovery",
            reason="operational database is unavailable",
            occurred_at=now,
            error_class="database_unavailable",
            snapshot_updates={
                "consecutive_unclassified_failures": (
                    record.snapshot.consecutive_unclassified_failures
                ),
                "backoff_step": record.snapshot.backoff_step + 1,
                "last_probe_classified_at": now,
                "last_probe_boot_id": self._boot_id,
                "next_attempt_at": now + timedelta(microseconds=delay_ns / 1_000),
                "dispatch_id": None,
                "dispatch_deadline": None,
            },
        )
        return EXIT_OK

    def _dispatch(self, record: HostTransitionRecord, now: datetime) -> int:
        try:
            started = self._start_target()
        except (OSError, subprocess.SubprocessError):
            _LOGGER.exception("runtime target dispatch failed")
            started = False
        if not started:
            policy = _record_policy(record)
            delay_ns = policy.retry_delay_ns(record.snapshot.backoff_step)
            self._journal.transition(
                record.attempt_id,
                to_state=HostTransitionState.RESUME_DEGRADED,
                actor="host-recovery",
                reason="runtime target dispatch was not confirmed",
                occurred_at=now,
                error_class="runtime_dispatch_failed",
                snapshot_updates={
                    "backoff_step": record.snapshot.backoff_step + 1,
                    "next_attempt_at": now + timedelta(microseconds=delay_ns / 1_000),
                    "dispatch_id": None,
                    "dispatch_deadline": None,
                },
            )
            return EXIT_TEMPORARY

        resolved = self._journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.RESOLVED,
            actor="host-recovery",
            reason="runtime target start confirmed",
            occurred_at=now,
            snapshot_updates={
                "next_attempt_at": None,
                "dispatch_id": None,
                "dispatch_deadline": None,
            },
        )
        return self._finish_replay(resolved, now)

    def _finish_replay(self, record: HostTransitionRecord, now: datetime) -> int:
        events = self._journal.events(record.attempt_id)
        pending = events[
            record.replayed_through_seq : record.replayed_through_seq + _REPLAY_BATCH_SIZE
        ]
        if not pending and record.replayed_through_seq != record.last_event_seq:
            raise RuntimeError("host replay cursor does not identify an immutable event prefix")
        try:
            replay_host_events(self._session_factory, record, pending, replayed_at=now)
        except SQLAlchemyError:
            _LOGGER.warning("host history replay deferred because the database is unavailable")
            return EXIT_OK
        through_seq = pending[-1].event_seq if pending else record.replayed_through_seq
        self._journal.mark_replayed(
            record.attempt_id,
            through_seq=through_seq,
            replayed_at=now,
        )
        return EXIT_OK

    def _replay_current_prefix(self, record: HostTransitionRecord, now: datetime) -> None:
        events = self._journal.events(record.attempt_id)
        pending = events[record.replayed_through_seq :]
        try:
            replay_host_events(self._session_factory, record, pending, replayed_at=now)
        except SQLAlchemyError:
            return
        self._journal.mark_replayed(
            record.attempt_id,
            through_seq=record.last_event_seq,
            replayed_at=now,
        )


def replay_host_events(
    session_factory: Callable[[], Session],
    record: HostTransitionRecord,
    events: tuple[HostTransitionEvent, ...],
    *,
    replayed_at: datetime,
) -> int:
    """Append the unreplayed immutable prefix atomically and without duplicates."""

    replayed = 0
    with session_factory.begin() as db:  # type: ignore[attr-defined]
        for event in events:
            existing = db.get(HostEventReplayRecord, (record.attempt_id, event.event_seq))
            if existing is not None:
                continue
            appended = append_global_audit(
                db,
                type=EventType.HOST_TRANSITION_STATE_CHANGED,
                occurred_at=event.occurred_at,
                actor=event.actor,
                public_summary=f"Host transition: {event.to_state.value}",
                topic="audit.host_transition.v1",
                payload={
                    "attempt_id": str(record.attempt_id),
                    "operation": record.operation.value,
                    "event_seq": event.event_seq,
                    "from_state": (
                        event.from_state.value if event.from_state is not None else None
                    ),
                    "to_state": event.to_state.value,
                    "reason": event.reason,
                    "error_class": event.error_class,
                    "policy_sha256": record.policy.canonical_sha256,
                },
            )
            db.add(
                HostEventReplayRecord(
                    attempt_id=record.attempt_id,
                    event_seq=event.event_seq,
                    audit_event_id=appended.audit_event.id.root,
                    replayed_at=_aware(replayed_at),
                )
            )
            replayed += 1
    return replayed


def start_runtime_target() -> bool:
    """Ask systemd to start the full writer target and verify its active state."""

    started = subprocess.run(
        ("systemctl", "start", "noezema-runtime.target"),
        check=False,
        timeout=15,
    )
    if started.returncode != 0:
        return False
    verified = subprocess.run(
        ("systemctl", "is-active", "--quiet", "noezema-runtime.target"),
        check=False,
        timeout=3,
    )
    return verified.returncode == 0


def read_boot_id(path: Path = Path("/proc/sys/kernel/random/boot_id")) -> UUID:
    return UUID(path.read_text(encoding="ascii").strip())


def _record_policy(record: HostTransitionRecord) -> HostRecoveryPolicy:
    return HostRecoveryPolicy.model_validate(record.policy.values)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("host recovery clock must be timezone-aware")
    return value.astimezone(UTC)
