"""Public results of one trusted-host wake attempt."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

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


class SupervisorTrigger(StrEnum):
    SCHEDULED = "scheduled"
    WAKE_NOW = "wake_now"
    RECOVERY = "recovery"


class SupervisorTickStatus(StrEnum):
    IDLE = "idle"
    SKIPPED = "skipped"
    SESSION_COMPLETED = "session_completed"
    ERROR = "error"


class SupervisorSkipReason(StrEnum):
    NOT_DUE = "not_due"
    SCHEDULER_BUSY = "scheduler_busy"
    BACKOFF = "backoff"
    RESOURCE_UNAVAILABLE = "resource_unavailable"
    UNRESOLVED_COMMIT = "unresolved_commit"
    CONFIG_ACTIVATION_IN_PROGRESS = "config_activation_in_progress"
    OPERATOR_PAUSED = "operator_paused"
    NO_ELIGIBLE_QUESTION = "no_eligible_question"
    ACTIVE_SESSION = "active_session"


class SupervisorTickResult(ContractModel):
    """Public, secret-free result of one autonomous supervisor tick."""

    status: SupervisorTickStatus
    observed_at: AwareDatetime
    trigger: SupervisorTrigger | None = None
    skip_reason: SupervisorSkipReason | None = None
    session_id: SessionId | None = None
    terminal_state: SessionState | None = None
    error_class: str | None = Field(default=None, min_length=1, max_length=128)
    handled_wake_generation: int = Field(ge=0)
    next_scheduled_at: AwareDatetime | None = None
    backoff_until: AwareDatetime | None = None
    consecutive_failures: int = Field(ge=0)

    @model_validator(mode="after")
    def require_status_shape(self) -> SupervisorTickResult:
        if (self.status is SupervisorTickStatus.SKIPPED) != (self.skip_reason is not None):
            raise ValueError("only a skipped tick carries skip_reason")
        completed = self.status is SupervisorTickStatus.SESSION_COMPLETED
        has_session_id = self.session_id is not None
        has_terminal_state = self.terminal_state is not None
        if completed != (has_session_id and has_terminal_state):
            raise ValueError("a completed tick requires exactly one terminal session")
        if not completed and (has_session_id or has_terminal_state):
            raise ValueError("only a completed tick carries a terminal session")
        if self.terminal_state is not None and not self.terminal_state.is_terminal:
            raise ValueError("supervisor result must not expose a non-terminal outcome")
        if (self.status is SupervisorTickStatus.ERROR) != (self.error_class is not None):
            raise ValueError("only an error tick carries error_class")
        return self


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
