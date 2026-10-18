"""Scenario: `noezemactl claim-dispute` — операторский спор с хостовой консоли (T7.81).

CLI — тонкая обёртка над тем же сервисом `packages.memory/dispute.py`, что и Command API
веб-витрины; отличается только актор (`operator:hostctl`). Тест проверяет контракт,
который видит оператор на стенде `.92`: коды возврата, напечатанные id коррекции и
вопроса, honest-отказ с кодом причины вместо стека трейсов, и что дальше работает
существующий рабочий переоценки (бейдж уходит вместе с оценкой, а не по желанию CLI).

CliRunner-ловушка T7.59: команда поднимает свой event loop, поэтому тест не берёт
AsyncEngine фикстуры — каждый read/write открывает свой engine из scratch-URL и
`dispose()` его (иначе «Event loop is closed» на teardown).
"""

from __future__ import annotations

import asyncio
import contextlib
import re
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from hostctl.cli import EXIT_USAGE_ERROR, main
from packages.memory import dispute
from tests.scenario.test_claim_dispute import (
    PRIMARY_URI,
    REASON,
    RETOLD_URI,
    STATEMENT,
    _corrections,
    _head,
    _run_worker,
    _scalar,
    _seed_contested_claim,
    _source_members,
    _worker_reasons,
)

pytestmark = [pytest.mark.scenario]

_UUID_RE = re.compile(r"\b([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})")


def _text(result: Any) -> str:
    """Вывод команды целиком: отказ CLI печатает в stderr."""
    parts = [result.output]
    with contextlib.suppress(ValueError, AttributeError):
        parts.append(result.stderr)  # mix_stderr=False в тесте: отказ печатается отдельным потоком
    return "\n".join(parts)


def _dispute(runner: CliRunner, url: str, claim: uuid.UUID | str, *args: str):
    return runner.invoke(
        main,
        ["claim-dispute", "--claim", str(claim), "--primary", PRIMARY_URI, "--retelling", RETOLD_URI, *args],
        env={"NOEZEMA_DATABASE_URL": url},
    )


def _with_engine(url: str, fn: Callable[[AsyncEngine], Any]) -> Any:
    """Один engine на операцию: синхронный тест не может переиспользовать loop фикстуры."""

    async def _main() -> Any:
        engine = create_async_engine(url)
        try:
            return await fn(engine)
        finally:
            await engine.dispose()

    return asyncio.run(_main())


def _seed(url: str) -> dict[str, Any]:
    return _with_engine(url, lambda engine: _seed_contested_claim(engine))


def _ids(output: str) -> list[uuid.UUID]:
    return [uuid.UUID(m.group(1)) for m in _UUID_RE.finditer(output)]


# ─── успешный путь оператора ──────────────────────────────────────────────


def test_claim_dispute_command_writes_the_correction_and_queues_a_recheck(
    migrated_db: Any,
) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)
    claim_id = seeded["claim_id"]

    result = _dispute(CliRunner(), scratch_url, claim_id, "--reason", REASON)
    assert result.exit_code == 0, _text(result)
    output = _text(result)
    assert "оспорено" in output and "operator:hostctl" in output
    assert REASON in output
    assert "merge" in output and RETOLD_URI in output

    ids = _ids(output)
    assert claim_id in ids, output  # напечатан id утверждения
    question_id = ids[-1]  # последним напечатан id вопроса

    # коррекция графа с актором хоста: именно это §11.3 разрешает оператору
    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1, rows
    actor, kind, valid, _, basis, reason_event, from_id, to_id = rows[0]
    assert (actor, kind, valid) == ("operator:hostctl", "merge", True)
    assert basis == seeded["retold_artifact"] and reason_event is not None
    assert {from_id, to_id} == {seeded["primary"], seeded["retold"]}

    # действующая оценка снята в pending, заведена задача пересчёта
    async def _read(engine: AsyncEngine) -> tuple[Any, Any, Any]:
        head = await _head(engine, claim_id)
        job = await _scalar(
            engine,
            "SELECT reason, status FROM reassessment_jobs "
            "WHERE claim_id = :c AND reason = 'source_graph_change'",
            {"c": claim_id},
        )
        question = await _scalar(
            engine, "SELECT text, origin FROM questions WHERE id = :q", {"q": question_id}
        )
        return head, job, question

    head, job, question = _with_engine(scratch_url, _read)
    assert head[0] == "pending" and head[2] is None, head
    assert job[0] == "source_graph_change" and job[1] == "queued", job
    assert STATEMENT in question[0] and question[1] == "message", question


