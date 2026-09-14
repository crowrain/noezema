"""Session / model-run / action repositories (T1.5)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import SessionState
from packages.domain.models.sessions import ORMAction, ORMModelRun, ORMSession


class SessionRepository:
    @staticmethod
    async def create(db: AsyncSession, session: ORMSession) -> ORMSession:
        db.add(session)
        await db.flush()
        return session

    @staticmethod
    async def get(db: AsyncSession, session_id: uuid.UUID) -> ORMSession | None:
        return await db.get(ORMSession, session_id)

    @staticmethod
    async def set_state(db: AsyncSession, session_id: uuid.UUID, state: SessionState) -> None:
        session = await db.get(ORMSession, session_id)
        if session is None:
            raise LookupError(f"session {session_id} not found")
        session.state = state.value
        await db.flush()

    @staticmethod
    async def list_nonterminal(db: AsyncSession) -> list[ORMSession]:
        terminal = tuple(s.value for s in SessionState if s.is_terminal)
        stmt = select(ORMSession).where(ORMSession.state.not_in(terminal))
        result = await db.execute(stmt)
        return list(result.scalars().all())


class ModelRunRepository:
    @staticmethod
    async def create(db: AsyncSession, run: ORMModelRun) -> ORMModelRun:
        db.add(run)
        await db.flush()
        return run

    @staticmethod
    async def list_for_session(db: AsyncSession, session_id: uuid.UUID) -> list[ORMModelRun]:
        stmt = (
            select(ORMModelRun)
            .where(ORMModelRun.session_id == session_id)
            .order_by(ORMModelRun.created_at.asc(), ORMModelRun.id)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())


class ActionRepository:
    @staticmethod
    async def create(db: AsyncSession, action: ORMAction) -> ORMAction:
        db.add(action)
        await db.flush()
        return action

    @staticmethod
    async def get_by_idempotency_key(
        db: AsyncSession, session_id: uuid.UUID, key: str
    ) -> ORMAction | None:
        stmt = select(ORMAction).where(
            ORMAction.session_id == session_id,
            ORMAction.idempotency_key == key,
        )
        result = await db.execute(stmt)
        return result.scalar_one_or_none()

    @staticmethod
    async def list_for_session(db: AsyncSession, session_id: uuid.UUID) -> list[ORMAction]:
        stmt = select(ORMAction).where(ORMAction.session_id == session_id).order_by(ORMAction.id)
        result = await db.execute(stmt)
        return list(result.scalars().all())
