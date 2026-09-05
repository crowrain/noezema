"""Crash-resumable single-node scheduling around the autonomous session runner."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator.commands import dispatch_next_operator_command
from apps.orchestrator.models import (
    SessionRunResult,
    SupervisorSkipReason,
    SupervisorTickResult,
    SupervisorTickStatus,
    SupervisorTrigger,
    WakeSkipped,
    WakeSkipReason,
)
from apps.orchestrator.runner import SessionStepLimitExceededError
from packages.domain import EventType, NodeState, SessionId, SessionState
from packages.persistence import append_global_audit
from packages.persistence.models import (
    CommitAttemptRecord,
    RuntimeConfigHeadRecord,
    RuntimeControlRecord,
    SessionRecord,
)

_LOGGER = logging.getLogger(__name__)
_UNRESOLVED_ATTEMPT_STATES = ("prepared", "reconciling")
_TERMINAL_SESSION_STATES = tuple(state.value for state in SessionState if state.is_terminal)


class SessionRunner(Protocol):
    def run_once(
        self,
        *,
        max_steps: int | None = None,
    ) -> SessionRunResult | WakeSkipped: ...


class SupervisorLeaseLostError(RuntimeError):
    """Another supervisor fenced an expired dispatch before it could finish."""


@dataclass(frozen=True, slots=True)
class SupervisorPolicy:
    """Trusted single-node scheduling and failure policy."""

    schedule_interval_seconds: int = 60 * 60
    poll_interval_seconds: float = 5.0
    lease_ttl_seconds: int = 5 * 60
    failure_backoff_initial_seconds: int = 60
    failure_backoff_multiplier: int = 2
    failure_backoff_max_seconds: int = 60 * 60
    pause_after_consecutive_failures: int = 5
    max_session_steps: int = 256
    command_batch_limit: int = 128

    def __post_init__(self) -> None:
        if not 1 <= self.schedule_interval_seconds <= 31 * 24 * 60 * 60:
            raise ValueError("schedule interval must be between 1 second and 31 days")
        if not 0.05 <= self.poll_interval_seconds <= 60:
            raise ValueError("poll interval must be between 0.05 and 60 seconds")
        if not 1 <= self.lease_ttl_seconds <= 24 * 60 * 60:
            raise ValueError("scheduler lease TTL must be between 1 second and 1 day")
        if not 1 <= self.failure_backoff_initial_seconds <= 24 * 60 * 60:
            raise ValueError("initial failure backoff must be between 1 second and 1 day")
        if not 1 <= self.failure_backoff_multiplier <= 100:
            raise ValueError("failure backoff multiplier must be between 1 and 100")
        if not (
            self.failure_backoff_initial_seconds
            <= self.failure_backoff_max_seconds
            <= 31 * 24 * 60 * 60
        ):
            raise ValueError("maximum failure backoff must cover the initial backoff")
        if not 1 <= self.pause_after_consecutive_failures <= 1_000:
            raise ValueError("pause threshold must be between 1 and 1000 failures")
        if not 1 <= self.max_session_steps <= 10_000:
            raise ValueError("session step limit must be between 1 and 10000")
        if not 1 <= self.command_batch_limit <= 10_000:
            raise ValueError("command batch limit must be between 1 and 10000")

    def failure_delay(self, failures: int) -> int:
        if failures < 1:
            raise ValueError("failure count must be positive")
        delay = self.failure_backoff_initial_seconds * (
            self.failure_backoff_multiplier ** (failures - 1)
        )
        return min(delay, self.failure_backoff_max_seconds)


@dataclass(frozen=True, slots=True)
class _DispatchClaim:
    owner: str
    fence: int
    trigger: SupervisorTrigger
    wake_generation: int
    session_id: SessionId | None


class AutonomousSupervisor:
    """Run one durable scheduler dispatch at a time and recover after process loss."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        runner: SessionRunner,
        owner: str,
        policy: SupervisorPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
        sleeper: Callable[[float], None] = time.sleep,
        resources_ready: Callable[[], bool] | None = None,
    ) -> None:
        if not owner or len(owner) > 128:
            raise ValueError("supervisor owner must contain between 1 and 128 characters")
        self._session_factory = session_factory
        self._runner = runner
        self._owner = owner
        self._policy = policy or SupervisorPolicy()
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleeper
        self._resources_ready = resources_ready or (lambda: True)

    def tick(self) -> SupervisorTickResult:
        """Process controls, claim one due dispatch and drive it to a durable outcome."""

        observed_at = _clock_time(self._clock())
        try:
            self._dispatch_pending_commands(observed_at)
            claimed = self._claim(observed_at)
        except Exception as exc:
            _LOGGER.exception("supervisor admission failed")
            return self._record_unclaimed_error(exc, observed_at)
        if isinstance(claimed, SupervisorTickResult):
            return claimed

        try:
            outcome = self._run_claimed(claimed)
        except Exception as exc:
            _LOGGER.exception("supervisor dispatch failed")
            return self._finish_error(claimed, exc)
        if isinstance(outcome, WakeSkipped):
            return self._finish_skipped(claimed, outcome.reason)
        return self._finish_session(claimed, outcome)

    def run_forever(self, *, stop_requested: Callable[[], bool]) -> None:
        """Poll until the host-owned stop predicate becomes true."""

        while not stop_requested():
            self.tick()
            if not stop_requested():
                self._sleep(self._policy.poll_interval_seconds)

    def _dispatch_pending_commands(self, occurred_at: datetime) -> None:
        for _ in range(self._policy.command_batch_limit):
            with self._session_factory.begin() as db:
                result = dispatch_next_operator_command(db, occurred_at=occurred_at)
            if result is None:
                return

    def _claim(self, occurred_at: datetime) -> _DispatchClaim | SupervisorTickResult:
        with self._session_factory.begin() as db:
            head = db.scalar(
                select(RuntimeConfigHeadRecord)
                .where(RuntimeConfigHeadRecord.scope == "global")
                .with_for_update()
            )
            runtime = db.scalar(
                select(RuntimeControlRecord)
                .where(RuntimeControlRecord.scope == "global")
                .with_for_update()
            )
            if head is None or runtime is None:
                raise RuntimeError("global runtime singleton is missing")
            active_ids = tuple(
                db.scalars(
                    select(SessionRecord.id)
                    .where(SessionRecord.state.not_in(_TERMINAL_SESSION_STATES))
                    .order_by(SessionRecord.created_at, SessionRecord.id)
                    .limit(2)
                    .with_for_update(of=SessionRecord)
                )
            )
            if len(active_ids) > 1:
                raise RuntimeError("more than one active session exists")

            lease_expiry = _database_time(runtime.scheduler_lease_expires_at)
            if runtime.scheduler_lease_owner is not None and lease_expiry is not None:
                if lease_expiry > occurred_at:
                    return self._snapshot(
                        runtime,
                        status=SupervisorTickStatus.SKIPPED,
                        skip_reason=SupervisorSkipReason.SCHEDULER_BUSY,
                        observed_at=occurred_at,
                    )

            active_session_id = SessionId(root=active_ids[0]) if active_ids else None
            pending_wake = runtime.wake_generation > runtime.scheduler_last_handled_wake_generation
            trigger = self._trigger(
                runtime,
                active_session_id=active_session_id,
                pending_wake=pending_wake,
                occurred_at=occurred_at,
            )
            backoff_until = _database_time(runtime.scheduler_backoff_until)
            urgent_recovery = False
            if active_session_id is not None:
                active = db.get(SessionRecord, active_session_id.root)
                assert active is not None
                urgent_recovery = (
                    active.stop_requested_at is not None or active.abort_requested_at is not None
                )
            if backoff_until is not None and backoff_until > occurred_at and not urgent_recovery:
                if trigger is SupervisorTrigger.WAKE_NOW:
                    runtime.scheduler_last_handled_wake_generation = runtime.wake_generation
                    self._audit_finished(
                        db,
                        occurred_at=occurred_at,
                        trigger=trigger,
                        outcome=SupervisorSkipReason.BACKOFF.value,
                        runtime=runtime,
                    )
                return self._snapshot(
                    runtime,
                    status=SupervisorTickStatus.SKIPPED,
                    trigger=trigger,
                    skip_reason=SupervisorSkipReason.BACKOFF,
                    observed_at=occurred_at,
                )
            if trigger is None:
                return self._snapshot(
                    runtime,
                    status=SupervisorTickStatus.IDLE,
                    observed_at=occurred_at,
                )

            if active_session_id is None:
                blocked = self._new_session_blocker(db, head=head, runtime=runtime)
                if blocked is None and not self._resources_ready():
                    blocked = SupervisorSkipReason.RESOURCE_UNAVAILABLE
                if blocked is not None:
                    if trigger is SupervisorTrigger.WAKE_NOW:
                        runtime.scheduler_last_handled_wake_generation = runtime.wake_generation
                    else:
                        runtime.scheduler_next_scheduled_at = occurred_at + timedelta(
                            seconds=self._policy.schedule_interval_seconds
                        )
                    self._audit_finished(
                        db,
                        occurred_at=occurred_at,
                        trigger=trigger,
                        outcome=blocked.value,
                        runtime=runtime,
                    )
                    return self._snapshot(
                        runtime,
                        status=SupervisorTickStatus.SKIPPED,
                        trigger=trigger,
                        skip_reason=blocked,
                        observed_at=occurred_at,
                    )

            runtime.scheduler_fence += 1
            runtime.scheduler_lease_owner = self._owner
            runtime.scheduler_lease_expires_at = occurred_at + timedelta(
                seconds=self._policy.lease_ttl_seconds
            )
            runtime.updated_at = occurred_at
            claim = _DispatchClaim(
                owner=self._owner,
                fence=runtime.scheduler_fence,
                trigger=trigger,
                wake_generation=runtime.wake_generation,
                session_id=active_session_id,
            )
            append_global_audit(
                db,
                type=EventType.SCHEDULER_WAKE_STARTED,
                occurred_at=occurred_at,
                actor="supervisor",
                public_summary=f"Scheduler dispatch started: {trigger.value}",
                topic="audit.scheduler_wake_started.v1",
                payload={
                    "trigger": trigger.value,
                    "wake_generation": claim.wake_generation,
                    "scheduler_fence": claim.fence,
                    "session_id": str(active_session_id) if active_session_id else None,
                },
            )
            return claim

    def _trigger(
        self,
        runtime: RuntimeControlRecord,
        *,
        active_session_id: SessionId | None,
        pending_wake: bool,
        occurred_at: datetime,
    ) -> SupervisorTrigger | None:
        if active_session_id is not None:
            return SupervisorTrigger.RECOVERY
        if pending_wake:
            return SupervisorTrigger.WAKE_NOW
        next_scheduled_at = _database_time(runtime.scheduler_next_scheduled_at)
        if next_scheduled_at is None or next_scheduled_at <= occurred_at:
            return SupervisorTrigger.SCHEDULED
        return None

    def _new_session_blocker(
        self,
        db: Session,
        *,
        head: RuntimeConfigHeadRecord,
        runtime: RuntimeControlRecord,
    ) -> SupervisorSkipReason | None:
        if NodeState(runtime.node_state) is NodeState.PAUSED:
            return SupervisorSkipReason.OPERATOR_PAUSED
        if head.activating_config_snapshot_id is not None:
            return SupervisorSkipReason.CONFIG_ACTIVATION_IN_PROGRESS
        unresolved = db.scalar(
            select(CommitAttemptRecord.id)
            .where(CommitAttemptRecord.status.in_(_UNRESOLVED_ATTEMPT_STATES))
            .limit(1)
        )
        if unresolved is not None:
            return SupervisorSkipReason.UNRESOLVED_COMMIT
        return None

    def _run_claimed(self, claim: _DispatchClaim) -> SessionRunResult | WakeSkipped:
        for _ in range(self._policy.max_session_steps):
            try:
                return self._runner.run_once(max_steps=1)
            except SessionStepLimitExceededError:
                self._renew(claim, _clock_time(self._clock()))
        raise RuntimeError("autonomous session exceeded the supervisor step limit")

    def _renew(self, claim: _DispatchClaim, occurred_at: datetime) -> None:
        with self._session_factory.begin() as db:
            runtime = self._locked_runtime(db, claim)
            runtime.scheduler_lease_expires_at = occurred_at + timedelta(
                seconds=self._policy.lease_ttl_seconds
            )
            runtime.updated_at = occurred_at

    def _finish_session(
        self,
        claim: _DispatchClaim,
        result: SessionRunResult,
    ) -> SupervisorTickResult:
        occurred_at = _clock_time(self._clock())
        with self._session_factory.begin() as db:
            runtime = self._locked_runtime(db, claim)
            runtime.scheduler_last_session_id = result.session_id.root
            runtime.scheduler_last_terminal_state = result.terminal_state.value
            runtime.scheduler_last_error_class = None
            if result.terminal_state is SessionState.FAILED:
                self._apply_failure(runtime, occurred_at=occurred_at)
            else:
                runtime.scheduler_consecutive_failures = 0
                runtime.scheduler_backoff_until = None
                runtime.scheduler_next_scheduled_at = occurred_at + timedelta(
                    seconds=self._policy.schedule_interval_seconds
                )
            self._consume_and_release(runtime, claim=claim, occurred_at=occurred_at)
            self._audit_finished(
                db,
                occurred_at=occurred_at,
                trigger=claim.trigger,
                outcome=result.terminal_state.value,
                runtime=runtime,
                session_id=result.session_id,
            )
            return self._snapshot(
                runtime,
                status=SupervisorTickStatus.SESSION_COMPLETED,
                trigger=claim.trigger,
                session_id=result.session_id,
                terminal_state=result.terminal_state,
                observed_at=occurred_at,
            )

    def _finish_skipped(
        self,
        claim: _DispatchClaim,
        reason: WakeSkipReason,
    ) -> SupervisorTickResult:
        occurred_at = _clock_time(self._clock())
        mapped = SupervisorSkipReason(reason.value)
        with self._session_factory.begin() as db:
            runtime = self._locked_runtime(db, claim)
            runtime.scheduler_backoff_until = None
            runtime.scheduler_next_scheduled_at = occurred_at + timedelta(
                seconds=self._policy.schedule_interval_seconds
            )
            self._consume_and_release(runtime, claim=claim, occurred_at=occurred_at)
            self._audit_finished(
                db,
                occurred_at=occurred_at,
                trigger=claim.trigger,
                outcome=mapped.value,
                runtime=runtime,
            )
            return self._snapshot(
                runtime,
                status=SupervisorTickStatus.SKIPPED,
                trigger=claim.trigger,
                skip_reason=mapped,
                observed_at=occurred_at,
            )

    def _finish_error(
        self,
        claim: _DispatchClaim,
        error: Exception,
    ) -> SupervisorTickResult:
        occurred_at = _clock_time(self._clock())
        error_class = type(error).__name__[:128]
        with self._session_factory.begin() as db:
            runtime = self._locked_runtime(db, claim)
            runtime.scheduler_last_session_id = (
                claim.session_id.root if claim.session_id is not None else None
            )
            runtime.scheduler_last_terminal_state = None
            runtime.scheduler_last_error_class = error_class
            self._apply_failure(runtime, occurred_at=occurred_at)
            self._consume_and_release(runtime, claim=claim, occurred_at=occurred_at)
            self._audit_finished(
                db,
                occurred_at=occurred_at,
                trigger=claim.trigger,
                outcome="error",
                runtime=runtime,
                session_id=claim.session_id,
                error_class=error_class,
            )
            return self._snapshot(
                runtime,
                status=SupervisorTickStatus.ERROR,
                trigger=claim.trigger,
                error_class=error_class,
                observed_at=occurred_at,
            )

    def _record_unclaimed_error(
        self,
        error: Exception,
        occurred_at: datetime,
    ) -> SupervisorTickResult:
        error_class = type(error).__name__[:128]
        with self._session_factory.begin() as db:
            runtime = db.scalar(
                select(RuntimeControlRecord)
                .where(RuntimeControlRecord.scope == "global")
                .with_for_update()
            )
            if runtime is None:
                raise RuntimeError("global runtime control is missing") from error
            runtime.scheduler_last_error_class = error_class
            self._apply_failure(runtime, occurred_at=occurred_at)
            self._audit_finished(
                db,
                occurred_at=occurred_at,
                trigger=None,
                outcome="admission_error",
                runtime=runtime,
                error_class=error_class,
            )
            return self._snapshot(
                runtime,
                status=SupervisorTickStatus.ERROR,
                error_class=error_class,
                observed_at=occurred_at,
            )

    def _apply_failure(self, runtime: RuntimeControlRecord, *, occurred_at: datetime) -> None:
        runtime.scheduler_consecutive_failures += 1
        delay = self._policy.failure_delay(runtime.scheduler_consecutive_failures)
        retry_at = occurred_at + timedelta(seconds=delay)
        runtime.scheduler_backoff_until = retry_at
        runtime.scheduler_next_scheduled_at = retry_at
        if runtime.scheduler_consecutive_failures >= self._policy.pause_after_consecutive_failures:
            runtime.node_state = NodeState.PAUSED.value
        runtime.updated_at = occurred_at

    @staticmethod
    def _consume_and_release(
        runtime: RuntimeControlRecord,
        *,
        claim: _DispatchClaim,
        occurred_at: datetime,
    ) -> None:
        if claim.trigger is SupervisorTrigger.WAKE_NOW:
            runtime.scheduler_last_handled_wake_generation = max(
                runtime.scheduler_last_handled_wake_generation,
                claim.wake_generation,
            )
        runtime.scheduler_lease_owner = None
        runtime.scheduler_lease_expires_at = None
        runtime.updated_at = occurred_at

    @staticmethod
    def _locked_runtime(db: Session, claim: _DispatchClaim) -> RuntimeControlRecord:
        runtime = db.scalar(
            select(RuntimeControlRecord)
            .where(RuntimeControlRecord.scope == "global")
            .with_for_update()
        )
        if (
            runtime is None
            or runtime.scheduler_lease_owner != claim.owner
            or runtime.scheduler_fence != claim.fence
        ):
            raise SupervisorLeaseLostError("scheduler dispatch was fenced by another owner")
        return runtime

    def _audit_finished(
        self,
        db: Session,
        *,
        occurred_at: datetime,
        trigger: SupervisorTrigger | None,
        outcome: str,
        runtime: RuntimeControlRecord,
        session_id: SessionId | None = None,
        error_class: str | None = None,
    ) -> None:
        append_global_audit(
            db,
            type=EventType.SCHEDULER_WAKE_FINISHED,
            occurred_at=occurred_at,
            actor="supervisor",
            public_summary=f"Scheduler dispatch finished: {outcome}",
            topic="audit.scheduler_wake_finished.v1",
            payload={
                "trigger": trigger.value if trigger is not None else None,
                "outcome": outcome,
                "session_id": str(session_id) if session_id is not None else None,
                "error_class": error_class,
                "consecutive_failures": runtime.scheduler_consecutive_failures,
                "backoff_until": (
                    _database_time(runtime.scheduler_backoff_until).isoformat()
                    if runtime.scheduler_backoff_until is not None
                    else None
                ),
                "auto_paused": runtime.node_state == NodeState.PAUSED.value
                and runtime.scheduler_consecutive_failures
                >= self._policy.pause_after_consecutive_failures,
            },
        )

    @staticmethod
    def _snapshot(
        runtime: RuntimeControlRecord,
        *,
        status: SupervisorTickStatus,
        observed_at: datetime,
        trigger: SupervisorTrigger | None = None,
        skip_reason: SupervisorSkipReason | None = None,
        session_id: SessionId | None = None,
        terminal_state: SessionState | None = None,
        error_class: str | None = None,
    ) -> SupervisorTickResult:
        return SupervisorTickResult(
            status=status,
            observed_at=observed_at,
            trigger=trigger,
            skip_reason=skip_reason,
            session_id=session_id,
            terminal_state=terminal_state,
            error_class=error_class,
            handled_wake_generation=runtime.scheduler_last_handled_wake_generation,
            next_scheduled_at=_database_time(runtime.scheduler_next_scheduled_at),
            backoff_until=_database_time(runtime.scheduler_backoff_until),
            consecutive_failures=runtime.scheduler_consecutive_failures,
        )


def _clock_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("supervisor clock must return a timezone-aware datetime")
    return value.astimezone(UTC)


def _database_time(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
