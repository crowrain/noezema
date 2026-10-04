"""Scenario: `noezemactl ask` puts an operator question into the queue (T7.59).

CliRunner against a scratch DB — the CLI is a thin wrapper over the intake
service, so the test checks the contract the operator sees: exit codes, the
printed id and queue position, and that a repeated formulation replays the
same id instead of duplicating. hostctl commands are otherwise tested as pure
functions (tests/unit/test_corpus_parse.py); this one needs the DB, hence the
`scenario` marker.

These tests are synchronous (the CLI builds its own event loop), so they never
touch the fixture's AsyncEngine — a second `asyncio.run` on it would hit a
closed loop. Every DB read/write here opens its own engine from the scratch
URL and disposes it.
"""

from __future__ import annotations

import asyncio
import re
import uuid

import pytest
from click.testing import CliRunner
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.repositories.questions import QuestionRepository

pytestmark = [pytest.mark.scenario]

_UUID_RE = re.compile(r"\bid\s+([0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12})")


def _ask(runner: CliRunner, url: str, *args: str):
    return runner.invoke(main, ["ask", *args], env={"NOEZEMA_DATABASE_URL": url})


def _printed_id(output: str) -> uuid.UUID:
    match = _UUID_RE.search(output)
    assert match is not None, output
    return uuid.UUID(match.group(1))


async def _with_db(url: str, fn):
    """One engine per call: sync tests cannot reuse the fixture's loop."""
    engine = create_async_engine(url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            return await fn(db)
    finally:
        await engine.dispose()


def _select(url: str, sql: str, **params):
    async def _run(db):
        return list((await db.execute(text(sql), params)).scalars().all())

    return asyncio.run(_with_db(url, _run))


def _seed_question(url: str, raw_text: str, *, priority: int = 0, origin: str = "seeded") -> None:
    """Seed like `eval-run` does (hostctl/cli.py), to mix origins in the queue."""

    async def _run(db):
        await db.execute(
            text(
                "INSERT INTO questions (id, text, origin, state, priority) "
                "VALUES (:id, :t, :o, 'candidate', :p)"
            ),
            {"id": uuid.uuid4(), "t": raw_text, "o": origin, "p": priority},
        )
        await db.commit()

    asyncio.run(_with_db(url, _run))


def test_ask_queues_the_question_and_reports_its_position(migrated_db) -> None:
    scratch_url, _engine = migrated_db
    runner = CliRunner()

    result = _ask(runner, scratch_url, "Где зимуют ежи?", "--priority", "7")
    assert result.exit_code == 0, result.output
    assert "question accepted" in result.output
    question_id = _printed_id(result.output)
    assert "origin   message" in result.output
    assert "state candidate" in result.output
    assert "position 1" in result.output

    async def _check(db):
        question = await QuestionRepository.get(db, question_id)
        assert question is not None
        selected = await FIFOQuestionSelector().select(db)
        rows = int((await db.execute(text("SELECT count(*) FROM questions"))).scalar_one())
        return (
            question.origin,
            question.state,
            question.priority,
            rows,
            str(selected.id) if selected is not None else "",
        )

    origin, state, priority, rows, selected_id = asyncio.run(_with_db(scratch_url, _check))
    assert (origin, state, priority) == (QuestionOrigin.MESSAGE.value, QuestionState.CANDIDATE.value, 7)
    assert rows == 1
    assert selected_id == str(question_id)  # the FIFO head is the question just asked


def test_ask_is_idempotent_by_exact_text(migrated_db) -> None:
    scratch_url, _engine = migrated_db
    runner = CliRunner()

    first = _ask(runner, scratch_url, "Сколько будет 6*7?")
    assert first.exit_code == 0, first.output
    first_id = _printed_id(first.output)

    again = _ask(runner, scratch_url, "Сколько будет 6*7?")
    assert again.exit_code == 0, again.output
    assert "already in the queue (idempotent replay)" in again.output
    assert _printed_id(again.output) == first_id

    # no duplicate candidate, and the replay did not re-rank the row
    rows = _select(scratch_url, "SELECT priority FROM questions WHERE text = :t", t="Сколько будет 6*7?")
    assert rows == [0]


def test_ask_replays_a_text_that_is_already_in_the_registry(migrated_db) -> None:
    """Exact-text dedup covers corpus-seeded questions too: re-asking a seeded
    formulation returns the existing id instead of duplicating it."""
    scratch_url, _engine = migrated_db
    runner = CliRunner()
    _seed_question(scratch_url, "Сколько будет 6*7?", priority=0)

    result = _ask(runner, scratch_url, "Сколько будет 6*7?")
    assert result.exit_code == 0, result.output
    assert "already in the queue (idempotent replay)" in result.output

    rows = _select(
        scratch_url,
        "SELECT origin FROM questions WHERE text = :t",
        t="Сколько будет 6*7?",
    )
    assert rows == [QuestionOrigin.SEEDED.value]


def test_ask_puts_the_question_forward_in_fifo(migrated_db) -> None:
    scratch_url, _engine = migrated_db
    runner = CliRunner()
    _seed_question(scratch_url, "старый кандидат", priority=0)

    result = _ask(runner, scratch_url, "срочный вопрос оператора", "--priority", "9")
    assert result.exit_code == 0, result.output
    assert "position 1" in result.output  # priority moves it ahead of the older candidate

    async def _check(db):
        selected = await FIFOQuestionSelector().select(db)
        if selected is None:
            return "", 0
        candidates = (await db.execute(text("SELECT text FROM questions ORDER BY priority DESC, created_at")))
        return selected.text, len(list(candidates.scalars().all()))

    selected_text, total = asyncio.run(_with_db(scratch_url, _check))
    assert selected_text == "срочный вопрос оператора"
    assert total == 2  # the older candidate is still queued, just behind


def test_ask_rejects_a_bad_formulation_with_usage_exit(migrated_db) -> None:
    scratch_url, _engine = migrated_db
    runner = CliRunner()

    result = _ask(runner, scratch_url, "   ")
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "empty" in result.output

    result = _ask(runner, scratch_url, "вопрос", "--priority", "101")
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "out of range" in result.output

    rows = _select(scratch_url, "SELECT count(*) FROM questions")
    assert int(rows[0]) == 0  # a rejected formulation never reaches the queue


def test_ask_fails_closed_without_a_database_url() -> None:
    runner = CliRunner()
    result = runner.invoke(main, ["ask", "вопрос"], env={"NOEZEMA_DATABASE_URL": ""})
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "NOEZEMA_DATABASE_URL" in result.output


def test_ask_does_not_let_the_operator_choose_origin_or_state(migrated_db) -> None:
    """§3.4 provenance is not a CLI flag: origin='message' and state
    'candidate' are fixed by the intake service."""
    scratch_url, _engine = migrated_db
    runner = CliRunner()

    result = _ask(runner, scratch_url, "вопрос", "--origin", "seeded")
    assert result.exit_code == EXIT_USAGE_ERROR
    assert "No such option" in result.output and "--origin" in result.output

    rows = _select(scratch_url, "SELECT count(*) FROM questions")
    assert int(rows[0]) == 0
