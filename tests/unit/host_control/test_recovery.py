"""End-to-end state-machine tests for boot and retry recovery."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.host_control import (
    HostJournalPaths,
    HostOperation,
    HostTransitionJournal,
    HostTransitionState,
    load_host_recovery_policy,
)
from apps.host_control.ctl import retry_blocked_transition
from apps.host_control.failure_handler import degrade_unclassified_failure
from apps.host_control.recovery import (
    EXIT_BLOCKED,
    EXIT_OK,
    HostRecoveryService,
    replay_host_events,
)
from packages.domain import EventType
from packages.persistence import INVALID_QUESTION_NAMESPACE
from packages.persistence.models import (
    AuditEventRecord,
    HostEventReplayRecord,
    SystemConstantRecord,
)

NOW = datetime(2026, 9, 5, 12, tzinfo=UTC)
BOOT_ID = UUID("11111111-2222-4333-8444-555555555555")
ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "infra" / "systemd" / "host-recovery.defaults.toml"


def _policy():
    return load_host_recovery_policy(BASELINE, enforce_root_metadata=False)


def _seed_system_constant(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as db:
        db.add(
            SystemConstantRecord(
                key="invalid_question_uuid5_namespace",
                value=str(INVALID_QUESTION_NAMESPACE),
                created_at=NOW,
            )
        )


def test_successful_recovery_starts_target_replays_history_and_clears_head(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))
    starts: list[bool] = []
    service = HostRecoveryService(
        journal,
        session_factory,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: starts.append(True) is None,
        clock=lambda: NOW,
    )

    assert service.run_once() == EXIT_OK
    assert starts == [True]
    assert journal.active() is None
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(HostEventReplayRecord)) == 3
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(
                    AuditEventRecord.type
                    == EventType.HOST_TRANSITION_STATE_CHANGED.value
                )
            )
            == 3
        )


def test_database_outage_enters_retry_wait_then_resumes_same_attempt(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))

    def unavailable() -> Session:
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    first = HostRecoveryService(
        journal,
        unavailable,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: True,
        clock=lambda: NOW,
    )
    assert first.run_once() == EXIT_OK
    waiting = journal.active()
    assert waiting is not None
    assert waiting.state is HostTransitionState.RETRY_WAIT
    assert waiting.snapshot.next_attempt_at == NOW + timedelta(seconds=30)

    second = HostRecoveryService(
        journal,
        session_factory,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: True,
        clock=lambda: NOW + timedelta(seconds=30),
    )
    assert second.run_once() == EXIT_OK
    assert journal.active() is None


def test_reboot_forces_one_probe_even_when_wall_clock_deadline_is_not_due(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))

    def unavailable() -> Session:
        raise OperationalError("SELECT 1", {}, RuntimeError("database unavailable"))

    HostRecoveryService(
        journal,
        unavailable,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: True,
        clock=lambda: NOW,
    ).run_once()
    waiting = journal.active()
    assert waiting is not None and waiting.state is HostTransitionState.RETRY_WAIT

    new_boot = UUID("99999999-2222-4333-8444-555555555555")
    assert (
        HostRecoveryService(
            journal,
            session_factory,
            policy=_policy(),
            boot_id=new_boot,
            start_target=lambda: True,
            clock=lambda: NOW + timedelta(seconds=1),
        ).run_once()
        == EXIT_OK
    )
    assert journal.active() is None


def test_durable_admission_failure_is_blocked_and_never_dispatches(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))
    starts: list[bool] = []

    result = HostRecoveryService(
        journal,
        session_factory,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: starts.append(True) is None,
        clock=lambda: NOW,
    ).run_once()

    assert result == EXIT_BLOCKED
    active = journal.active()
    assert active is not None and active.state is HostTransitionState.RESUME_BLOCKED
    assert active.snapshot.error_class == "runtime_admission_rejected"
    assert starts == []


def test_host_event_replay_is_idempotent_before_local_cursor_is_advanced(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=BOOT_ID,
        policy=_policy(),
        actor="host-recovery",
        reason="boot",
        occurred_at=NOW,
    )
    ready = journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.READY_TO_START,
        actor="host-recovery",
        reason="admitted",
        occurred_at=NOW,
        snapshot_updates={
            "dispatch_id": UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            "dispatch_deadline": NOW + timedelta(seconds=30),
        },
    )
    resolved = journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.RESOLVED,
        actor="host-recovery",
        reason="started",
        occurred_at=NOW,
        snapshot_updates={"dispatch_id": None, "dispatch_deadline": None},
    )
    events = journal.events(record.attempt_id)

    assert replay_host_events(session_factory, resolved, events, replayed_at=NOW) == 3
    assert replay_host_events(session_factory, resolved, events, replayed_at=NOW) == 0
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(HostEventReplayRecord)) == 3
        assert db.scalar(select(func.count()).select_from(AuditEventRecord)) == 3
    assert ready.last_event_seq == 2


def test_retry_tick_does_not_create_a_transition_when_head_is_absent(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))

    result = HostRecoveryService(
        journal,
        session_factory,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: False,
        clock=lambda: NOW,
        create_if_absent=False,
    ).run_once()

    assert result == EXIT_OK
    assert journal.active() is None


def test_failure_handler_converts_only_unclassified_probe_to_slow_retry(
    tmp_path: Path,
) -> None:
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=BOOT_ID,
        policy=_policy(),
        actor="host-recovery",
        reason="boot",
        occurred_at=NOW,
        initial_snapshot_updates={
            "attempts_total": 1,
            "current_attempt_seq": 1,
            "last_probe_started_at": NOW,
        },
    )
    resets: list[bool] = []

    assert degrade_unclassified_failure(
        journal,
        occurred_at=NOW + timedelta(seconds=10),
        reset_failed=lambda: resets.append(True),
    )
    degraded = journal.active()
    assert degraded is not None and degraded.attempt_id == record.attempt_id
    assert degraded.state is HostTransitionState.RESUME_DEGRADED
    assert degraded.snapshot.next_attempt_at == NOW + timedelta(seconds=1810)
    assert resets == [True]


def test_operator_retry_reopens_only_a_blocked_transition(tmp_path: Path) -> None:
    journal = HostTransitionJournal(HostJournalPaths(tmp_path))
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=BOOT_ID,
        policy=_policy(),
        actor="host-recovery",
        reason="boot",
        occurred_at=NOW,
        initial_snapshot_updates={
            "attempts_total": 1,
            "current_attempt_seq": 1,
            "last_probe_started_at": NOW,
        },
    )
    journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.RESUME_BLOCKED,
        actor="host-recovery",
        reason="invalid pointer",
        error_class="runtime_admission_rejected",
        occurred_at=NOW,
        snapshot_updates={"last_probe_classified_at": NOW},
    )

    reopened = retry_blocked_transition(
        journal,
        actor="operator",
        reason="repaired pointer",
        occurred_at=NOW + timedelta(minutes=1),
    )

    assert reopened.state is HostTransitionState.CHECKING
    assert reopened.snapshot.attempts_total == 1
    assert reopened.snapshot.current_attempt_seq == 1
    assert reopened.snapshot.last_probe_started_at is None
    assert reopened.snapshot.last_probe_classified_at is None
