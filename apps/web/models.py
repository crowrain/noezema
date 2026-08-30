"""Strict public contracts for the owner-facing read model."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, SecretStr, field_validator, model_validator

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
from packages.domain._base import ContractModel, JsonObject, NonEmptyText, ShortReason

NonNegativeInt = Annotated[int, Field(ge=0)]


class ApiRequestModel(ContractModel):
    """JSON requests parse their wire strings before strict domain construction."""

    model_config = ConfigDict(strict=False)


class LoginRequest(ApiRequestModel):
    password: SecretStr

    @field_validator("password")
    @classmethod
    def validate_password_size(cls, value: SecretStr) -> SecretStr:
        if not 1 <= len(value.get_secret_value()) <= 1024:
            raise ValueError("password must contain between 1 and 1024 characters")
        return value


class AuthSessionResponse(ContractModel):
    subject: str
    csrf_token: str
    issued_at: AwareDatetime
    expires_at: AwareDatetime


class MessageCreateRequest(ApiRequestModel):
    idempotency_key: UUID
    body: NonEmptyText
    priority: Annotated[int, Field(default=0, ge=-1000, le=1000)]
    expires_in_seconds: Annotated[
        int,
        Field(default=7 * 24 * 60 * 60, ge=60, le=30 * 24 * 60 * 60),
    ]


class MessageAcceptedResponse(ContractModel):
    id: UUID
    question_id: UUID
    state: MessageState
    newly_created: bool


class OperatorCommandCreateRequest(ApiRequestModel):
    idempotency_key: UUID
    type: OperatorCommandType
    session_id: UUID | None = None
    arguments: JsonObject = Field(default_factory=dict)
    reason: ShortReason

    @model_validator(mode="after")
    def require_target_and_argument_shape(self) -> OperatorCommandCreateRequest:
        session_scoped = self.type in {
            OperatorCommandType.STOP_GRACEFULLY,
            OperatorCommandType.ABORT_SESSION,
        }
        if session_scoped != (self.session_id is not None):
            raise ValueError("stop/abort require exactly one session target")
        argument_free = self.type in {
            OperatorCommandType.PAUSE,
            OperatorCommandType.RESUME,
            OperatorCommandType.WAKE_NOW,
            OperatorCommandType.STOP_GRACEFULLY,
            OperatorCommandType.ABORT_SESSION,
        }
        if argument_free and self.arguments:
            raise ValueError(f"{self.type.value} does not accept arguments")
        encoded_arguments = json.dumps(
            self.arguments,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        if len(encoded_arguments) > 16 * 1024:
            raise ValueError("operator command arguments exceed 16 KiB")
        return self


class OperatorCommandAcceptedResponse(ContractModel):
    id: UUID
    type: OperatorCommandType
    state: OperatorCommandState
    newly_created: bool


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
