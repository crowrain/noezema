"""Durable autonomous scheduling, wake and failure-policy tests."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import (
    AutonomousSupervisor,
    SupervisorLeaseLostError,
    SupervisorPolicy,
    SupervisorSkipReason,
    SupervisorTickStatus,
    SupervisorTrigger,
    WakeSkipped,
    WakeSkipReason,
)
from packages.domain import (
    EventType,
    InboxIdempotencyKey,
    NodeState,
    OperatorCommandDraft,
    OperatorCommandState,
    OperatorCommandType,
)
from packages.persistence import submit_operator_command
from packages.persistence.models import (
    AuditEventRecord,
    OperatorCommandRecord,
    RuntimeConfigHeadRecord,
    RuntimeControlRecord,
)

NOW = datetime(2026, 8, 16, 10, 0, tzinfo=UTC)


@dataclass
class _Clock:
    now: datetime = NOW

    def __call__(self) -> datetime:
        return self.now


class _SkippedRunner:
    def __init__(self, reason: WakeSkipReason = WakeSkipReason.NO_ELIGIBLE_QUESTION) -> None:
        self.reason = reason
        self.calls = 0

    def run_once(self, *, max_steps: int | None = None) -> WakeSkipped:
        assert max_steps == 1
        self.calls += 1
        return WakeSkipped(reason=self.reason)


class _FailingRunner:
    def __init__(self) -> None:
        self.calls = 0

    def run_once(self, *, max_steps: int | None = None) -> WakeSkipped:
        assert max_steps == 1
        self.calls += 1
        raise ConnectionError("backend details must not enter the public result")


def _supervisor(
    session_factory: sessionmaker[Session],
    *,
    runner: Any,
    clock: _Clock,
    owner: str = "supervisor-a/incarnation-1",
    policy: SupervisorPolicy | None = None,
) -> AutonomousSupervisor:
    return AutonomousSupervisor(
        session_factory=session_factory,
        runner=runner,
        owner=owner,
        policy=policy,
        clock=clock,
        sleeper=lambda _seconds: None,
    )


def _wake_now() -> OperatorCommandDraft:
    return OperatorCommandDraft.new(
        idempotency_key=InboxIdempotencyKey.new(),
        actor_id="owner",
        type=OperatorCommandType.WAKE_NOW,
        reason="run one autonomous cycle",
        created_at=NOW,
    )


def test_policy_validates_bounds_and_caps_exponential_backoff() -> None:
    policy = SupervisorPolicy(
        failure_backoff_initial_seconds=10,
        failure_backoff_multiplier=3,
        failure_backoff_max_seconds=50,
    )

    assert [policy.failure_delay(failures) for failures in range(1, 5)] == [10, 30, 50, 50]
    with pytest.raises(ValueError, match="positive"):
        policy.failure_delay(0)
    with pytest.raises(ValueError, match="schedule interval"):
        SupervisorPolicy(schedule_interval_seconds=0)


def test_scheduled_tick_is_audited_and_advances_cadence(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    runner = _SkippedRunner()
    supervisor = _supervisor(session_factory, runner=runner, clock=clock)

    result = supervisor.tick()

    assert result.status is SupervisorTickStatus.SKIPPED
    assert result.trigger is SupervisorTrigger.SCHEDULED
    assert result.skip_reason is SupervisorSkipReason.NO_ELIGIBLE_QUESTION
    assert result.next_scheduled_at == NOW + timedelta(hours=1)
    assert runner.calls == 1
    with session_factory() as db:
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None
        assert runtime.scheduler_lease_owner is None
        assert runtime.scheduler_fence == 1
        assert runtime.scheduler_consecutive_failures == 0
        types = tuple(db.scalars(select(AuditEventRecord.type).order_by(AuditEventRecord.sequence)))
    assert types == (
        EventType.SCHEDULER_WAKE_STARTED.value,
        EventType.SCHEDULER_WAKE_FINISHED.value,
    )


def test_wake_now_bypasses_cadence_and_is_consumed_exactly_once(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    first_runner = _SkippedRunner()
    with session_factory.begin() as db:
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None
        runtime.scheduler_next_scheduled_at = NOW + timedelta(days=1)
        command = submit_operator_command(db, _wake_now())

    first = _supervisor(session_factory, runner=first_runner, clock=clock).tick()

    assert first.trigger is SupervisorTrigger.WAKE_NOW
    assert first.handled_wake_generation == 1
    assert first_runner.calls == 1
    with session_factory() as db:
        stored = db.get(OperatorCommandRecord, command.id.root)
        runtime = db.get(RuntimeControlRecord, "global")
        assert stored is not None and stored.state == OperatorCommandState.COMPLETED.value
        assert runtime is not None
        assert runtime.wake_generation == runtime.scheduler_last_handled_wake_generation == 1

    restarted_runner = _SkippedRunner()
    restarted = _supervisor(
        session_factory,
        runner=restarted_runner,
        clock=clock,
        owner="supervisor-b/incarnation-1",
    ).tick()

    assert restarted.status is SupervisorTickStatus.IDLE
    assert restarted_runner.calls == 0


def test_live_lease_blocks_a_second_supervisor_and_expired_owner_is_fenced(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    old = _supervisor(
        session_factory,
        runner=_SkippedRunner(),
        clock=clock,
        owner="old/incarnation",
        policy=SupervisorPolicy(lease_ttl_seconds=10),
    )
    old_claim = old._claim(NOW)  # noqa: SLF001 - exercise the durable fencing boundary
    assert not hasattr(old_claim, "status")

    new_runner = _SkippedRunner()
    current = _supervisor(
        session_factory,
        runner=new_runner,
        clock=clock,
        owner="new/incarnation",
        policy=SupervisorPolicy(lease_ttl_seconds=10),
    )
    busy = current.tick()
    assert busy.skip_reason is SupervisorSkipReason.SCHEDULER_BUSY
    assert new_runner.calls == 0

    clock.now = NOW + timedelta(seconds=11)
    recovered = current.tick()
    assert recovered.trigger is SupervisorTrigger.SCHEDULED
    assert new_runner.calls == 1
    with pytest.raises(SupervisorLeaseLostError, match="fenced"):
        old._finish_skipped(  # type: ignore[arg-type]  # noqa: SLF001
            old_claim,
            WakeSkipReason.NO_ELIGIBLE_QUESTION,
        )


def test_failure_backoff_survives_restart_and_auto_pauses(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    policy = SupervisorPolicy(
        failure_backoff_initial_seconds=10,
        failure_backoff_multiplier=2,
        failure_backoff_max_seconds=60,
        pause_after_consecutive_failures=2,
    )
    first_runner = _FailingRunner()
    first = _supervisor(
        session_factory,
        runner=first_runner,
        clock=clock,
        policy=policy,
    ).tick()

    assert first.status is SupervisorTickStatus.ERROR
    assert first.error_class == "ConnectionError"
    assert first.backoff_until == NOW + timedelta(seconds=10)
    assert first.consecutive_failures == 1

    restarted_runner = _FailingRunner()
    restarted = _supervisor(
        session_factory,
        runner=restarted_runner,
        clock=clock,
        owner="supervisor-b/incarnation-1",
        policy=policy,
    )
    blocked = restarted.tick()
    assert blocked.skip_reason is SupervisorSkipReason.BACKOFF
    assert restarted_runner.calls == 0

    clock.now = NOW + timedelta(seconds=10)
    second = restarted.tick()

    assert second.status is SupervisorTickStatus.ERROR
    assert second.backoff_until == clock.now + timedelta(seconds=20)
    assert second.consecutive_failures == 2
    with session_factory() as db:
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None
        assert runtime.node_state == NodeState.PAUSED.value


def test_activation_blocks_new_session_without_invoking_runner(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    runner = _SkippedRunner()
    with session_factory.begin() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        head.activating_config_snapshot_id = head.active_config_snapshot_id

    result = _supervisor(session_factory, runner=runner, clock=clock).tick()

    assert result.status is SupervisorTickStatus.SKIPPED
    assert result.skip_reason is SupervisorSkipReason.CONFIG_ACTIVATION_IN_PROGRESS
    assert result.next_scheduled_at == NOW + timedelta(hours=1)
    assert runner.calls == 0


def test_unavailable_host_resources_block_only_new_session_admission(
    session_factory: sessionmaker[Session],
) -> None:
    clock = _Clock()
    runner = _SkippedRunner()
    supervisor = AutonomousSupervisor(
        session_factory=session_factory,
        runner=runner,
        owner="supervisor/incarnation-1",
        clock=clock,
        resources_ready=lambda: False,
    )

    result = supervisor.tick()

    assert result.status is SupervisorTickStatus.SKIPPED
    assert result.skip_reason is SupervisorSkipReason.RESOURCE_UNAVAILABLE
    assert runner.calls == 0