def test_the_cli_dispute_changes_the_grade_only_through_the_existing_worker(
    migrated_db: Any,
) -> None:
    """CLI не назначает оценку: её пересчитывает рабочий переоценки по правилам."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)
    claim_id = seeded["claim_id"]

    assert _dispute(CliRunner(), scratch_url, claim_id, "--reason", REASON).exit_code == 0

    _with_engine(scratch_url, lambda engine: _run_worker(engine))

    async def _read(engine: AsyncEngine) -> tuple[Any, list[Any], list[str]]:
        head = await _head(engine, claim_id)
        members = await _source_members(engine, claim_id)
        return head, members, await _worker_reasons(engine, claim_id)

    head, members, reasons = _with_engine(scratch_url, _read)
    assert head[0] == "current" and head[1] == "hypothesis", head
    assert len({m[0] for m in members}) == 1, members
    assert "insufficient_independence" in reasons, reasons


def test_repeat_on_the_console_is_a_replay_and_cancel_restores_the_previous_grade(
    migrated_db: Any,
) -> None:
    """Идемпотентный повтор не дублирует коррекцию; отмена возвращает прежнюю оценку."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)
    claim_id = seeded["claim_id"]
    runner = CliRunner()

    first = _dispute(runner, scratch_url, claim_id, "--reason", REASON)
    again = _dispute(runner, scratch_url, claim_id, "--reason", REASON)
    assert again.exit_code == 0, _text(again)
    output = _text(again)
    assert "идемпотентный повтор" in output, output
    correction_first = _ids(_text(first))[1]
    assert correction_first in _ids(output)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1, rows
    questions = _with_engine(
        scratch_url,
        lambda engine: _scalar(
            engine, "SELECT count(*) FROM questions WHERE text LIKE 'Перепроверить%'"
        ),
    )
    assert questions[0] == 1, questions

    # спор реализовался в оценке: независимость стала одной группой
    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head_after_dispute = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head_after_dispute[1] == "hypothesis", head_after_dispute

    cancelled = runner.invoke(
        main,
        [
            "claim-dispute-cancel",
            "--claim",
            str(claim_id),
            "--reason",
            "первоисточник всё-таки отдельный: разные таблицы и даты",
        ],
        env={"NOEZEMA_DATABASE_URL": scratch_url},
    )
    assert cancelled.exit_code == 0, _text(cancelled)
    assert "спор снят" in _text(cancelled) and "valid = false" in _text(cancelled)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert len(rows) == 1 and rows[0][2] is False, rows  # строка осталась, стала недействующей

    _with_engine(scratch_url, lambda engine: _run_worker(engine))
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    members = _with_engine(scratch_url, lambda engine: _source_members(engine, claim_id))
    assert head[0] == "current" and head[1] == "supported" and head[3] == "E3", head
    assert len({m[0] for m in members}) == 2, members


# ─── честные отказы CLI ───────────────────────────────────────────────────


def test_cli_refusals_exit_two_and_write_nothing(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)
    claim_id = seeded["claim_id"]
    runner = CliRunner()

    cases = [
        (_dispute(runner, scratch_url, uuid.uuid4(), "--reason", REASON), "dispute_claim_not_found"),
        (
            _dispute(
                runner,
                scratch_url,
                claim_id,
                "--primary",
                PRIMARY_URI,
                "--retelling",
                "https://rian.example/retelling",
                "--reason",
                REASON,
            ),
            "dispute_source_not_found",
        ),
        (_dispute(runner, scratch_url, claim_id, "--reason", "см"), "dispute_reason_invalid"),
    ]
    for result, code in cases:
        assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
        output = _text(result)
        assert f"отказ ({code})" in output, output
        assert "Traceback" not in output, output

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, claim_id))
    assert rows == [], rows
    head = _with_engine(scratch_url, lambda engine: _head(engine, claim_id))
    assert head[0] == "current" and head[3] == "E3", head
    audit = _with_engine(
        scratch_url,
        lambda engine: _scalar(
            engine,
            "SELECT count(*) FROM audit_events WHERE type IN "
            "('operator_command_received','operator_command_completed')",
        ),
    )
    assert audit[0] == 0, audit


def test_the_console_needs_a_full_claim_uuid(migrated_db: Any) -> None:
    """Витрина показывает утверждение коротким id; CLI требует полный uuid и говорит об этом."""
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)

    result = _dispute(CliRunner(), scratch_url, str(seeded["claim_id"])[:8], "--reason", REASON)
    assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
    assert "нужен полный uuid" in _text(result)

    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, seeded["claim_id"]))
    assert rows == [], rows


def test_the_console_cancels_only_a_real_dispute(migrated_db: Any) -> None:
    scratch_url, _fixture_engine = migrated_db
    seeded = _seed(scratch_url)

    result = CliRunner().invoke(
        main,
        ["claim-dispute-cancel", "--claim", str(seeded["claim_id"])],
        env={"NOEZEMA_DATABASE_URL": scratch_url},
    )
    assert result.exit_code == EXIT_USAGE_ERROR, _text(result)
    assert f"отказ ({dispute.REFUSAL_CORRECTION_NOT_FOUND})" in _text(result)
    rows = _with_engine(scratch_url, lambda engine: _corrections(engine, seeded["claim_id"]))
    assert rows == [], rows
