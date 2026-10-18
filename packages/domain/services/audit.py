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
from packages.domain.sanitization import mask_nul, mask_nul_deep

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
        # T7.46a (SMOKE-V12-K2 §3.4): the AUDIT boundary — the last line
        # of defense before the JSONB/TEXT code. A NUL byte anywhere in
        # the payload (or the summary) made asyncpg encode the parameter
        # with a ``\\u0000`` escape, which the server's JSONB parser
        # rejects — and the error took down the WHOLE caller transaction
        # (amplification: one NUL → all steps of the session lost). Mask
        # it visibly here so any FUTURE NUL source (model text, a new
        # payload field) cannot do that again.
        if payload is not None:
            payload = mask_nul_deep(payload)
        if public_summary is not None:
            public_summary = mask_nul(public_summary)
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
