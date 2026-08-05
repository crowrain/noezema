"""Async repository layer — CRUD operations over SQLAlchemy ORM models.

All methods accept an async session from Database.get_session_factory().
No transaction management here — callers control commit/rollback.
"""

from typing import Sequence
import uuid

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.orm_session import (
    ORMSession,
    ORMQuestion,
    ORMAction,
    ORMModelRun,
    ORMAuditEvent,
    ORMMessage,
)


class SessionRepository:
    """CRUD for cognitive sessions."""

    @staticmethod
    async def create(session: AsyncSession, orm_session: ORMSession) -> ORMSession:
        session.add(orm_session)
        await session.flush()
        return orm_session

    @staticmethod
    async def get_by_id(session: AsyncSession, session_id: uuid.UUID) -> ORMSession | None:
        result = await session.execute(
            select(ORMSession).where(ORMSession.id == session_id)
        )
        return result.scalar_one_or_none()

    @staticmethod
    async def list_by_state(
        session: AsyncSession, state: str, limit: int = 50
    ) -> Sequence[ORMSession]:
        result = await session.execute(
            select(ORMSession)
            .where(ORMSession.state == state)
            .order_by(ORMSession.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def count_by_state(session: AsyncSession, state: str) -> int:
        result = await session.execute(
            select(func.count()).select_from(ORMSession).where(ORMSession.state == state)
        )
        return result.scalar_one() or 0

    @staticmethod
    async def update_state(
        session: AsyncSession, session_id: uuid.UUID, new_state: str
    ) -> ORMSession | None:
        orm = await SessionRepository.get_by_id(session, session_id)
        if orm is not None:
            orm.state = new_state
            await session.flush()
        return orm

    @staticmethod
    async def lease(
        session: AsyncSession,
        session_id: uuid.UUID,
        owner: str,
        expires_at,
    ) -> ORMSession | None:
        orm = await SessionRepository.get_by_id(session, session_id)
        if orm is not None:
            orm.lease_owner = owner
            orm.lease_expires_at = expires_at
            orm.last_heartbeat_at = expires_at
            await session.flush()
        return orm


class QuestionRepository:
    """CRUD for research questions."""

    @staticmethod
    async def create(session: AsyncSession, question: ORMQuestion) -> ORMQuestion:
        session.add(question)
        await session.flush()
        return question

    @staticmethod
    async def list_unresolved(session: AsyncSession, limit: int = 20) -> Sequence[ORMQuestion]:
        result = await session.execute(
            select(ORMQuestion)
            .where(ORMQuestion.resolved == False)  # noqa: E712
            .order_by(ORMQuestion.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def mark_resolved(session: AsyncSession, question_id: uuid.UUID) -> ORMQuestion | None:
        q = await session.get(ORMQuestion, question_id)
        if q is not None:
            q.resolved = True
            await session.flush()
        return q


class ActionRepository:
    """CRUD for tool actions within sessions."""

    @staticmethod
    async def create(session: AsyncSession, action: ORMAction) -> ORMAction:
        session.add(action)
        await session.flush()
        return action

    @staticmethod
    async def list_by_session(
        session: AsyncSession, session_id: uuid.UUID, limit: int = 100
    ) -> Sequence[ORMAction]:
        result = await session.execute(
            select(ORMAction)
            .where(ORMAction.session_id == session_id)
            .order_by(ORMAction.step.asc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def next_step_for_session(session: AsyncSession, session_id: uuid.UUID) -> int:
        result = await session.execute(
            select(func.coalesce(func.max(ORMAction.step), -1)).where(
                ORMAction.session_id == session_id
            )
        )
        return result.scalar_one() + 1

    @staticmethod
    async def update_status(
        session: AsyncSession, action_id: uuid.UUID, status: str, result: str | None = None
    ) -> ORMAction | None:
        action = await session.get(ORMAction, action_id)
        if action is not None:
            action.status = status
            if result is not None:
                action.result = result
            await session.flush()
        return action


class ModelRunRepository:
    """CRUD for LLM call records."""

    @staticmethod
    async def create(session: AsyncSession, run: ORMModelRun) -> ORMModelRun:
        session.add(run)
        await session.flush()
        return run


class AuditEventRepository:
    """Append-only audit log."""

    @staticmethod
    async def create(session: AsyncSession, event: ORMAuditEvent) -> ORMAuditEvent:
        session.add(event)
        await session.flush()
        return event

    @staticmethod
    async def list_by_session(
        session: AsyncSession, session_id: uuid.UUID, limit: int = 100
    ) -> Sequence[ORMAuditEvent]:
        result = await session.execute(
            select(ORMAuditEvent)
            .where(ORMAuditEvent.session_id == session_id)
            .order_by(ORMAuditEvent.created_at.desc())
            .limit(limit)
        )
        return result.scalars().all()


class MessageRepository:
    """CRUD for human messages."""

    @staticmethod
    async def create(session: AsyncSession, msg: ORMMessage) -> ORMMessage:
        session.add(msg)
        await session.flush()
        return msg

    @staticmethod
    async def list_queued(session: AsyncSession, limit: int = 50) -> Sequence[ORMMessage]:
        result = await session.execute(
            select(ORMMessage)
            .where(ORMMessage.state == "queued")
            .order_by(ORMMessage.created_at.asc())
            .limit(limit)
        )
        return result.scalars().all()

    @staticmethod
    async def mark_processed(session: AsyncSession, msg_id: uuid.UUID) -> ORMMessage | None:
        msg = await session.get(ORMMessage, msg_id)
        if msg is not None:
            msg.state = "processed"
            await session.flush()
        return msg
