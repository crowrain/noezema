"""Audit / outbox repositories (T1.5)."""

from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.events import ORMAuditEvent, ORMOutboxEvent


class AuditEventRepository:
    @staticmethod
    async def next_sequence(db: AsyncSession, session_id: uuid.UUID | None) -> int:
        """Per-session monotonic sequence (§14.4: UNIQUE(session_id, sequence)).

        Out-of-session events (session_id IS NULL) are sequenced in their
        own scope; Postgres treats NULLs as distinct in the unique index,
        so the counter is kept per scope here.
        """
        stmt = select(func.coalesce(func.max(ORMAuditEvent.sequence), 0)).where(
            ORMAuditEvent.session_id == session_id
        )
        return (await db.execute(stmt)).scalar_one() + 1

    @staticmethod
    async def create(db: AsyncSession, event: ORMAuditEvent) -> ORMAuditEvent:
        db.add(event)
        await db.flush()
        return event

    @staticmethod
    async def list_for_session(db: AsyncSession, session_id: uuid.UUID, limit: int = 200) -> list[ORMAuditEvent]:
        stmt = (
            select(ORMAuditEvent)
            .where(ORMAuditEvent.session_id == session_id)
            .order_by(ORMAuditEvent.sequence.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def list_timeline(db: AsyncSession, limit: int = 200) -> list[ORMAuditEvent]:
        """Committed operational timeline (newest first, §13.1)."""
        stmt = (
            select(ORMAuditEvent)
            .order_by(ORMAuditEvent.occurred_at.desc(), ORMAuditEvent.id)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


class OutboxRepository:
    @staticmethod
    async def create(db: AsyncSession, outbox: ORMOutboxEvent) -> ORMOutboxEvent:
        db.add(outbox)
        await db.flush()
        return outbox

    @staticmethod
    async def list_unpublished(db: AsyncSession, limit: int = 100) -> list[ORMOutboxEvent]:
        stmt = (
            select(ORMOutboxEvent)
            .where(ORMOutboxEvent.published_at.is_(None))
            .order_by(ORMOutboxEvent.created_at.asc())
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def mark_published(db: AsyncSession, outbox_id: uuid.UUID) -> None:
        outbox = await db.get(ORMOutboxEvent, outbox_id)
        if outbox is None:
            raise LookupError(f"outbox event {outbox_id} not found")
        outbox.published_at = func.now()
        await db.flush()
