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


def test_real_prompts_are_versioned() -> None:
    """Repo prompts load with the versions pinned in the config snapshot."""
    from packages.llm_gateway.roles import Role, load_prompt

    explorer = load_prompt(Role.EXPLORER)
    curator = load_prompt(Role.CURATOR)
    # T7.14: prompts bumped to v3 (explorer: ≤2 repeats after a tool error;
    # curator: no assertion text in evidence → question, not claim)
    # T7.21: explorer bumped to v4 (fetch every question-named source
    # before completing — backstop for the host coverage gate, ADR-0010)
    # T7.34: curator bumped to v4 (the reverify operation —
    # existing_claim_id, ADR-0018)
    assert explorer.version == "explorer-v4"
    assert curator.version == "curator-v4"
    assert len(explorer.sha256) == 64
    assert len(curator.sha256) == 64
    # config snapshot references these exact paths/versions
    from packages.domain.config import BOOTSTRAP_PAYLOAD

    assert BOOTSTRAP_PAYLOAD["prompts"]["explorer"]["version"] == explorer.version
    assert BOOTSTRAP_PAYLOAD["prompts"]["curator"]["version"] == curator.version
