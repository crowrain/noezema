"""Strict public contracts for the owner-facing read model."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from packages.domain import (
    EventType,
    MessageState,
    NodeState,
    OperatorCommandState,
    OperatorCommandType,
    QuestionOrigin,
    SessionBudget,
    SessionState,
)
from packages.domain._base import ContractModel, NonEmptyText, ShortReason

NonNegativeInt = Annotated[int, Field(ge=0)]


class NodeActivity(StrEnum):
    """Whether the single-session orchestrator currently has admitted work."""

    IDLE = "idle"
    RUNNING = "running"


class SessionUsageProjection(ContractModel):
    model_turns: NonNegativeInt
    tool_actions: NonNegativeInt
    input_tokens: NonNegativeInt
    output_tokens: NonNegativeInt


class SessionProjection(ContractModel):
    id: UUID
    state: SessionState
    config_snapshot_id: UUID
    question_id: UUID | None
    question_text: NonEmptyText | None
    question_origin: QuestionOrigin | None
    budget: SessionBudget
    usage: SessionUsageProjection
    cognitive_deadline_at: AwareDatetime
    host_deadline_at: AwareDatetime
    stop_requested_at: AwareDatetime | None
    abort_requested_at: AwareDatetime | None
    termination_reason: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    terminal_at: AwareDatetime | None

    @model_validator(mode="after")
    def require_complete_question_projection(self) -> SessionProjection:
        question_values = (self.question_id, self.question_text, self.question_origin)
        if any(value is None for value in question_values) and any(
            value is not None for value in question_values
        ):
            raise ValueError("question fields must be all present or all absent")
        return self


class NodeStatusProjection(ContractModel):
    node_state: NodeState
    activity: NodeActivity
    active_session: SessionProjection | None
    queued_questions: NonNegativeInt
    pending_messages: NonNegativeInt
    pending_commands: NonNegativeInt
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def activity_must_match_session(self) -> NodeStatusProjection:
        expected = NodeActivity.RUNNING if self.active_session is not None else NodeActivity.IDLE
        if self.activity is not expected:
            raise ValueError("node activity must match active_session")
        return self


class TimelineEventProjection(ContractModel):
    id: UUID
    session_id: UUID | None
    sequence: Annotated[int, Field(ge=1)]
    type: EventType
    schema_version: Annotated[int, Field(ge=1)]
    occurred_at: AwareDatetime
    actor: ShortReason
    summary: NonEmptyText


class MessageProjection(ContractModel):
    id: UUID
    question_id: UUID
    sender: ShortReason
    body: NonEmptyText
    priority: Annotated[int, Field(ge=-1000, le=1000)]
    state: MessageState
    expires_at: AwareDatetime
    created_at: AwareDatetime
    delivered_session_id: UUID | None
    delivered_at: AwareDatetime | None
    acknowledged_at: AwareDatetime | None
    answered_at: AwareDatetime | None
    response_audit_event_id: UUID | None


class OperatorCommandProjection(ContractModel):
    id: UUID
    actor_id: ShortReason
    type: OperatorCommandType
    session_id: UUID | None
    state: OperatorCommandState
    reason: ShortReason
    error_code: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    finished_at: AwareDatetime | None


class TimelinePage(ContractModel):
    items: tuple[TimelineEventProjection, ...]
    next_cursor: str | None


class SessionPage(ContractModel):
    items: tuple[SessionProjection, ...]
    next_cursor: str | None


class MessagePage(ContractModel):
    items: tuple[MessageProjection, ...]
    next_cursor: str | None


class OperatorCommandPage(ContractModel):
    items: tuple[OperatorCommandProjection, ...]
    next_cursor: str | None
