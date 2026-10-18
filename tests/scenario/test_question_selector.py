"""Scenario: FIFO question selector on PostgreSQL (T1.12, §5.3.2)."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository

pytestmark = [pytest.mark.scenario]


@pytest.mark.asyncio
async def test_fifo_selects_highest_priority_oldest_first(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]

    async with factory() as db:
        async with transaction(db):
            low = await QuestionRepository.create(
                db, ORMQuestion(text="low", origin=QuestionOrigin.SEEDED.value, priority=1)
            )
            mid = await QuestionRepository.create(
                db, ORMQuestion(text="mid", origin=QuestionOrigin.MESSAGE.value, priority=5)
            )
            high = await QuestionRepository.create(
                db, ORMQuestion(text="high", origin=QuestionOrigin.CONFLICT.value, priority=10)
            )

        selector = FIFOQuestionSelector()
        first = await selector.select(db)
        assert first is not None and first.id == high.id

        async with transaction(db):
            await QuestionRepository.set_state(db, high.id, QuestionState.SELECTED)
        second = await selector.select(db)
        assert second is not None and second.id == mid.id

        async with transaction(db):
            await QuestionRepository.set_state(db, mid.id, QuestionState.SELECTED)
            await QuestionRepository.set_state(db, low.id, QuestionState.RESEARCHING)
        third = await selector.select(db)
        assert third is None  # nothing left in candidate state


@pytest.mark.asyncio
async def test_empty_queue_returns_none(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)  # type: ignore[arg-type]
    async with factory() as db:
        selector = FIFOQuestionSelector()
        assert await selector.select(db) is None


# T7.35 (ADR-0019): the old test_real_prompts_are_versioned (file header
# == BOOTSTRAP_PAYLOAD version label) is superseded, STRENGTHENED, by
# tests/unit/test_prompt_pinning.py::test_bootstrap_payload_pins_match_repo_files
# — the pin is now CONTENT (path + version + sha256 of the file bytes),
# and the check verifies all three against the committed repo files.
