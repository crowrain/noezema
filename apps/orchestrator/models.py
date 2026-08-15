"""Public results of one trusted-host wake attempt."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, model_validator

from packages.cognition import CuratorProposal
from packages.domain import (
    ActionId,
    BrokerRunResult,
    CheckpointId,
    ConfigSnapshotId,
    DecisionEnvelope,
    ModelRunId,
    QuestionId,
    QuestionOrigin,
    SessionBudgetUsage,
    SessionId,
    SessionLease,
    SessionState,
    ToolDecision,
    TurnId,
)
from packages.domain._base import ContractModel, NonEmptyText


class WakeSkipReason(StrEnum):
    ACTIVE_SESSION = "active_session"
    CONFIG_ACTIVATION_IN_PROGRESS = "config_activation_in_progress"
    NO_ELIGIBLE_QUESTION = "no_eligible_question"
    OPERATOR_PAUSED = "operator_paused"


class SessionStarted(ContractModel):
    status: Literal["started"] = "started"
    session_id: SessionId
    question_id: QuestionId
    question_text: NonEmptyText
    question_origin: QuestionOrigin
    config_snapshot_id: ConfigSnapshotId


class WakeSkipped(ContractModel):
    status: Literal["skipped"] = "skipped"
    reason: WakeSkipReason


WakeResult = SessionStarted | WakeSkipped


class SessionWorkKind(StrEnum):
    WAKE = "wake"
    ORIENT = "orient"
    SELECT_QUESTION = "select_question"
    PLAN = "plan"
    EXPLORE = "explore"
    VERIFY = "verify"
    STOP = "stop"
    CONSOLIDATE = "consolidate"
    REPORT = "report"
    RESUME_TURN = "resume_turn"
    RECOVER_ACTION = "recover_action"
    FINALIZE_COMMIT = "finalize_commit"
    RECONCILE_COMMIT = "reconcile_commit"
    ABORT = "abort"


class OperatorControlKind(StrEnum):
    STOP_GRACEFULLY = "stop_gracefully"
    ABORT_SESSION = "abort_session"


class SafeBoundaryKind(StrEnum):
    CONTINUE = "continue"
    ACTION_IN_FLIGHT = "action_in_flight"
    STOPPING = "stopping"
    CANCELLED = "cancelled"
    FAILED = "failed"
    SOFT_EXHAUSTED = "soft_exhausted"


class OperatorControlResult(ContractModel):
    command: OperatorControlKind
    state: SessionState
    requested_at: AwareDatetime
    newly_recorded: bool


class SafeBoundaryResult(ContractModel):
    kind: SafeBoundaryKind
    state: SessionState
    budget_usage: SessionBudgetUsage
    action_id: ActionId | None = None

    @model_validator(mode="after")
    def require_action_only_when_deferred(self) -> SafeBoundaryResult:
        if (self.kind is SafeBoundaryKind.ACTION_IN_FLIGHT) != (self.action_id is not None):
            raise ValueError("only an in-flight safe boundary carries action_id")
        return self


class SessionWorkDirective(ContractModel):
    kind: SessionWorkKind
    state: SessionState
    turn_id: TurnId | None = None
    action_id: ActionId | None = None

    @model_validator(mode="after")
    def require_exact_recovery_reference(self) -> SessionWorkDirective:
        if self.kind is SessionWorkKind.RESUME_TURN:
            if self.turn_id is None or self.action_id is not None:
                raise ValueError("resume_turn requires only turn_id")
        elif self.kind is SessionWorkKind.RECOVER_ACTION:
            if self.action_id is None or self.turn_id is not None:
                raise ValueError("recover_action requires only action_id")
        elif self.turn_id is not None or self.action_id is not None:
            raise ValueError("phase work must not carry a recovery identity")
        return self


class ClaimedSession(ContractModel):
    session_id: SessionId
    lease: SessionLease
    directive: SessionWorkDirective

    @model_validator(mode="after")
    def require_session_binding(self) -> ClaimedSession:
        if self.lease.session_id != self.session_id:
            raise ValueError("claimed lease belongs to another session")
        return self


class ExplorerTurnResult(ContractModel):
    session_id: SessionId
    turn_id: TurnId
    model_run_id: ModelRunId
    decision: DecisionEnvelope
    action_result: BrokerRunResult | None = None
    replayed_model_run: bool

    @model_validator(mode="after")
    def require_decision_effect(self) -> ExplorerTurnResult:
        expects_action = isinstance(self.decision.decision, ToolDecision)
        if expects_action != (self.action_result is not None):
            raise ValueError("tool decisions require exactly one broker result")
        return self


class CuratorTurnResult(ContractModel):
    session_id: SessionId
    turn_id: TurnId
    model_run_id: ModelRunId
    proposal: CuratorProposal
    replayed_model_run: bool


class SessionRunResult(ContractModel):
    """Terminal projection returned by one complete or recovered session run."""

    session_id: SessionId
    question_id: QuestionId | None
    terminal_state: SessionState
    checkpoint_id: CheckpointId | None = None
    model_turns: int
    tool_actions: int
    claims_committed: int
    termination_reason: str | None = None

    @model_validator(mode="after")
    def require_terminal_projection(self) -> SessionRunResult:
        if not self.terminal_state.is_terminal:
            raise ValueError("session run result must describe a terminal session")
        successful = self.terminal_state in {
            SessionState.SUCCEEDED,
            SessionState.SUCCEEDED_PARTIAL,
        }
        if successful != (self.checkpoint_id is not None):
            raise ValueError("successful session run requires exactly one checkpoint")
        return self
