"""Question repository (T1.5)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import QuestionState
from packages.domain.models.questions import ORMQuestion


class QuestionRepository:
    @staticmethod
    async def create(db: AsyncSession, question: ORMQuestion) -> ORMQuestion:
        db.add(question)
        await db.flush()
        return question

    @staticmethod
    async def get(db: AsyncSession, question_id: uuid.UUID) -> ORMQuestion | None:
        return await db.get(ORMQuestion, question_id)

    @staticmethod
    async def list_candidates(
        db: AsyncSession,
        limit: int = 10,
    ) -> list[ORMQuestion]:
        """FIFO by (priority DESC, created_at ASC) among candidates (§5.3.2)."""
        stmt = (
            select(ORMQuestion)
            .where(ORMQuestion.state == QuestionState.CANDIDATE.value)
            .order_by(ORMQuestion.priority.desc(), ORMQuestion.created_at.asc(), ORMQuestion.id)
            .limit(limit)
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    async def set_state(db: AsyncSession, question_id: uuid.UUID, state: QuestionState) -> None:
        question = await db.get(ORMQuestion, question_id)
        if question is None:
            raise LookupError(f"question {question_id} not found")
        question.state = state.value
        await db.flush()
