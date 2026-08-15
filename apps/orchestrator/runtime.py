"""Fenced session phases and resumable Explorer/Curator model turns."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator.models import (
    ClaimedSession,
    CuratorTurnResult,
    ExplorerTurnResult,
    OperatorControlKind,
    OperatorControlResult,
    SafeBoundaryKind,
    SafeBoundaryResult,
    SessionWorkDirective,
    SessionWorkKind,
)
from packages.cognition import (
    CuratorContext,
    CuratorProposal,
    ExplorerContext,
    PromptBundle,
    build_curator_request,
    build_explorer_request,
    validate_curator_proposal,
    validate_explorer_decision,
)
from packages.domain import (
    ActionId,
    ActionState,
    BoundAction,
    BrokerRunResult,
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
from packages.llm_gateway import (
    GatewayRequest,
    ModelPhase,
    ModelRunResult,
    StructuredRunResult,
)
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


class CognitiveGateway(Protocol):
    def generate_decision(self, request: GatewayRequest) -> ModelRunResult: ...

    def generate_structured(
        self,
        request: GatewayRequest,
        *,
        response_model: type[CuratorProposal],
        schema_name: str,
    ) -> StructuredRunResult[CuratorProposal]: ...


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
        _record_budget_exhaustion(db, record, usage.exhaustion_reason×N¼òÚ$z{-®éÜj×ö67W'&VEöC×F–ÖW7F×À¢¢&÷'FVBÒG'VP¢VÇ6S ¢ÖöFVÅ÷'Våö–BÒÖöFVÅ'Vä–BææWr‚¢6öçFW‡EöÖæ–fW7BÒö6öçFW‡EöÖæ–fW7B‡&WVW7B¢F"æFB€¢ÖöFVÅ'Vå&V6÷&B€¢–CÖÖöFVÅ÷'Våö–Bç&ö÷BÀ¢6W76–öåö–C×6W76–öåö–Bç&ö÷BÀ¢GW&åö–C×GW&åö–Bç&ö÷BÀ¢†6S×&WVW7Bç†6RçfÇVRÀ¢ÖöFVÅöf–ævW'&–çC×&W7VÇBæÖöFVÅöf–ævW'&–çE÷6†#SbÀ¢6öçFW‡EöÖæ–fW7E÷6†#Sc×&WVW7Bæ6öçFW‡EöÖæ–fW7E÷6†#SbÀ¢6öçFW‡EöÖæ–fW7CÖ6öçFW‡EöÖæ–fW7BÀ¢&ö×E÷fW'6–öã×&WVW7Bç&ö×E÷fW'6–öâÀ¢FööÅ÷66†VÖ÷6†#Sc×&W7VÇBçFööÅ÷66†VÖ÷6†#SbÀ¢–çWE÷Fö¶Vç3×&W7VÇBçW6vRæ–çWE÷Fö¶Vç2À¢÷WGWE÷Fö¶Vç3×&W7VÇBçW6vRæ÷WGWE÷Fö¶Vç2À¢ÆFVæ7•ö×3×&W7VÇBæÆFVæ7•ö×2À¢f–æ—6…÷&V6öã×&W7VÇBæf–æ—6…÷&V6öâÀ¢÷WGWE÷66†VÖ÷fÆ–CÕG'VRÀ¢&u÷&W7öç6Uö'F–f7CÔæöæRÀ¢7&VFVEöC×F–ÖW7F×À¢¢¢F"æfÇW6‚‚¢GW&âç7FGW2Ò&6ö×ÆWFVB ¢GW&âæÖöFVÅ÷'Våö–BÒÖöFVÅ÷'Våö–Bç&ö÷@¢GW&âç&W7VÇBÒ&W7VÇBæÖöFVÅöGV×†ÖöFSÒ&§6öâ"¢GW&âæ6ö×ÆWFVEöBÒF–ÖW7F× ¢VæE÷6W76–öåöVF—B€¢F"À¢6W76–öåö–C×6W76–öåö–BÀ¢G—SÔWfVçEG—RäÔôDTÅõ%Tåô4ôÕÄUDTBÀ¢ö67W'&VEöC×F–ÖW7F×À¢7F÷#Ò&÷&6†W7G&F÷""À¢V&Æ–5÷7VÖÖ'“×&W7VÇBæFV6—6–öâçV&Æ–5÷&F–öæÆRÀ¢F÷–3Ò&VF—BæÖöFVÅ÷'Våö6ö×ÆWFVBçc"À¢–ÆöC×°¢'GW&åö–B#¢7G"‡GW&åö–B’À¢&ÖöFVÅ÷'Våö–B#¢7G"†ÖöFVÅ÷'Våö–B’À¢'†6R#¢&WVW7Bç†6RçfÇVRÀ¢&FV6—6–öåö¶–æB#¢&W7VÇBæFV6—6–öâæFV6—6–öâæ¶–æBçfÇVRÀ¢&–çWE÷Fö¶Vç2#¢&W7VÇBçW6vRæ–çWE÷Fö¶Vç2À¢&÷WGWE÷Fö¶Vç2#¢&W7VÇBçW6vRæ÷WGWE÷Fö¶Vç2À¢&ÆFVæ7•ö×2#¢&W7VÇBæÆFVæ7•ö×2À¢ÒÀ¢¢F"æfÇW6‚‚¢–b&÷'FVC ¢&—6R6W76–öä&÷'FVDW'&÷"‚&÷W&F÷"&÷'BF—66&FVBF†RVæf–æ—6†VBÖöFVÂ÷WGWB"¢&WGW&â&W7VÇ@ ¢FVböf–Å÷GW&åö–eö÷væVB€¢6VÆbÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢GW&åö–C¢GW&ä–BÀ¢W'&÷%ö6öFS¢7G"À¢’ÓâæöæS ¢F–ÖW7F×Ò6VÆbåö6Æö6²‚¢G'“ ¢v—F‚6VÆbå÷6W76–öåöf7F÷'’æ&Vv–â‚’2F# ¢ÆV6RÒ6VÆbåö7V—&R†F"Â6W76–öåö–C×6W76–öåö–BÂö67W'&VEöC×F–ÖW7F×¢GW&âÒF"ç66Æ"€¢6VÆV7B„÷&6†W7G&F÷%GW&å&V6÷&B¢çv†W&R„÷&6†W7G&F÷%GW&å&V6÷&Bæ–BÓÒGW&åö–Bç&ö÷B¢çv—F…öf÷%÷WFFR‚¢¢–b€¢GW&â—2æ÷BæöæP¢æBGW&âç7FGW2ÓÒ'&W&VB ¢æBGW&âæÆV6Uö÷væW"ÓÒÆV6Ræ÷væW ¢æBGW&âç6W76–öåöfVæ6RÓÒÆV6RæfVæ6P¢“ ¢GW&âç7FGW2Ò&f–ÆVB ¢GW&âæW'&÷%ö6öFRÒW'&÷%ö6öFU³£#…Ğ¢GW&âæ6ö×ÆWFVEöBÒF–ÖW7F× ¢F"æfÇW6‚‚¢W†6WB6öæ7W'&Væ7”6öçG&öÄW'&÷# ¢&WGW&à ¢FVböÖöFVÅ÷'Våö–B‡6VÆbÂ¢Â6W76–öåö–C¢6W76–öä–BÂGW&åö–C¢GW&ä–B’ÓâÖöFVÅ'Vä–C ¢v—F‚6VÆbå÷6W76–öåöf7F÷'’‚’2F# ¢GW&âÒF"ævWB„÷&6†W7G&F÷%GW&å&V6÷&BÂGW&åö–Bç&ö÷B¢–b€¢GW&â—2æöæP¢÷"GW&âç6W76–öåö–BÒ6W76–öåö–Bç&ö÷@¢÷"GW&âç7FGW2Ò&6ö×ÆWFVB ¢÷"GW&âæÖöFVÅ÷'Våö–B—2æöæP¢“ ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&6ö×ÆWFVBGW&â†2æòÖöFVÂ'Vâ"¢&WGW&âÖöFVÅ'Vä–B‡&ö÷C×GW&âæÖöFVÅ÷'Våö–B ¢FVbö&÷VæEö7F–öâ€¢6VÆbÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢ÖöFVÅ÷'Våö–C¢ÖöFVÅ'Vä–BÀ¢FV6—6–öã¢FööÄFV6—6–öâÀ¢’Óâ&÷VæD7F–öã ¢v—F‚6VÆbå÷6W76–öåöf7F÷'’‚’2F# ¢&V6÷&BÒF"ç66Æ"€¢6VÆV7B„7F–öå&V6÷&B’çv†W&R„7F–öå&V6÷&BæÖöFVÅ÷'Våö–BÓÒÖöFVÅ÷'Våö–Bç&ö÷B¢¢–b&V6÷&B—2æöæS ¢&WGW&â&–æEö7F–öâ€¢6W76–öåö–C×6W76–öåö–BÀ¢ÖöFVÅ÷'Våö–CÖÖöFVÅ÷'Våö–BÀ¢FV6—6–öãÖFV6—6–öâÀ¢¢7F–öâÒ&÷VæD7F–öâ€¢7F–öåö–CÔ7F–öä–B‡&ö÷C×&V6÷&Bæ–B’À¢6W76–öåö–C×6W76–öåö–BÀ¢ÖöFVÅ÷'Våö–CÖÖöFVÅ÷'Våö–BÀ¢–FV×÷FVæ7•ö¶W“Ô–FV×÷FVæ7”¶W’‡&ö÷C×&V6÷&Bæ–FV×÷FVæ7•ö¶W’’À¢–FV×÷FVæ7•ö6Æ73Ô–FV×÷FVæ7”6Æ72‡&V6÷&Bæ–FV×÷FVæ7•ö6Æ72’À¢FööÃÕFööÄæÖR‡&V6÷&BçFööÂ’À¢&wVÖVçG5ö§6öã×&V6÷&Bæ&wVÖVçG5ö§6öâÀ¢&wVÖVçG5÷6†#Sc×&V6÷&Bæ&wVÖVçG5÷6†#SbÀ¢¢W‡V7FVBÒ&–æEö7F–öâ€¢6W76–öåö–C×6W76–öåö–BÀ¢ÖöFVÅ÷'Våö–CÖÖöFVÅ÷'Våö–BÀ¢FV6—6–öãÖFV6—6–öâÀ¢¢–b€¢7F–öâçFööÂ—2æ÷BW‡V7FVBçFööÀ¢÷"7F–öâæ&wVÖVçG5ö§6öâÒW‡V7FVBæ&wVÖVçG5ö§6öà¢÷"7F–öâæ&wVÖVçG5÷6†#SbÒW‡V7FVBæ&wVÖVçG5÷6†#S`¢“ ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"€¢&ÖöFVÂ'Vâ7F–öâ6öæfÆ–7G2v—F‚—G2W'6—7FVBFV6—6–öâ ¢¢&WGW&â7F–öà ¢FVbö7V—&R€¢6VÆbÀ¢F#¢6W76–öâÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢ö67W'&VEöC¢FFWF–ÖRÀ¢’Óâ6W76–öäÆV6S ¢&WGW&â7V—&U÷6W76–öåöÆV6R€¢F"À¢6W76–öåö–C×6W76–öåö–BÀ¢÷væW#×6VÆbåöÆV6Uö÷væW"À¢GFÅ÷6V6öæG3×6VÆbåöÆV6U÷GFÅ÷6V6öæG2À¢ö67W'&VEöCÖö67W'&VEöBÀ¢ ¢FVb÷6fUö&÷VæF'’€¢6VÆbÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢ö67W'&VEöC¢FFWF–ÖRÂæöæRÒæöæRÀ¢’Óâ6fT&÷VæF'•&W7VÇC ¢F–ÖW7F×Òö67W'&VEöB÷"6VÆbåö6Æö6²‚¢v—F‚6VÆbå÷6W76–öåöf7F÷'’æ&Vv–â‚’2F# ¢ÆV6RÒ6VÆbåö7V—&R†F"Â6W76–öåö–C×6W76–öåö–BÂö67W'&VEöC×F–ÖW7F×¢&WGW&âÇ•÷6W76–öå÷6fUö&÷VæF'’†F"ÂÆV6SÖÆV6RÂö67W'&VEöC×F–ÖW7F× ¢FVböVæf÷&6U÷6fUö&÷VæF'’€¢6VÆbÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢ö67W'&VEöC¢FFWF–ÖRÂæöæRÒæöæRÀ¢’ÓâæöæS ¢6VÆbå÷&—6Uöf÷%ö&÷VæF'’€¢6VÆbå÷6fUö&÷VæF'’‡6W76–öåö–C×6W76–öåö–BÂö67W'&VEöCÖö67W'&VEöB¢ ¢FVböVæf÷&6Uöf–æÆ—¦F–öåö&÷VæF'’€¢6VÆbÀ¢¢À¢6W76–öåö–C¢6W76–öä–BÀ¢ö67W'&VEöC¢FFWF–ÖRÂæöæRÒæöæRÀ¢’ÓâæöæS ¢""$†öæ÷"&÷'Bö†÷7B6fWG’v†–ÆRÆÆ÷v–ær&W6W'fVB6öç6öÆ–FF–öâv÷&²â""  ¢&÷VæF'’Ò6VÆbå÷6fUö&÷VæF'’‡6W76–öåö–C×6W76–öåö–BÂö67W'&VEöCÖö67W'&VEöB¢–b&÷VæF'’æ¶–æB–â°¢6fT&÷VæF'”¶–æBä4ôåD”åTRÀ¢6fT&÷VæF'”¶–æBå5Dõ”ärÀ¢6fT&÷VæF'”¶–æBå4ôeEôU„„U5DTBÀ¢Ó ¢&WGW&à¢6VÆbå÷&—6Uöf÷%ö&÷VæF'’†&÷VæF'’ ¢7FF–6ÖWF†ö@¢FVb÷&—6Uöf÷%ö&÷VæF'’†&÷VæF'“¢6fT&÷VæF'•&W7VÇB’ÓâæöæS ¢–b&÷VæF'’æ¶–æB—26fT&÷VæF'”¶–æBä4ôåD”åTS ¢&WGW&à¢–b&÷VæF'’æ¶–æB—26fT&÷VæF'”¶–æBä5D”ôåô”åôdÄ”t…C ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"€¢b&7F–öâ¶&÷VæF'’æ7F–öåö–GÒ×W7B&V6‚FW&Ö–æÂ7FFR&Vf÷&RæWrv÷&² ¢¢–b&÷VæF'’æ¶–æB—26fT&÷VæF'”¶–æBå5Dõ”äs ¢&—6Rw&6VgVÅ7F÷&WVW7FVDW'&÷"‚&÷W&F÷"7F÷&V6†VB6fR&÷VæF'’"¢–b&÷VæF'’æ¶–æB—26fT&÷VæF'”¶–æBä4ä4TÄÄTC ¢&—6R6W76–öä&÷'FVDW'&÷"‚&÷W&F÷"&÷'B&V6†VB6fR&÷VæF'’"¢–b&÷VæF'’æ¶–æB—26fT&÷VæF'”¶–æBå4ôeEôU„„U5DTC ¢&V6öâÒ&÷VæF'’æ'VFvWE÷W6vRæW††W7F–öå÷&V6öà¢&—6R6ögD'VFvWDW††W7FVDW'&÷"€¢b'6ögB6W76–öâ'VFvWBW††W7FVC¢·&V6öâçfÇVR–b&V6öâVÇ6RwVæ¶æ÷vâwÒ ¢¢&—6R6W76–öä&÷VæF'”f–ÇW&TW'&÷"‚'6W76–öâf–ÆVBB6fR&÷VæF'’"  ¦FVb÷&V6÷fW'•öF—&V7F—fR€¢F#¢6W76–öâÀ¢6W76–öå÷&V6÷&C¢6W76–öå&V6÷&BÀ¢’Óâ6W76–öåv÷&´F—&V7F—fS ¢7FFRÒ6W76–öå7FFR‡6W76–öå÷&V6÷&Bç7FFR¢–b7FFR—26W76–öå7FFRä4ôÔÔ•ED”äs ¢GFV×BÒö&÷VæEö6öÖÖ—EöGFV×B†F"Â6W76–öå÷&V6÷&B¢¶–æBÒ€¢6W76–öåv÷&´¶–æBäd”äÄ•¤Uô4ôÔÔ•@¢–bGFV×Bç7FGW2ÓÒ'&W&VB ¢VÇ6R6W76–öåv÷&´¶–æBå$T4ôä4”ÄUô4ôÔÔ•@¢¢&WGW&â6W76–öåv÷&´F—&V7F—fR†¶–æCÖ¶–æBÂ7FFS×7FFR¢–b7FFR—26W76–öå7FFRå$T4ôä4”Ä”äuô4ôÔÔ•C ¢ö&÷VæEö6öÖÖ—EöGFV×B†F"Â6W76–öå÷&V6÷&B¢&WGW&â6W76–öåv÷&´F—&V7F—fR†¶–æCÕ6W76–öåv÷&´¶–æBå$T4ôä4”ÄUô4ôÔÔ•BÂ7FFS×7FFR ¢&V6÷fW&&ÆUö7F–öç2ÒGWÆR€¢F"ç66Æ'2€¢6VÆV7B„7F–öå&V6÷&B¢çv†W&R€¢7F–öå&V6÷&Bç6W76–öåö–BÓÒ6W76–öå÷&V6÷&Bæ–BÀ¢7F–öå&V6÷&Bç7FFRæ–åò…õ$T4õdU$$ÄUô5D”ôåõ5DDU2’À¢¢æ÷&FW%ö'’„7F–öå&V6÷&Bæ7&VFVEöBÂ7F–öå&V6÷&Bæ–B¢¢¢–bÆVâ‡&V6÷fW&&ÆUö7F–öç2’â ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚'6W76–öâ†2×VÇF—ÆR&V6÷fW&&ÆR7F–öç2"¢–b&V6÷fW&&ÆUö7F–öç3 ¢7F–öâÒ&V6÷fW&&ÆUö7F–öç5³Ğ¢&WGW&â6W76–öåv÷&´F—&V7F—fR€¢¶–æCÕ6W76–öåv÷&´¶–æBå$T4õdU%ô5D”ôâÀ¢7FFS×7FFRÀ¢7F–öåö–CÔ7F–öä–B‡&ö÷CÖ7F–öâæ–B’À¢ ¢–b6W76–öå÷&V6÷&Bæ&÷'E÷&WVW7FVEöB—2æ÷BæöæS ¢&WGW&â6W76–öåv÷&´F—&V7F—fR†¶–æCÕ6W76–öåv÷&´¶–æBä$õ%BÂ7FFS×7FFR¢–b€¢6W76–öå÷&V6÷&Bç7F÷÷&WVW7FVEöB—2æ÷BæöæR÷"6W76–öå÷&V6÷&Bç6ögEöW††W7FVEöB—2æ÷BæöæP¢’æB7FFR–â°¢6W76–öå7FFRåt´”ärÀ¢6W76–öå7FFRäõ$”TåD”ärÀ¢6W76–öå7FFRå4TÄT5D”äuõTU5D”ôâÀ¢6W76–öå7FFRåÄää”ärÀ¢6W76–öå7FFRäU…Äõ$”ärÀ¢6W76–öå7FFRådU$”e””ärÀ¢6W76–öå7FFRå5Dõ”ärÀ¢Ó ¢&WGW&â6W76–öåv÷&´F—&V7F—fR†¶–æCÕ6W76–öåv÷&´¶–æBå5DõÂ7FFS×7FFR ¢ÆFW7E÷GW&âÒF"ç66Æ"€¢6VÆV7B„÷&6†W7G&F÷%GW&å&V6÷&B¢çv†W&R„÷&6†W7G&F÷%GW&å&V6÷&Bç6W76–öåö–BÓÒ6W76–öå÷&V6÷&Bæ–B¢æ÷&FW%ö'’„÷&6†W7G&F÷%GW&å&V6÷&Bæ÷&F–æÂæFW62‚’¢æÆ–Ö—Bƒ¢¢–bÆFW7E÷GW&â—2æ÷BæöæRæBÆFW7E÷GW&âç7FGW2ÓÒ'&W&VB# ¢&WGW&â6W76–öåv÷&´F—&V7F—fR€¢¶–æCÕ6W76–öåv÷&´¶–æBå$U5TÔUõEU$âÀ¢7FFS×7FFRÀ¢GW&åö–CÕGW&ä–B‡&ö÷CÖÆFW7E÷GW&âæ–B’À¢¢–b€¢ÆFW7E÷GW&â—2æ÷BæöæP¢æBÆFW7E÷GW&âç7FGW2ÓÒ&6ö×ÆWFVB ¢æB7FFR—26W76–öå7FFRäU…Äõ$”äp¢“ ¢–bÆFW7E÷GW&âç&W7VÇB—2æöæS ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&6ö×ÆWFVBGW&â†2æò&W7VÇB"¢FV6—6–öâÒöÖöFVÅ÷'Vå÷&W7VÇB†ÆFW7E÷GW&âç&W7VÇB’æFV6—6–öâæFV6—6–öà¢–bæ÷B—6–ç7Fæ6R†FV6—6–öâÂFööÄFV6—6–öâ“ ¢&WGW&â6W76–öåv÷&´F—&V7F—fR€¢¶–æCÕ6W76–öåv÷&´¶–æBå$U5TÔUõEU$âÀ¢7FFS×7FFRÀ¢GW&åö–CÕGW&ä–B‡&ö÷CÖÆFW7E÷GW&âæ–B’À¢¢–bÆFW7E÷GW&âæÖöFVÅ÷'Våö–B—2æöæS ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&6ö×ÆWFVBGW&â†2æòÖöFVÂ'Vâ"¢7F–öâÒF"ç66Æ"€¢6VÆV7B„7F–öå&V6÷&B’çv†W&R„7F–öå&V6÷&BæÖöFVÅ÷'Våö–BÓÒÆFW7E÷GW&âæÖöFVÅ÷'Våö–B¢¢–b7F–öâ—2æöæS ¢&WGW&â6W76–öåv÷&´F—&V7F—fR€¢¶–æCÕ6W76–öåv÷&´¶–æBå$U5TÔUõEU$âÀ¢7FFS×7FFRÀ¢GW&åö–CÕGW&ä–B‡&ö÷CÖÆFW7E÷GW&âæ–B’À¢ ¢¶–æBÒõ„4Uõtõ$²ævWB‡7FFR¢–b¶–æB—2æöæS ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"†b'6W76–öâ7FFR†2æò&V6÷fW'’v÷&³¢·7FFWÒ"¢&WGW&â6W76–öåv÷&´F—&V7F—fR†¶–æCÖ¶–æBÂ7FFS×7FFR  ¦FVbö&÷VæEö6öÖÖ—EöGFV×B€¢F#¢6W76–öâÀ¢6W76–öå÷&V6÷&C¢6W76–öå&V6÷&BÀ¢’Óâ6öÖÖ—DGFV×E&V6÷&C ¢–b6W76–öå÷&V6÷&Bæ6öÖÖ—EöGFV×Eö–B—2æöæS ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&6öÖÖ—B7FFR†2æò&÷VæBGFV×B"¢GFV×BÒF"ævWB„6öÖÖ—DGFV×E&V6÷&BÂ6W76–öå÷&V6÷&Bæ6öÖÖ—EöGFV×Eö–B¢–bGFV×B—2æöæR÷"GFV×Bç6W76–öåö–BÒ6W76–öå÷&V6÷&Bæ–C ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&&÷VæB6öÖÖ—BGFV×B—2Ö—76–ær"¢&WGW&âGFV×@  ¦FVbö6öçFW‡EöÖæ–fW7B‡&WVW7C¢vFWv•&WVW7B’ÓâF–7E·7G"Âö&¦V7EÓ ¢G'“ ¢–ÆöBÒ§6öâæÆöG2‡&WVW7BæÖW76vW5²ÓÒæ6öçFVçB¢W†6WB„–æFW„W'&÷"Â§6öâä¥4ôäFV6öFTW'&÷"’2W†3 ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"€¢&vFWv’&WVW7B6öçFW‡B—2æ÷B6æöæ–6Â¥4ôâ ¢’g&öÒW†0¢–bæ÷B—6–ç7Fæ6R‡–ÆöBÂF–7B“ ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚&vFWv’&WVW7B6öçFW‡B—2æ÷Bâö&¦V7B"¢&WGW&â–Æö@  ¦FVböW‡Æ÷&W%ö6öçFW‡B‡&WVW7C¢vFWv•&WVW7B’ÓâW‡Æ÷&W$6öçFW‡C ¢–ÆöBÒö6öçFW‡EöÖæ–fW7B‡&WVW7B¢–b–ÆöBævWB‚'&÷Fö6öÂ"’Ò&W‡Æ÷&W"Ö–çWB÷c"# ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚'GW&â—2æ÷BâW‡Æ÷&W"&÷Fö6öÂ&WVW7B"¢G'“ ¢6öçFW‡BÒ–ÆöE²&6öçFW‡B%Ğ¢–bæ÷B—6–ç7Fæ6R†6öçFW‡BÂF–7B“ ¢&—6RfÇVTW'&÷"‚$W‡Æ÷&W"6öçFW‡B—2æ÷Bâö&¦V7B"¢&WGW&âW‡Æ÷&W$6öçFW‡BæÖöFVÅ÷fÆ–FFUö§6öâ…ö6æöæ–6Åö§6öâ†6öçFW‡B’¢W†6WB„¶W”W'&÷"ÂfÇVTW'&÷"’2W†3 ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚'7F÷&VBW‡Æ÷&W"6öçFW‡B—2–çfÆ–B"’g&öÒW†0  ¦FVbö7W&F÷%ö6öçFW‡B‡&WVW7C¢vFWv•&WVW7B’Óâ7W&F÷$6öçFW‡C ¢–ÆöBÒö6öçFW‡EöÖæ–fW7B‡&WVW7B¢–b–ÆöBævWB‚'&÷Fö6öÂ"’Ò&7W&F÷"Ö–çWB÷c"# ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚'GW&â—2æ÷B7W&F÷"&÷Fö6öÂ&WVW7B"¢G'“ ¢6öçFW‡BÒ–ÆöE²&6öçFW‡B%Ğ¢–bæ÷B—6–ç7Fæ6R†6öçFW‡BÂF–7B“ ¢&—6RfÇVTW'&÷"‚$7W&F÷"6öçFW‡B—2æ÷Bâö&¦V7B"¢&WGW&â7W&F÷$6öçFW‡BæÖöFVÅ÷fÆ–FFUö§6öâ…ö6æöæ–6Åö§6öâ†6öçFW‡B’¢W†6WB„¶W”W'&÷"ÂfÇVTW'&÷"’2W†3 ¢&—6R–æ6öç6—7FVçE6W76–öå'VçF–ÖTW'&÷"‚'7F÷&VB7W&F÷"6öçFW‡B—2–çfÆ–B"’g&öÒW†0  ¦FVbövFWv•÷&WVW7B‡fÇVS¢F–7E·7G"Âö&¦V7EÒ’ÓâvFWv•&WVW7C ¢&WGW&âvFWv•&WVW7BæÖöFVÅ÷fÆ–FFUö§6öâ…ö6æöæ–6Åö§6öâ‡fÇVR’  ¦FVböÖöFVÅ÷'Vå÷&W7VÇB‡fÇVS¢F–7E·7G"Âö&¦V7EÒ’ÓâÖöFVÅ'Vå&W7VÇC ¢&WGW&âÖöFVÅ'Vå&W7VÇBæÖöFVÅ÷fÆ–FFUö§6öâ…ö6æöæ–6Åö§6öâ‡fÇVR’  ¦FVbö7W&F÷%÷'Vå÷&W7VÇB€¢fÇVS¢F–7E·7G"Âö&¦V7EÒÀ¢’Óâ7G'V7GW&VE'Vå&W7VÇE´7W&F÷%&÷÷6ÅÓ ¢&WGW&â7G'V7GW&VE'Vå&W7VÇE´7W&F÷%&÷÷6ÅÒæÖöFVÅ÷fÆ–FFUö§6öâ…ö6æöæ–6Åö§6öâ‡fÇVR’  ¦FVbö6æöæ–6Åö§6öâ‡fÇVS¢F–7E·7G"Âö&¦V7EÒ’Óâ7G# ¢&WGW&â§6öâæGV×2€¢fÇVRÀ¢ÆÆ÷uöæãÔfÇ6RÀ¢Vç7W&Uö66–“ÔfÇ6RÀ¢6W&F÷'3Ò‚"Â"Â#¢"’À¢6÷'Eö¶W—3ÕG'VRÀ¢  ¦FVböv&R‡fÇVS¢FFWF–ÖR’ÓâFFWF–ÖS ¢–bfÇVRçG¦–æfò—2æöæS ¢&WGW&âfÇVRç&WÆ6R‡G¦–æfóÕUD2¢&WGW&âfÇVRæ7F–ÖW¦öæR…UD2