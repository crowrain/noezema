"""Fenced session phases and resumable Explorer model turns."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator.models import (
    ClaimedSession,
    ExplorerTurnResult,
    OperatorControlKind,
    OperatorControlResult,
    SafeBoundaryKind,
    SafeBoundaryResult,
    SessionWorkDirective,
    SessionWorkKind,
)
from packages.cognition import (
    ExplorerContext,
    PromptBundle,
    build_explorer_request,
    validate_explorer_decision,
)
from packages.domain import (
    ActionId,
    ActionState,
    BoundAction,
    BudgetExhaustionReason,
    DecisionEnvelope,
    EventType,
    IdempotencyClass,
    IdempotencyKey,
    ModelRunId,
    SessionBudget,
    SessionBudgetUsage,
    SessionId,
    SessionLease,
    SessionState,
    ToolDecision,
    ToolName,
    TurnId,
    bind_action,
    canonical_json_sha256,
)
from packages.llm_gateway import GatewayRequest, ModelPhase, ModelRunResult
from packages.llm_gateway.fingerprint import response_schema_sha256
from packages.persistence import (
    ConcurrencyControlError,
    acquire_session_lease,
    append_session_audit,
    release_session_lease,
    validate_session_lease,
)
from packages.persistence.models import (
    ActionRecord,
    CommitAttemptRecord,
    ModelRunRecord,
    OrchestratorTurnRecord,
    SessionRecord,
)
from packages.tool_broker import ToolBroker


class SessionRuntimeError(RuntimeError):
    """Base class for a rejected orchestrator operation."""


class IllegalSessionTransitionError(SessionRuntimeError):
    """The requested phase edge is absent from the trusted state graph."""


class InconsistentSessionRuntimeError(SessionRuntimeError):
    """Durable session, turn, action, or commit state contradicts itself."""


class TurnBindingConflictError(SessionRuntimeError):
    """A stable turn identity was reused for another immutable request."""


class TurnFenceRejectedError(SessionRuntimeError):
    """A model result was produced under an obsolete session fence."""


class FailedTurnError(SessionRuntimeError):
    """A durable model turn has already failed and cannot be replayed."""


class OperatorControlRejectedError(SessionRuntimeError):
    """An operator control cannot cross the commit boundary or a terminal state."""


class GracefulStopRequestedError(SessionRuntimeError):
    """The session reached a safe boundary after a durable graceful-stop intent."""


class SessionAbortedError(SessionRuntimeError):
    """The session discarded unfinished cognitive work after an operator abort."""


class SoftBudgetExhaustedError(SessionRuntimeError):
    """The exploration budget is exhausted and reserved finalization must begin."""


class SessionBoundaryFailureError(SessionRuntimeError):
    """A safe-boundary invariant forced deterministic session failure."""


class DecisionGateway(Protocol):
    def generate_decision(self, request: GatewayRequest) -> ModelRunResult: ...


_LEGAL_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.WAKING, SessionState.ABORTING}),
    SessionState.WAKING: frozenset(
        {SessionState.ORIENTING, SessionState.STOPPING, SessionState.ABORTING}
    ),
    SessionState.ORIENTING: frozenset(
        {
            SessionState.SELECTING_QUESTION,
            SessionState.PLANNING,
            SessionState.STOPPING,
            SessionState.ABORTING,
        }
    ),
    SessionState.SELECTING_QUESTION: frozenset(
        {SessionState.PLANNING, SessionState.STOPPING, SessionState.ABORTING}
    ),
    SessionState.PLANNING: frozenset(
        {SessionState.EXPLORING, SessionState.STOPPING, SessionState.ABORTING}
    ),
    SessionState.EXPLORING: frozenset(
        {SessionState.VERIFYING, SessionState.STOPPING, SessionState.ABORTING}
    ),
    SessionState.VERIFYING: frozenset(
        {
            SessionState.EXPLORING,
            SessionState.STOPPING,
            SessionState.CONSOLIDATING,
            SessionState.ABORTING,
        }
    ),
    SessionState.STOPPING: frozenset(
        {SessionState.CONSOLIDATING, SessionState.REPORTING, SessionState.ABORTING}
    ),
    SessionState.CONSOLIDATING: frozenset(
        {SessionState.COMMITTING, SessionState.REPORTING, SessionState.ABORTING}
    ),
    SessionState.REPORTING: frozenset({SessionState.SUCCEEDED_PARTIAL, SessionState.ABORTING}),
    SessionState.COMMITTING: frozenset({SessionState.RECONCILING_COMMIT}),
    SessionState.RECONCILING_COMMIT: frozenset({SessionState.COMMITTING}),
    SessionState.ABORTING: frozenset({SessionState.FAILED, SessionState.CANCELLED}),
}

_PHASE_WORK: dict[SessionState, SessionWorkKind] = {
    SessionState.WAKING: SessionWorkKind.WAKE,
    SessionState.ORIENTING: SessionWorkKind.ORIENT,
    SessionState.SELECTING_QUESTION: SessionWorkKind.SELECT_QUESTION,
    SessionState.PLANNING: SessionWorkKind.PLAN,
    SessionState.EXPLORING: SessionWorkKind.EXPLORE,
    SessionState.VERIFYING: SessionWorkKind.VERIFY,
    SessionState.STOPPING: SessionWorkKind.STOP,
    SessionState.CONSOLIDATING: SessionWorkKind.CONSOLIDATE,
    SessionState.REPORTING: SessionWorkKind.REPORT,
    SessionState.ABORTING: SessionWorkKind.ABORT,
}

_RECOVERABLE_ACTION_STATES = {
    ActionState.PROPOSED.value,
    ActionState.POLICY_EVALUATED.value,
    ActionState.ACCEPTED.value,
    ActionState.STARTED.value,
    ActionState.OUTCOME_UNKNOWN.value,
}

_ACTION_IN_FLIGHT_STATES = {
    ActionState.PROPOSED.value,
    ActionState.POLICY_EVALUATED.value,
    ActionState.ACCEPTED.value,
    ActionState.STARTED.value,
}

_GRACEFUL_STOP_SOURCE_STATES = {
    SessionState.WAKING,
    SessionState.ORIENTING,
    SessionState.SELECTING_QUESTION,
    SessionState.PLANNING,
    SessionState.EXPLORING,
    SessionState.VERIFYING,
    SessionState.STOPPING,
    SessionState.CONSOLIDATING,
    SessionState.REPORTING,
}

_ABORT_SOURCE_STATES = {
    SessionState.CREATED,
    *_GRACEFUL_STOP_SOURCE_STATES,
    SessionState.ABORTING,
}


def claim_session(
    db: Session,
    *,
    session_id: SessionId,
    owner: str,
    ttl_seconds: int,
    occurred_at: datetime | None = None,
) -> ClaimedSession:
    """Acquire one session incarnation and return its exact durable recovery work."""

    timestamp = occurred_at or datetime.now(UTC)
    lease = acquire_session_lease(
        db,
        session_id=session_id,
        owner=owner,
        ttl_seconds=ttl_seconds,
        occurred_at=timestamp,
    )
    session_record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
    if (
        SessionState(session_record.state) is SessionState.CREATED
        and session_record.abort_requested_at is None
    ):
        transition_session_phase(
            db,
            lease=lease,
            target=SessionState.WAKING,
            reason="session_claimed",
            occurred_at=timestamp,
        )
        session_record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
    directive = _recovery_directive(db, session_record)
    return ClaimedSession(session_id=session_id, lease=lease, directive=directive)


def transition_session_phase(
    db: Session,
    *,
    lease: SessionLease,
    target: SessionState,
    reason: str,
    occurred_at: datetime | None = None,
) -> bool:
    """Advance a session over one legal edge with its audit/outbox atomically."""

    timestamp = occurred_at or datetime.now(UTC)
    record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
    current = SessionState(record.state)
    if current is target:
        return False
    if current.is_terminal or target not in _LEGAL_TRANSITIONS.get(current, frozenset()):
        raise IllegalSessionTransitionError(f"illegal session transition: {current} -> {target}")
    record.state = target.value
    record.updated_at = timestamp
    if target.is_terminal:
        record.terminal_at = timestamp
        record.termination_reason = reason[:256]
    append_session_audit(
        db,
        session_id=lease.session_id,
        type=EventType.SESSION_STATE_CHANGED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary=f"Session state changed to {target.value}",
        topic="audit.session_state_changed.v1",
        payload={"from": current.value, "to": target.value, "reason": reason},
    )
    if target.is_terminal:
        release_session_lease(db, lease=lease, occurred_at=timestamp)
    db.flush()
    return True


def terminate_session(
    db: Session,
    *,
    lease: SessionLease,
    terminal_state: SessionState,
    reason: str,
    occurred_at: datetime | None = None,
) -> None:
    """Move a non-terminal session through ABORTING into FAILED or CANCELLED."""

    if terminal_state not in {SessionState.FAILED, SessionState.CANCELLED}:
        raise ValueError("failure termination supports only failed or cancelled")
    timestamp = occurred_at or datetime.now(UTC)
    record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
    if SessionState(record.state) is not SessionState.ABORTING:
        transition_session_phase(
            db,
            lease=lease,
            target=SessionState.ABORTING,
            reason=reason,
            occurred_at=timestamp,
        )
    transition_session_phase(
        db,
        lease=lease,
        target=terminal_state,
        reason=reason,
        occurred_at=timestamp,
    )


def request_graceful_stop(
    db: Session,
    *,
    session_id: SessionId,
    actor: str = "operator",
    requested_at: datetime | None = None,
) -> OperatorControlResult:
    """Persist a graceful-stop intent without racing the active worker."""

    return _request_operator_control(
        db,
        session_id=session_id,
        command=OperatorControlKind.STOP_GRACEFULLY,
        actor=actor,
        requested_at=requested_at,
    )


def request_session_abort(
    db: Session,
    *,
    session_id: SessionId,
    actor: str = "operator",
    requested_at: datetime | None = None,
) -> OperatorControlResult:
    """Persist an abort intent; the worker applies it only at a safe boundary."""

    return _request_operator_control(
        db,
        session_id=session_id,
        command=OperatorControlKind.ABORT_SESSION,
        actor=actor,
        requested_at=requested_at,
    )


def session_budget_usage(
    db: Session,
    *,
    session_id: SessionId,
    occurred_at: datetime | None = None,
) -> SessionBudgetUsage:
    """Derive usage from durable model/action rows instead of mutable counters."""

    record = db.get(SessionRecord, session_id.root)
    if record is None:
        raise LookupError(f"session does not exist: {session_id}")
    return _session_budget_usage(db, record, occurred_at or datetime.now(UTC))


def apply_session_safe_boundary(
    db: Session,
    *,
    lease: SessionLease,
    occurred_at: datetime | None = None,
) -> SafeBoundaryResult:
    """Apply action outcome, operator intent and soft budget in canonical order."""

    timestamp = occurred_at or datetime.now(UTC)
    record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
    active_actions = tuple(
        db.scalars(
            select(ActionRecord)
            .where(
                ActionRecord.session_id == record.id,
                ActionRecord.state.in_(_ACTION_IN_FLIGHT_STATES),
            )
            .order_by(ActionRecord.created_at, ActionRecord.id)
            .with_for_update()
        )
    )
    if len(active_actions) > 1:
        raise InconsistentSessionRuntimeError("session has multiple in-flight actions")
    usage = _session_budget_usage(db, record, timestamp)
    if active_actions:
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.ACTION_IN_FLIGHT,
            state=SessionState(record.state),
            budget_usage=usage,
            action_id=ActionId(root=active_actions[0].id),
        )

    unknown_action = db.scalar(
        select(ActionRecord)
        .where(
            ActionRecord.session_id == record.id,
            ActionRecord.state == ActionState.OUTCOME_UNKNOWN.value,
        )
        .order_by(ActionRecord.created_at.desc(), ActionRecord.id.desc())
        .limit(1)
        .with_for_update()
    )
    if unknown_action is not None:
        _discard_prepared_turn(db, record, timestamp, "action_outcome_unknown")
        terminate_session(
            db,
            lease=lease,
            terminal_state=SessionState.FAILED,
            reason="action_outcome_unknown",
            occurred_at=timestamp,
        )
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.FAILED,
            state=SessionState.FAILED,
            budget_usage=usage,
        )

    if record.abort_requested_at is not None:
        _discard_prepared_turn(db, record, timestamp, "operator_abort")
        terminate_session(
            db,
            lease=lease,
            terminal_state=SessionState.CANCELLED,
            reason="operator_abort",
            occurred_at=timestamp,
        )
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.CANCELLED,
            state=SessionState.CANCELLED,
            budget_usage=usage,
        )

    if usage.exhaustion_reason is BudgetExhaustionReason.HOST_DEADLINE:
        _record_budget_exhaustion(db, record, usage.exhaustion_reason, timestamp)
        _discard_prepared_turn(db, record, timestamp, usage.exhaustion_reason.value)
        terminate_session(
            db,
            lease=lease,
            terminal_state=SessionState.FAILED,
            reason="host_reserve_exhausted",
            occurred_at=timestamp,
        )
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.FAILED,
            state=SessionState.FAILED,
            budget_usage=usage,
        )

    if record.stop_requested_at is not None:
        _discard_prepared_turn(db, record, timestamp, "operator_stop")
        resulting_state = _move_to_stopping(
            db,
            lease=lease,
            state=SessionState(record.state),
            reason="operator_stop",
            occurred_at=timestamp,
        )
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.STOPPING,
            state=resulting_state,
            budget_usage=usage,
        )

    if usage.exhaustion_reason is not None:
        _record_budget_exhaustion(db, record, usage.exhaustion_reason, timestamp)
        _discard_prepared_turn(db, record, timestamp, usage.exhaustion_reason.value)
        resulting_state = _move_to_stopping(
            db,
            lease=lease,
            state=SessionState(record.state),
            reason=f"soft_budget:{usage.exhaustion_reason.value}",
            occurred_at=timestamp,
        )
        return SafeBoundaryResult(
            kind=SafeBoundaryKind.SOFT_EXHAUSTED,
            state=resulting_state,
            budget_usage=usage,
        )

    return SafeBoundaryResult(
        kind=SafeBoundaryKind.CONTINUE,
        state=SessionState(record.state),
        budget_usage=usage,
    )


def _request_operator_control(
    db: Session,
    *,
    session_id: SessionId,
    command: OperatorControlKind,
    actor: str,
    requested_at: datetime | None,
) -> OperatorControlResult:
    timestamp = requested_at or datetime.now(UTC)
    normalized_actor = actor.strip()
    if not normalized_actor:
        raise ValueError("operator control actor must not be empty")
    record = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if record is None:
        raise LookupError(f"session does not exist: {session_id}")
    state = SessionState(record.state)
    allowed = (
        _GRACEFUL_STOP_SOURCE_STATES
        if command is OperatorControlKind.STOP_GRACEFULLY
        else _ABORT_SOURCE_STATES
    )
    if state not in allowed:
        raise OperatorControlRejectedError(f"{command.value} is rejected in {state.value}")
    attribute = (
        "stop_requested_at"
        if command is OperatorControlKind.STOP_GRACEFULLY
        else "abort_requested_at"
    )
    existing = getattr(record, attribute)
    if existing is not None:
        return OperatorControlResult(
            command=command,
            state=state,
            requested_at=_aware(existing),
            newly_recorded=False,
        )
    setattr(record, attribute, timestamp)
    event_type = (
        EventType.SESSION_STOP_REQUESTED
        if command is OperatorControlKind.STOP_GRACEFULLY
        else EventType.SESSION_ABORT_REQUESTED
    )
    append_session_audit(
        db,
        session_id=session_id,
        type=event_type,
        occurred_at=timestamp,
        actor=normalized_actor,
        public_summary=f"Operator requested {command.value}",
        topic=f"audit.session_{command.value}_requested.v1",
        payload={"command": command.value, "state": state.value},
    )
    db.flush()
    return OperatorControlResult(
        command=command,
        state=state,
        requested_at=timestamp,
        newly_recorded=True,
    )


def _session_budget_usage(
    db: Session,
    record: SessionRecord,
    occurred_at: datetime,
) -> SessionBudgetUsage:
    try:
        budget = SessionBudget.model_validate(record.budget)
    except ValueError as exc:
        raise InconsistentSessionRuntimeError("session budget snapshot is invalid") from exc
    if canonical_json_sha256(record.budget) != record.budget_sha256:
        raise InconsistentSessionRuntimeError("session budget snapshot hash does not match")
    model_turns, input_tokens, output_tokens = db.execute(
        select(
            func.count(ModelRunRecord.id),
            func.coalesce(func.sum(ModelRunRecord.input_tokens), 0),
            func.coalesce(func.sum(ModelRunRecord.output_tokens), 0),
        ).where(ModelRunRecord.session_id == record.id)
    ).one()
    tool_actions = db.scalar(
        select(func.count(ActionRecord.id)).where(ActionRecord.session_id == record.id)
    )
    used_turns = int(model_turns or 0)
    used_actions = int(tool_actions or 0)
    used_input = int(input_tokens or 0)
    used_output = int(output_tokens or 0)
    reason: BudgetExhaustionReason | None = None
    if occurred_at >= _aware(record.host_deadline_at):
        reason = BudgetExhaustionReason.HOST_DEADLINE
    elif occurred_at >= _aware(record.cognitive_deadline_at):
        reason = BudgetExhaustionReason.COGNITIVE_DEADLINE
    elif used_turns >= budget.exploration_model_turn_limit:
        reason = BudgetExhaustionReason.MODEL_TURNS
    elif used_actions >= budget.max_tool_actions:
        reason = BudgetExhaustionReason.TOOL_ACTIONS
    elif used_input >= budget.exploration_input_token_limit:
        reason = BudgetExhaustionReason.INPUT_TOKENS
    elif used_output >= budget.exploration_output_token_limit:
        reason = BudgetExhaustionReason.OUTPUT_TOKENS
    return SessionBudgetUsage(
        model_turns=used_turns,
        tool_actions=used_actions,
        input_tokens=used_input,
        output_tokens=used_output,
        remaining_exploration_model_turns=max(0, budget.exploration_model_turn_limit - used_turns),
        remaining_tool_actions=max(0, budget.max_tool_actions - used_actions),
        remaining_exploration_input_tokens=max(
            0, budget.exploration_input_token_limit - used_input
        ),
        remaining_exploration_output_tokens=max(
            0, budget.exploration_output_token_limit - used_output
        ),
        exhaustion_reason=reason,
    )


def _record_budget_exhaustion(
    db: Session,
    record: SessionRecord,
    reason: BudgetExhaustionReason,
    occurred_at: datetime,
) -> None:
    if record.soft_exhausted_at is not None:
        return
    record.soft_exhausted_at = occurred_at
    record.soft_exhaustion_reason = reason.value
    append_session_audit(
        db,
        session_id=SessionId(root=record.id),
        type=EventType.SESSION_BUDGET_EXHAUSTED,
        occurred_at=occurred_at,
        actor="orchestrator",
        public_summary=f"Session budget exhausted: {reason.value}",
        topic="audit.session_budget_exhausted.v1",
        payload={"reason": reason.value},
    )


def _discard_prepared_turn(
    db: Session,
    record: SessionRecord,
    occurred_at: datetime,
    error_code: str,
) -> None:
    prepared = tuple(
        db.scalars(
            select(OrchestratorTurnRecord)
            .where(
                OrchestratorTurnRecord.session_id == record.id,
                OrchestratorTurnRecord.status == "prepared",
            )
            .with_for_update()
        )
    )
    if len(prepared) > 1:
        raise InconsistentSessionRuntimeError("session has multiple prepared model turns")
    if prepared:
        prepared[0].status = "failed"
        prepared[0].error_code = error_code[:128]
        prepared[0].completed_at = occurred_at


def _move_to_stopping(
    db: Session,
    *,
    lease: SessionLease,
    state: SessionState,
    reason: str,
    occurred_at: datetime,
) -> SessionState:
    if state in {
        SessionState.STOPPING,
        SessionState.CONSOLIDATING,
        SessionState.REPORTING,
    }:
        return state
    if state not in _GRACEFUL_STOP_SOURCE_STATES:
        raise InconsistentSessionRuntimeError(f"cannot enter stopping from {state.value}")
    transition_session_phase(
        db,
        lease=lease,
        target=SessionState.STOPPING,
        reason=reason,
        occurred_at=occurred_at,
    )
    return SessionState.STOPPING


class DurableSessionOrchestrator:
    """Persist turn intent before LLM I/O and replay downstream effects safely."""

    def __init__(
        self,
        *,
        session_factory: sessionmaker[Session],
        gateway: DecisionGateway,
        tool_broker: ToolBroker,
        lease_owner: str,
        lease_ttl_seconds: int = 300,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not lease_owner.strip():
            raise ValueError("lease_owner must identify one worker incarnation")
        self._session_factory = session_factory
        self._gateway = gateway
        self._tool_broker = tool_broker
        self._lease_owner = lease_owner
        self._lease_ttl_seconds = lease_ttl_seconds
        self._clock = clock or (lambda: datetime.now(UTC))

    def run_explorer_turn(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
        context: ExplorerContext,
        prompts: PromptBundle,
        policy_version: str,
    ) -> ExplorerTurnResult:
        request = build_explorer_request(
            context,
            prompts=prompts,
            policy_version=policy_version,
        )
        return self._run_explorer_request(
            session_id=session_id,
            turn_id=turn_id,
            request=request,
            context=context,
        )

    def resume_explorer_turn(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
    ) -> ExplorerTurnResult:
        with self._session_factory() as db:
            turn = db.get(OrchestratorTurnRecord, turn_id.root)
            if turn is None or turn.session_id != session_id.root:
                raise InconsistentSessionRuntimeError("orchestrator turn is missing")
            request = _gateway_request(turn.request)
        context = _explorer_context(request)
        return self._run_explorer_request(
            session_id=session_id,
            turn_id=turn_id,
            request=request,
            context=context,
        )

    def _run_explorer_request(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
        request: GatewayRequest,
        context: ExplorerContext,
    ) -> ExplorerTurnResult:
        started_at = self._clock()
        self._enforce_safe_boundary(session_id=session_id, occurred_at=started_at)
        persisted = self._prepare_turn(
            session_id=session_id,
            turn_id=turn_id,
            request=request,
            occurred_at=started_at,
        )
        replayed = persisted is not None
        if persisted is None:
            try:
                generated = self._gateway.generate_decision(request)
                validate_explorer_decision(generated.decision, context=context)
            except Exception as exc:
                self._fail_turn_if_owned(
                    session_id=session_id,
                    turn_id=turn_id,
                    error_code=type(exc).__name__,
                )
                self._enforce_safe_boundary(session_id=session_id)
                raise
            persisted = self._complete_turn(
                session_id=session_id,
                turn_id=turn_id,
                request=request,
                result=generated,
            )

        self._enforce_safe_boundary(session_id=session_id)
        model_run_id = self._model_run_id(session_id=session_id, turn_id=turn_id)
        action_result = None
        if isinstance(persisted.decision.decision, ToolDecision):
            action = self._bound_action(
                session_id=session_id,
                model_run_id=model_run_id,
                decision=persisted.decision.decision,
            )
            action_result = self._tool_broker.run(action)
            boundary = self._safe_boundary(session_id=session_id)
            if boundary.kind is not SafeBoundaryKind.SOFT_EXHAUSTED:
                self._raise_for_boundary(boundary)
        else:
            timestamp = self._clock()
            with self._session_factory.begin() as db:
                lease = self._acquire(db, session_id=session_id, occurred_at=timestamp)
                record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
                state = SessionState(record.state)
                if state is SessionState.EXPLORING:
                    transition_session_phase(
                        db,
                        lease=lease,
                        target=SessionState.VERIFYING,
                        reason="explorer_completed",
                        occurred_at=timestamp,
                    )
                elif state is not SessionState.VERIFYING:
                    raise InconsistentSessionRuntimeError(
                        "completed Explorer turn is outside exploring/verifying"
                    )
        return ExplorerTurnResult(
            session_id=session_id,
            turn_id=turn_id,
            model_run_id=model_run_id,
            decision=persisted.decision,
            action_result=action_result,
            replayed_model_run=replayed,
        )

    def _prepare_turn(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
        request: GatewayRequest,
        occurred_at: datetime | None = None,
    ) -> ModelRunResult | None:
        if request.phase is not ModelPhase.EXPLORATION:
            raise ValueError("Explorer runtime accepts only exploration requests")
        timestamp = occurred_at or self._clock()
        request_json = request.model_dump(mode="json")
        request_hash = canonical_json_sha256(request_json)
        schema_hash = response_schema_sha256(DecisionEnvelope)
        with self._session_factory.begin() as db:
            lease = self._acquire(db, session_id=session_id, occurred_at=timestamp)
            session_record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
            state = SessionState(session_record.state)
            turn = db.scalar(
                select(OrchestratorTurnRecord)
                .where(OrchestratorTurnRecord.id == turn_id.root)
                .with_for_update()
            )
            if turn is None:
                if state is not SessionState.EXPLORING:
                    raise InconsistentSessionRuntimeError(
                        "a new Explorer turn requires the exploring state"
                    )
                if (
                    session_record.stop_requested_at is not None
                    or session_record.abort_requested_at is not None
                    or session_record.soft_exhausted_at is not None
                ):
                    raise InconsistentSessionRuntimeError(
                        "a new Explorer turn cannot cross a pending safe boundary"
                    )
                in_flight = db.scalar(
                    select(OrchestratorTurnRecord.id)
                    .where(
                        OrchestratorTurnRecord.session_id == session_id.root,
                        OrchestratorTurnRecord.status == "prepared",
                    )
                    .limit(1)
                    .with_for_update()
                )
                if in_flight is not None:
                    raise InconsistentSessionRuntimeError(
                        "session already has a prepared model turn"
                    )
                ordinal = db.scalar(
                    select(func.coalesce(func.max(OrchestratorTurnRecord.ordinal), 0)).where(
                        OrchestratorTurnRecord.session_id == session_id.root
                    )
                )
                turn = OrchestratorTurnRecord(
                    id=turn_id.root,
                    session_id=session_id.root,
                    ordinal=int(ordinal or 0) + 1,
                    phase=request.phase.value,
                    status="prepared",
                    lease_owner=lease.owner,
                    session_fence=lease.fence,
                    request=request_json,
                    request_sha256=request_hash,
                    response_schema_sha256=schema_hash,
                    model_run_id=None,
                    result=None,
                    error_code=None,
                    created_at=timestamp,
                    completed_at=None,
                )
                db.add(turn)
                db.flush()
                return None
            if (
                turn.session_id != session_id.root
                or turn.request_sha256 != request_hash
                or turn.response_schema_sha256 != schema_hash
                or turn.request != request_json
            ):
                raise TurnBindingConflictError("turn is bound to another immutable request")
            if turn.status == "failed":
                raise FailedTurnError(turn.error_code or "model turn failed")
            if turn.status == "completed":
                if turn.result is None:
                    raise InconsistentSessionRuntimeError("completed turn has no result")
                return _model_run_result(turn.result)
            if state is not SessionState.EXPLORING:
                raise InconsistentSessionRuntimeError(
                    "prepared Explorer turn requires the exploring state"
                )
            turn.lease_owner = lease.owner
            turn.session_fence = lease.fence
            db.flush()
            return None

    def _complete_turn(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
        request: GatewayRequest,
        result: ModelRunResult,
    ) -> ModelRunResult:
        timestamp = self._clock()
        aborted = False
        with self._session_factory.begin() as db:
            try:
                lease = self._acquire(db, session_id=session_id, occurred_at=timestamp)
            except ConcurrencyControlError as exc:
                raise TurnFenceRejectedError(
                    "model result cannot reacquire its session incarnation"
                ) from exc
            turn = db.scalar(
                select(OrchestratorTurnRecord)
                .where(OrchestratorTurnRecord.id == turn_id.root)
                .with_for_update()
            )
            if turn is None or turn.session_id != session_id.root:
                raise InconsistentSessionRuntimeError("prepared turn disappeared")
            if turn.status == "completed":
                if turn.result is None:
                    raise InconsistentSessionRuntimeError("completed turn has no result")
                return _model_run_result(turn.result)
            if turn.status != "prepared":
                raise FailedTurnError(turn.error_code or "model turn failed")
            if turn.lease_owner != lease.owner or turn.session_fence != lease.fence:
                raise TurnFenceRejectedError("model result was produced under a stale fence")
            session_record = validate_session_lease(db, lease=lease, occurred_at=timestamp)
            if session_record.abort_requested_at is not None:
                turn.status = "failed"
                turn.error_code = "operator_abort"
                turn.completed_at = timestamp
                terminate_session(
                    db,
                    lease=lease,
                    terminal_state=SessionState.CANCELLED,
                    reason="operator_abort",
                    occurred_at=timestamp,
                )
                aborted = True
            else:
                model_run_id = ModelRunId.new()
                context_manifest = _context_manifest(request)
                db.add(
                    ModelRunRecord(
                        id=model_run_id.root,
                        session_id=session_id.root,
                        turn_id=turn_id.root,
                        phase=request.phase.value,
                        model_fingerprint=result.model_fingerprint_sha256,
                        context_manifest_sha256=request.context_manifest_sha256,
                        context_manifest=context_manifest,
                        prompt_version=request.prompt_version,
                        tool_schema_sha256=result.tool_schema_sha256,
                        input_tokens=result.usage.input_tokens,
                        output_tokens=result.usage.output_tokens,
                        latency_ms=result.latency_ms,
                        finish_reason=result.finish_reason,
                        output_schema_valid=True,
                        raw_response_artifact=None,
                        created_at=timestamp,
                    )
                )
                db.flush()
                turn.status = "completed"
                turn.model_run_id = model_run_id.root
                turn.result = result.model_dump(mode="json")
                turn.completed_at = timestamp
                append_session_audit(
                    db,
                    session_id=session_id,
                    type=EventType.MODEL_RUN_COMPLETED,
                    occurred_at=timestamp,
                    actor="orchestrator",
                    public_summary=result.decision.public_rationale,
                    topic="audit.model_run_completed.v1",
                    payload={
                        "turn_id": str(turn_id),
                        "model_run_id": str(model_run_id),
                        "phase": request.phase.value,
                        "decision_kind": result.decision.decision.kind.value,
                        "input_tokens": result.usage.input_tokens,
                        "output_tokens": result.usage.output_tokens,
                        "latency_ms": result.latency_ms,
                    },
                )
                db.flush()
        if aborted:
            raise SessionAbortedError("operator abort discarded the unfinished model output")
        return result

    def _fail_turn_if_owned(
        self,
        *,
        session_id: SessionId,
        turn_id: TurnId,
        error_code: str,
    ) -> None:
        timestamp = self._clock()
        try:
            with self._session_factory.begin() as db:
                lease = self._acquire(db, session_id=session_id, occurred_at=timestamp)
                turn = db.scalar(
                    select(OrchestratorTurnRecord)
                    .where(OrchestratorTurnRecord.id == turn_id.root)
                    .with_for_update()
                )
                if (
                    turn is not None
                    and turn.status == "prepared"
                    and turn.lease_owner == lease.owner
                    and turn.session_fence == lease.fence
                ):
                    turn.status = "failed"
                    turn.error_code = error_code[:128]
                    turn.completed_at = timestamp
                    db.flush()
        except ConcurrencyControlError:
            return

    def _model_run_id(self, *, session_id: SessionId, turn_id: TurnId) -> ModelRunId:
        with self._session_factory() as db:
            turn = db.get(OrchestratorTurnRecord, turn_id.root)
            if (
                turn is None
                or turn.session_id != session_id.root
                or turn.status != "completed"
                or turn.model_run_id is None
            ):
                raise InconsistentSessionRuntimeError("completed turn has no model run")
            return ModelRunId(root=turn.model_run_id)

    def _bound_action(
        self,
        *,
        session_id: SessionId,
        model_run_id: ModelRunId,
        decision: ToolDecision,
    ) -> BoundAction:
        with self._session_factory() as db:
            record = db.scalar(
                select(ActionRecord).where(ActionRecord.model_run_id == model_run_id.root)
            )
            if record is None:
                return bind_action(
                    session_id=session_id,
                    model_run_id=model_run_id,
                    decision=decision,
                )
            action = BoundAction(
                action_id=ActionId(root=record.id),
                session_id=session_id,
                model_run_id=model_run_id,
                idempotency_key=IdempotencyKey(root=record.idempotency_key),
                idempotency_class=IdempotencyClass(record.idempotency_class),
                tool=ToolName(record.tool),
                arguments_json=record.arguments_json,
                arguments_sha256=record.arguments_sha256,
            )
            expected = bind_action(
                session_id=session_id,
                model_run_id=model_run_id,
                decision=decision,
            )
            if (
                action.tool is not expected.tool
                or action.arguments_json != expected.arguments_json
                or action.arguments_sha256 != expected.arguments_sha256
            ):
                raise InconsistentSessionRuntimeError(
                    "model run action conflicts with its persisted decision"
                )
            return action

    def _acquire(
        self,
        db: Session,
        *,
        session_id: SessionId,
        occurred_at: datetime,
    ) -> SessionLease:
        return acquire_session_lease(
            db,
            session_id=session_id,
            owner=self._lease_owner,
            ttl_seconds=self._lease_ttl_seconds,
            occurred_at=occurred_at,
        )

    def _safe_boundary(
        self,
        *,
        session_id: SessionId,
        occurred_at: datetime | None = None,
    ) -> SafeBoundaryResult:
        timestamp = occurred_at or self._clock()
        with self._session_factory.begin() as db:
            lease = self._acquire(db, session_id=session_id, occurred_at=timestamp)
            return apply_session_safe_boundary(db, lease=lease, occurred_at=timestamp)

    def _enforce_safe_boundary(
        self,
        *,
        session_id: SessionId,
        occurred_at: datetime | None = None,
    ) -> None:
        self._raise_for_boundary(
            self._safe_boundary(session_id=session_id, occurred_at=occurred_at)
        )

    @staticmethod
    def _raise_for_boundary(boundary: SafeBoundaryResult) -> None:
        if boundary.kind is SafeBoundaryKind.CONTINUE:
            return
        if boundary.kind is SafeBoundaryKind.ACTION_IN_FLIGHT:
            raise InconsistentSessionRuntimeError(
                f"action {boundary.action_id} must reach a terminal state before new work"
            )
        if boundary.kind is SafeBoundaryKind.STOPPING:
            raise GracefulStopRequestedError("operator stop reached a safe boundary")
        if boundary.kind is SafeBoundaryKind.CANCELLED:
            raise SessionAbortedError("operator abort reached a safe boundary")
        if boundary.kind is SafeBoundaryKind.SOFT_EXHAUSTED:
            reason = boundary.budget_usage.exhaustion_reason
            raise SoftBudgetExhaustedError(
                f"soft session budget exhausted: {reason.value if reason else 'unknown'}"
            )
        raise SessionBoundaryFailureError("session failed at a safe boundary")


def _recovery_directive(
    db: Session,
    session_record: SessionRecord,
) -> SessionWorkDirective:
    state = SessionState(session_record.state)
    if state is SessionState.COMMITTING:
        attempt = _bound_commit_attempt(db, session_record)
        kind = (
            SessionWorkKind.FINALIZE_COMMIT
            if attempt.status == "prepared"
            else SessionWorkKind.RECONCILE_COMMIT
        )
        return SessionWorkDirective(kind=kind, state=state)
    if state is SessionState.RECONCILING_COMMIT:
        _bound_commit_attempt(db, session_record)
        return SessionWorkDirective(kind=SessionWorkKind.RECONCILE_COMMIT, state=state)

    recoverable_actions = tuple(
        db.scalars(
            select(ActionRecord)
            .where(
                ActionRecord.session_id == session_record.id,
                ActionRecord.state.in_(_RECOVERABLE_ACTION_STATES),
            )
            .order_by(ActionRecord.created_at, ActionRecord.id)
        )
    )
    if len(recoverable_actions) > 1:
        raise InconsistentSessionRuntimeError("session has multiple recoverable actions")
    if recoverable_actions:
        action = recoverable_actions[0]
        return SessionWorkDirective(
            kind=SessionWorkKind.RECOVER_ACTION,
            state=state,
            action_id=ActionId(root=action.id),
        )

    if session_record.abort_requested_at is not None:
        return SessionWorkDirective(kind=SessionWorkKind.ABORT, state=state)
    if session_record.stop_requested_at is not None or session_record.soft_exhausted_at is not None:
        return SessionWorkDirective(kind=SessionWorkKind.STOP, state=state)

    latest_turn = db.scalar(
        select(OrchestratorTurnRecord)
        .where(OrchestratorTurnRecord.session_id == session_record.id)
        .order_by(OrchestratorTurnRecord.ordinal.desc())
        .limit(1)
    )
    if latest_turn is not None and latest_turn.status == "prepared":
        return SessionWorkDirective(
            kind=SessionWorkKind.RESUME_TURN,
            state=state,
            turn_id=TurnId(root=latest_turn.id),
        )
    if (
        latest_turn is not None
        and latest_turn.status == "completed"
        and state is SessionState.EXPLORING
    ):
        if latest_turn.result is None:
            raise InconsistentSessionRuntimeError("completed turn has no result")
        decision = _model_run_result(latest_turn.result).decision.decision
        if not isinstance(decision, ToolDecision):
            return SessionWorkDirective(
                kind=SessionWorkKind.RESUME_TURN,
                state=state,
                turn_id=TurnId(root=latest_turn.id),
            )
        if latest_turn.model_run_id is None:
            raise InconsistentSessionRuntimeError("completed turn has no model run")
        action = db.scalar(
            select(ActionRecord).where(ActionRecord.model_run_id == latest_turn.model_run_id)
        )
        if action is None:
            return SessionWorkDirective(
                kind=SessionWorkKind.RESUME_TURN,
                state=state,
                turn_id=TurnId(root=latest_turn.id),
            )

    kind = _PHASE_WORK.get(state)
    if kind is None:
        raise InconsistentSessionRuntimeError(f"session state has no recovery work: {state}")
    return SessionWorkDirective(kind=kind, state=state)


def _bound_commit_attempt(
    db: Session,
    session_record: SessionRecord,
) -> CommitAttemptRecord:
    if session_record.commit_attempt_id is None:
        raise InconsistentSessionRuntimeError("commit state has no bound attempt")
    attempt = db.get(CommitAttemptRecord, session_record.commit_attempt_id)
    if attempt is None or attempt.session_id != session_record.id:
        raise InconsistentSessionRuntimeError("bound commit attempt is missing")
    return attempt


def _context_manifest(request: GatewayRequest) -> dict[str, object]:
    try:
        payload = json.loads(request.messages[-1].content)
    except (IndexError, json.JSONDecodeError) as exc:
        raise InconsistentSessionRuntimeError(
            "gateway request context is not canonical JSON"
        ) from exc
    if not isinstance(payload, dict):
        raise InconsistentSessionRuntimeError("gateway request context is not an object")
    return payload


def _explorer_context(request: GatewayRequest) -> ExplorerContext:
    payload = _context_manifest(request)
    if payload.get("protocol") != "explorer-input/v2":
        raise InconsistentSessionRuntimeError("turn is not an Explorer protocol request")
    try:
        context = payload["context"]
        if not isinstance(context, dict):
            raise ValueError("Explorer context is not an object")
        return ExplorerContext.model_validate_json(_canonical_json(context))
    except (KeyError, ValueError) as exc:
        raise InconsistentSessionRuntimeError("stored Explorer context is invalid") from exc


def _gateway_request(value: dict[str, object]) -> GatewayRequest:
    return GatewayRequest.model_validate_json(_canonical_json(value))


def _model_run_result(value: dict[str, object]) -> ModelRunResult:
    return ModelRunResult.model_validate_json(_canonical_json(value))


def _canonical_json(value: dict[str, object]) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
