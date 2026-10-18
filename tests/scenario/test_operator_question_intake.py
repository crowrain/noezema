"""Scenario: operator question intake on PostgreSQL (T7.59, §5.3.2, §13.6).

Service level: exact-text dedup (idempotent replay), origin provenance, FIFO
position and priority-forward selection, and the queue view with the session
annotation. The HTTP layer is tests/scenario/test_web_questions.py.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.services.question_intake import (
    put_operator_question,
    question_queue,
    queue_position,
)

pytestmark = [pytest.mark.scenario]


async def _seed(factory, raw_text: str, *, priority: int = 0, origin: str = "seeded") -> uuid.UUID:
    async with factory() as db, transaction(db):
        question = await QuestionRepository.create(
            db, ORMQuestion(text=raw_text, origin=origin, priority=priority)
        )
        return question.id


@pytest.mark.asyncio
async def test_operator_question_is_a_message_origin_candidate(migrated_db) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        question, created = await put_operator_question(db, raw_text="  Где зимуют ежи?  ", raw_priority=4)
    assert created is True
    assert question.origin == QuestionOrigin.MESSAGE.value
    assert question.state == QuestionState.CANDIDATE.value
    assert question.text == "Где зимуют ежи?"  # stored stripped, not as typed
    assert question.priority == 4
    assert question.created_at is not None


@pytest.mark.asyncio
async def test_exact_text_replay_returns_the_same_question(migrated_db) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        first, created_first = await put_operator_question(db, raw_text="Что такое 2+2?", raw_priority=1)
    async with factory() as db, transaction(db):
        # the same formulation with different surrounding whitespace: still a replay
        second, created_second = await put_operator_question(db, raw_text=" Что такое 2+2? \n", raw_priority=99)
    assert created_first is True
    assert created_second is False
    assert second.id == first.id
    # no duplicate candidate, and the replay does not silently re-rank it
    async with factory() as db:
        rows = (
            await db.execute(
                text("SELECT count(*) FROM questions WHERE text = :t"), {"t": "Что такое 2+2?"}
            )
        ).scalar_one()
        stored = await QuestionRepository.get(db, first.id)
    assert int(rows) == 1
    assert stored is not None and stored.priority == 1


@pytest.mark.asyncio
async def test_replay_matches_a_question_that_is_not_from_the_operator(migrated_db) -> None:
    """Dedup is over the whole registry: re-asking a corpus-seeded text does
    not create a second candidate row for the same formulation."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    seeded_id = await _seed(factory, "Сколько будет 6*7?", priority=0)
    async with factory() as db, transaction(db):
        question, created = await put_operator_question(db, raw_text="Сколько будет 6*7?")
    assert created is False
    assert question.id == seeded_id
    assert question.origin == QuestionOrigin.SEEDED.value


@pytest.mark.asyncio
async def test_priority_puts_the_operator_question_forward_in_fifo(migrated_db) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    older_low = await _seed(factory, "старый вопрос про мхи", priority=0)
    async with factory() as db, transaction(db):
        asked, _created = await put_operator_question(
            db, raw_text="срочный вопрос operator", raw_priority=7
        )
        selector = FIFOQuestionSelector()
        selected = await selector.select(db)
        assert selected is not None and selected.id == asked.id

        position_asked = await queue_position(db, asked)
        older_row = await QuestionRepository.get(db, older_low)
        assert older_row is not None
        position_older = await queue_position(db, older_row)

    assert position_asked == 1  # priority 7 outranks everything queued
    assert position_older == 2  # …so the older candidate moves back, not away

    async with factory() as db, transaction(db):
        await QuestionRepository.set_state(db, asked.id, QuestionState.RESEARCHING)
        selector = FIFOQuestionSelector()
        nxt = await selector.select(db)
        assert nxt is not None and nxt.id == older_low
        # a question that is no longer queued has no position
        assert await queue_position(db, asked) is None


@pytest.mark.asyncio
async def test_fifo_ties_are_broken_by_age_not_by_operator(migrated_db) -> None:
    """Equal priority keeps the plain FIFO (§5.3.2): the operator does not get
    a privilege beyond ranking."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    first = await _seed(factory, "первый по возрасту", priority=3)
    async with factory() as db, transaction(db):
        second, _created = await put_operator_question(db, raw_text="второй по возрасту", raw_priority=3)
        position = await queue_position(db, second)
        selected = await FIFOQuestionSelector().select(db)
    assert position == 2
    assert selected is not None and selected.id == first


@pytest.mark.asyncio
async def test_queue_view_orders_candidates_and_shows_the_session(migrated_db) -> None:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queued = await _seed(factory, "очередной вопрос", priority=0)
    resolved = await _seed(factory, "уже отвеченный вопрос", priority=0)

    async with factory() as db, transaction(db):
        asked, _created = await put_operator_question(db, raw_text="вопрос оператора", raw_priority=5)
        await QuestionRepository.set_state(db, resolved, QuestionState.VERIFIED)

    # a session that already worked on the resolved question (the annotation)
    session_id = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, state, question_id, config_snapshot_id) VALUES "
                "(:id, 'succeeded', :q, (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": session_id, "q": resolved},
        )
        await conn.commit()

    async with factory() as db:
        rows = await question_queue(db, limit=20)

    by_id = {row["id"]: row for row in rows}
    assert set(by_id) == {str(queued), str(resolved), str(asked.id)}
    # candidates first, priority DESC, then age ASC: positions are the selector's order
    assert [row["id"] for row in rows if row["position"] is not None] == [str(asked.id), str(queued)]
    assert by_id[str(asked.id)] == {
        "id": str(asked.id),
        "text": "вопрос оператора",
        "origin": QuestionOrigin.MESSAGE.value,
        "state": QuestionState.CANDIDATE.value,
        "priority": 5,
        "created_at": by_id[str(asked.id)]["created_at"],
        "position": 1,
        "session": None,
    }
    tail = by_id[str(resolved)]
    assert tail["position"] is None
    assert tail["state"] == QuestionState.VERIFIED.value
    assert tail["session"] == {"id": str(session_id), "state": "succeeded"}
