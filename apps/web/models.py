"""Strict public contracts for the owner-facing read model."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    AwareDatetime,
    ConfigDict,
    Field,
    SecretStr,
    StringConstraints,
    field_validator,
    model_validator,
)

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
UnitName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=256,
        pattern=r"^[A-Za-z0-9_.@:-]+$",
    ),
]


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


class SchedulerStatusProjection(ContractModel):
    """Secret-free durable scheduler state exposed to the owner."""

    busy: bool
    wake_generation: NonNegativeInt
    handled_wake_generation: NonNegativeInt
    next_scheduled_at: AwareDatetime | None
    backoff_until: AwareDatetime | None
    consecutive_failures: NonNegativeInt
    last_session_id: UUID | None
    last_terminal_state: SessionState | None
    last_error_class: Annotated[str | None, Field(max_length=128)]

    @model_validator(mode="after")
    def handled_generation_cannot_exceed_requested(self) -> SchedulerStatusProjection:
        if self.handled_wake_generation > self.wake_generation:
            raise ValueError("handled wake generation exceeds the requested generation")
        return self


class NodeStatusProjection(ContractModel):
    node_state: NodeState
    activity: NodeActivity
    active_session: SessionProjection | None
    scheduler: SchedulerStatusProjection
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


class RuntimeMode(StrEnum):
    NORMAL = "normal"
    DEGRADED = "degraded"


class DegradedReason(StrEnum):
    UNIT_STATE_MISSING = "unit_state_missing"
    UNIT_STATE_INVALID = "unit_state_invalid"
    UNIT_STATE_STALE = "unit_state_stale"
    BOOT_ID_MISMATCH = "boot_id_mismatch"
    UNIT_STATE_PUBLISHER_FAILED = "unit_state_publisher_failed"
    RUNTIME_INACTIVE = "runtime_inactive"
    RUNTIME_MEMBER_UNHEALTHY = "runtime_member_unhealthy"
    MAINTENANCE_ACTIVE = "maintenance_active"
    HOST_TRANSITION_IN_PROGRESS = "host_transition_in_progress"
    HOST_POLICY_CHANGE_IN_PROGRESS = "host_policy_change_in_progress"
    DATABASE_UNAVAILABLE = "database_unavailable"


class HostUnitProjection(ContractModel):
    name: UnitName
    active_state: UnitName
    sub_state: UnitName
    result: UnitName


class HostStatusProjection(ContractModel):
    snapshot_state: Literal["current", "missing", "invalid", "stale"]
    snapshot_observed_at: AwareDatetime | None
    snapshot_age_seconds: float | None = Field(default=None, ge=0)
    boot_id: UUID | None
    target: HostUnitProjection | None
    members: tuple[HostUnitProjection, ...]
    maintenance_active: bool
    host_transition_active: bool
    host_policy_change_active: bool
    reasons: tuple[DegradedReason, ...]

    @model_validator(mode="after")
    def snapshot_shape_is_consistent(self) -> HostStatusProjection:
        has_snapshot = self.snapshot_state in {"current", "stale"}
        complete = (
            self.snapshot_observed_at is not None
            and self.snapshot_age_seconds is not None
            and self.boot_id is not None
            and self.target is not None
        )
        if has_snapshot != complete:
            raise ValueError("current and stale host snapshots require complete unit state")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("degraded reasons must be unique")
        required_snapshot_reason = {
            "missing": {DegradedReason.UNIT_STATE_MISSING},
            "invalid": {
                DegradedReason.UNIT_STATE_INVALID,
                DegradedReason.BOOT_ID_MISMATCH,
            },
            "stale": {DegradedReason.UNIT_STATE_STALE},
        }.get(self.snapshot_state)
        if required_snapshot_reason is not None and not required_snapshot_reason.intersection(
            self.reasons
        ):
            raise ValueError("non-current snapshot requires its degraded reason")
        return self


class SystemStatusProjection(ContractModel):
    mode: RuntimeMode
    command_api_enabled: bool
    reasons: tuple[DegradedReason, ...]
    host: HostStatusProjection
    operational: NodeStatusProjection | None
    observed_at: AwareDatetime

    @model_validator(mode="after")
    def availability_shape_is_consistent(self) -> SystemStatusProjection:
        expected_reasons = tuple(
            dict.fromkeys(
                (
                    *self.host.reasons,
                    *((DegradedReason.DATABASE_UNAVAILABLE,) if self.operational is None else ()),
                )
            )
        )
        if self.reasons != expected_reasons:
            raise ValueError("system reasons must exactly describe host and database state")
        healthy = not expected_reasons and self.operational is not None
        if (self.mode is RuntimeMode.NORMAL) != healthy:
            raise ValueError("normal mode requires healthy host and operational state")
        if self.command_api_enabled != healthy:
            raise ValueError("Command API must fail closed outside normal mode")
        if len(set(self.reasons)) != len(self.reasons):
            raise ValueError("degraded reasons must be unique")
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


class TimelineStreamEventProjection(TimelineEventProjection):
    """One public audit projection at its durable cross-session stream position."""

    stream_sequence: Annotated[int, Field(ge=1)]


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
