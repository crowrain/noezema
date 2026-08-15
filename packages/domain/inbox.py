"""Strict contracts for untrusted messages and typed operator commands."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, Field, model_validator

from packages.domain._base import ContractModel, JsonObject, NonEmptyText, ShortReason
from packages.domain.enums import MessageState, OperatorCommandState, OperatorCommandType
from packages.domain.ids import (
    InboxIdempotencyKey,
    MessageId,
    OperatorCommandId,
    QuestionId,
    SessionId,
)


class UserMessageDraft(ContractModel):
    """Immutable human input admitted by the trusted Command boundary."""

    id: MessageId
    idempotency_key: InboxIdempotencyKey
    sender: ShortReason
    body: NonEmptyText
    priority: int = Field(default=0, ge=-1000, le=1000)
    expires_at: AwareDatetime
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_future_expiry(self) -> UserMessageDraft:
        if self.expires_at <= self.created_at:
            raise ValueError("message expiry must be after creation")
        return self

    @classmethod
    def new(
        cls,
        *,
        idempotency_key: InboxIdempotencyKey,
        sender: str,
        body: str,
        expires_at: datetime,
        created_at: datetime,
        priority: int = 0,
    ) -> Self:
        return cls(
            id=MessageId.new(),
            idempotency_key=idempotency_key,
            sender=sender,
            body=body,
            priority=priority,
            expires_at=expires_at,
            created_at=created_at,
        )


class QueuedMessage(ContractModel):
    id: MessageId
    question_id: QuestionId
    state: MessageState
    newly_created: bool


class DeliveredMessage(ContractModel):
    id: MessageId
    body: NonEmptyText
    priority: int = Field(ge=-1000, le=1000)
    delivered_at: AwareDatetime


class OperatorCommandDraft(ContractModel):
    """Typed operator intent; free text can never select ``type``."""

    id: OperatorCommandId
    idempotency_key: InboxIdempotencyKey
    actor_id: ShortReason
    type: OperatorCommandType
    session_id: SessionId | None = None
    arguments: JsonObject = Field(default_factory=dict)
    reason: ShortReason
    created_at: AwareDatetime

    @model_validator(mode="after")
    def require_target_shape(self) -> OperatorCommandDraft:
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
        return self

    @classmethod
    def new(
        cls,
        *,
        idempotency_key: InboxIdempotencyKey,
        actor_id: str,
        type: OperatorCommandType,
        reason: str,
        created_at: datetime,
        session_id: SessionId | None = None,
        arguments: JsonObject | None = None,
    ) -> Self:
        return cls(
            id=OperatorCommandId.new(),
            idempotency_key=idempotency_key,
            actor_id=actor_id,
            type=type,
            session_id=session_id,
            arguments=arguments or {},
            reason=reason,
            created_at=created_at,
        )


class OperatorCommandResult(ContractModel):
    id: OperatorCommandId
    type: OperatorCommandType
    state: OperatorCommandState
    newly_created: bool = False
    result: JsonObject | None = None
