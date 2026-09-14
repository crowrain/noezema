"""Audit service: audit_events + outbox_events in one transaction (T1.6, §3.3).

The audit log is the immutable, explainable history; the outbox carries the
same event to SSE/projectors after commit. Both rows are written in the
caller's transaction, so a committed domain change always has its audit
record and vice versa.
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType, AuditVisibility
from packages.domain.models.events import ORMAuditEvent, ORMOutboxEvent
from packages.domain.repositories.events import AuditEventRepository, OutboxRepository

OUTBOX_TOPIC_AUDIT = "audit.events"


class AuditService:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self,
        event_type: AuditEventType | str,
        *,
        session_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        actor: str | None = None,
        public_summary: str | None = None,
        visibility: AuditVisibility = AuditVisibility.OPERATOR,
    ) -> ORMAuditEvent:
        """Record one event + its outbox twin (same transaction)."""
        # closed registry: reject unknown types (§14.4)
        resolved = event_type if isinstance(event_type, AuditEventType) else AuditEventType(event_type)

        sequence = await AuditEventRepository.next_sequence(self.db, session_id)
        type_value = resolved.value
        event = ORMAuditEvent(
            session_id=session_id,
            sequence=sequence,
            type=type_value,
            actor=actor,
            public_summary=public_summary,
            payload=payload,
            visibility=visibility.value,
        )
        await AuditEventRepository.create(self.db, event)

        outbox = ORMOutboxEvent(
            audit_event_id=event.id,
            topic=OUTBOX_TOPIC_AUDIT,
            payload={
                "event_id": str(event.id),
                "session_id": str(session_id) if session_id is not None else None,
                "sequence": sequence,
                "type": type_value,
                "actor": actor,
                "public_summary": public_summary,
                "payload": payload,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            },
        )
        await OutboxRepository.create(self.db, outbox)
        return event
