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
    DecisionEnvelope,
    EventType,
    IdempotencyClass,
    IdempotencyKey,
    ModelRunId,
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


class DecisionGateway(Protocol):
    def generate_decision(self, request: GatewayRequest) -> ModelRunResult: ...


_LEGAL_TRANSITIONS: dict[SessionState, frozenset[SessionState]] = {
    SessionState.CREATED: frozenset({SessionState.WAKING, SessionState.ABORTING}),
    SessionState.WAKING: frozenset({SessionState.ORIENTING, SessionState.ABORTING}),
    SessionState.ORIENTING: frozenset(
        {SessionState.SELECTING_QUESTION, SessionState.PLANNING, SessionState.ABORTING}
    ),
    SessionState.SELECTING_QUESTION: frozenset({SessionState.PLANNING, SessionState.ABORTING}),
    SessionState.PLANNING: frozenset({SessionState.EXPLORING, SessionState.ABORTING}),
    SessionState.EXPLORING: frozenset(
        {SessionState.VERIFYING, SessionState.STOPPING, SessionState.ABORTING}
    ),
    SessionState.VERIFYING: frozenset(
        {SessionState.EXPLORING, SessionState.CONSOLIDATING, SessionState.ABORTING}
    ),
    SessionState.STOPPING: frozenset(
        {SessionState.CONSOLIDATING, SessionState.REPORTING, SessionState.ABORTING}
    ),
    SessionState.CONSOLIDATING: frozenset(
        {SessionState.COMMITTING, SessionState.REPORTING, SessionState.ABORTING}
    ),
    SessionState.REPORTING: frozenset({SessionState.SUCCEEDED_PARTIAL, SessionState.ABORTING}),
    SessionState.COMMITTING: frozenset({SessionState.RECONCILING_COMMIT, SessionState.ABORTING}),
    SessionState.RECONCILING_COMMIT: frozenset({SessionState.COMMITTING, SessionState.ABORTING}),
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
    if SessionState(session_record.state) is SessionState.CREATED:
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
        persisted = self._prepare_turn(
            session_id=session_id,
            turn_id=turn_id,
            request=request,
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
                raise
            persisted = self._complete_turn(
                session_id=session_id,
                turn_id=turn_id,
                request=request,
                result=generated,
            )

        model_run_id = self._model_run_id(session_id=session_id, turn_id=turn_id)
        action_result = None
        if isinstance(persisted.decision.decision, ToolDecision):
            action = self._bound_action(
                session_id=session_id,
                model_run_id=model_run_id,
                decision=persisted.decision.decision,
            )
            action_result = self._tool_broker.run(action)
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
    ) -> ModelRunResult | None:
        if request.phase is not ModelPhase.EXPLORATION:
            raise ValueError("Explorer runtime accepts only exploration requests")
        timestamp = self._clock()
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
