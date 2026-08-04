"""Versioned immutable audit event contract."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import AwareDatetime, Field, PositiveInt

from packages.domain._base import ContractModel, JsonObject, NonEmptyText, ShortReason
from packages.domain.enums import EventType
from packages.domain.ids import AuditEventId, SessionId


class AuditEvent(ContractModel):
    """An immutable event ordered uniquely within an optional session."""

    id: AuditEventId
    session_id: SessionId | None
    sequence: PositiveInt
    type: EventType
    schema_version: Literal[1] = 1
    occurred_at: AwareDatetime
    actor: ShortReason
    public_summary: NonEmptyText
    payload: JsonObject = Field(default_factory=dict)

    @classmethod
    def new(
        cls,
        *,
        session_id: SessionId | None,
        sequence: int,
        type: EventType,
        occurred_at: datetime,
        actor: str,
        public_summary: str,
        payload: JsonObject | None = None,
    ) -> AuditEvent:
        """Create the event ID in the trusted host boundary."""

        return cls(
            id=AuditEventId.new(),
            session_id=session_id,
            sequence=sequence,
            type=type,
            occurred_at=occurred_at,
            actor=actor,
            public_summary=public_summary,
            payload=payload or {},
        )
