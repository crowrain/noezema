"""T7.127 (части 4 и 5в): отказы лимита поиска узла — отдельная строка отчёта честности.

Сидинг — перенесённые в `tests/fixtures/t7127/` копии реальных строк стенового прогона `accd7e2`
на config-v28 (фикстуры менеджера из `fixtures-t7127`):

* `c3_steps.jsonl` — сессия abe8163f: 10 поисков, из них 5 отклонено лимитом;
* `c4_steps.jsonl` — сессия c72bdc97: 4 поиска, 2 отказа лимита;
* `c2_steps.jsonl` — сессия c3d808bb: 8 поисков, отказов лимита нет;
* `upstream_rate_limit_rejections.jsonl` — 7 строк журнала прокси `research_fetch_rejected`
  (пишутся БЕЗ session_id — их пишет сам прокси, сессии не зная);
* `session_spans.json` — интервалы жизни сессий и id вопросов из control-отчёта прогона;
* `control_totals_before_t7127.json` — totals прежнего control-отчёта (без новой метрики).

Копии точны по полям, которые читает отчёт (`tool`, `arguments`, `action_id`, `ok`, `error`,
`reason`, `url`, `occurred_at`, `sequence`); из `action_completed` убран только `data` — большой
текст наблюдения отчётом не читается. Лента сидируется с исходными `sequence` и `occurred_at`:
у строк одной долгой транзакции времени совпадают (ловушка AGENTS §7), поэтому порядок даёт
только `sequence`, а привязка чужого журнала к сессии — интервал жизни сессии из строки
`sessions`.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.memory.honesty_report import (
    HonestyReport,
    collect_honesty_report,
    load_baseline,
    render_markdown,
)

pytestmark = pytest.mark.scenario

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t7127"

# id стеновых сессий восстановлены по 8-значным префиксам имён фикстур: отчёт показывает
# ровно первые 8 знаков, поэтому копии сходятся с исходными строками прогона.
S_C3 = uuid.UUID("abe8163f-0000-4000-8000-000000000127")
S_C4 = uuid.UUID("c72bdc97-0000-4000-8000-000000000127")
S_C2 = uuid.UUID("c3d808bb-0000-4000-8000-000000000127")

SESSION_KEYS = {S_C3: "abe8163f", S_C4: "c72bdc97", S_C2: "c3d808bb"}
STEP_FILES = {S_C3: "c3_steps.jsonl", S_C4: "c4_steps.jsonl", S_C2: "c2_steps.jsonl"}


def _ts(value: str) -> datetime:
    """ISO-время фикстуры в datetime: asyncpg принимает только datetime (CAST не помогает)."""
    return datetime.fromisoformat(value)


def _fixture_rows(name: str) -> list[dict[str, Any]]:
    return [json.loads(line) for line in (FIXTURES / name).read_text(encoding="utf-8").splitlines() if line]


def _spans() -> dict[str, Any]:
    return json.loads((FIXTURES / "session_spans.json").read_text(encoding="utf-8"))


async def _execute(engine: AsyncEngine, sql: str, **params: Any) -> None:
    async with engine.connect() as conn:
        await conn.execute(text(sql), params)
        await conn.commit()


async def _snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    async with engine.connect() as conn:
        return (
            await conn.execute(
                text("SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'")
            )
        ).scalar_one()


async def _seed(engine: AsyncEngine, *, shift_c3_out_of_window: bool = False) -> list[uuid.UUID]:
    """Одна сессия на вопрос: лента шагов из фикстуры + общий журнал отказов прокси."""
    snapshot = await _snapshot_id(engine)
    spans = _spans()
    question_ids: list[uuid.UUID] = []
    for session_id, key in SESSION_KEYS.items():
        info = spans[key]
        # id в control-отчёте усечены до 8 знаков (так их печатает отчёт) — для посева
        # восстанавливаются с фиксированным хвостом
        question_id = uuid.UUID(f"{info['question_id']}-0000-4000-8000-000000000007")
        question_ids.append(question_id)
        started_at, finished_at = str(info["started_at"]), str(info["finished_at"])
        if shift_c3_out_of_window and session_id == S_C3:
            # сдвиг жизни сессии за пределы всех отказов лимита: приписывать отказ нечему
            started_at, finished_at = "2026-10-27T11:23:25+00:00", "2026-10-27T11:45:17+00:00"
        await _execute(
            engine,
            "INSERT INTO questions (id, text, origin, state, priority) "
            "VALUES (:id, :t, 'seeded', 'verified', 0)",
            id=question_id, t=f"Стеновой вопрос {key}",
        )
        await _execute(
            engine,
            "INSERT INTO sessions (id, state, question_id, config_snapshot_id, termination_reason, "
            "started_at, finished_at) VALUES (:s, :state, :q, :c, 'goal_reached', "
            "CAST(:from_ts AS timestamptz), CAST(:to_ts AS timestamptz))",
            s=session_id, state=str(info["state"]), q=question_id, c=snapshot,
            from_ts=_ts(started_at), to_ts=_ts(finished_at),
        )
        for row in _fixture_rows(STEP_FILES[session_id]):
            await _execute(
                engine,
                "INSERT INTO audit_events (id, session_id, sequence, type, payload, occurred_at) "
                "VALUES (:id, :s, :seq, :type, CAST(:payload AS JSONB), CAST(:occ AS timestamptz))",
                id=uuid.uuid4(), s=session_id, seq=int(row["seq"]), type=str(row["type"]),
                payload=json.dumps(row["payload"]), occ=_ts(str(row["occurred_at"])),
            )
    for index, row in enumerate(_fixture_rows("upstream_rate_limit_rejections.jsonl")):
        await _execute(
            engine,
            "INSERT INTO audit_events (id, session_id, sequence, type, payload, occurred_at) "
            "VALUES (:id, NULL, :seq, :type, CAST(:payload AS JSONB), CAST(:occ AS timestamptz))",
            id=uuid.uuid4(), seq=900 + index, type=str(row["type"]),
            payload=json.dumps(row["payload"]), occ=_ts(str(row["occurred_at"])),
        )
    return question_ids


async def _collect(engine: AsyncEngine, question_ids: list[uuid.UUID]) -> HonestyReport:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return await collect_honesty_report(db, question_ids=question_ids)


def _steps(report: HonestyReport, session_id: uuid.UUID) -> Any:
    for question in report.questions:
        for session in question.sessions:
            if session.session_id == str(session_id)[:8]:
                return session.steps
    raise AssertionError(f"сессия {session_id} не найдена в отчёте")


async def test_rate_limit_refusals_are_counted_apart_from_empty_searches(migrated_db) -> None:
    """c3: 10 поисков, 5 отказов лимита считаются отдельно и не входят в «пустые» (было 5 → стало 2)."""
    scratch_url, _fixture_engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        question_ids = await _seed(engine)
        report = await _collect(engine, question_ids)

        c3 = _steps(report, S_C3)
        assert c3.searches_total == 10
        assert c3.searches_rate_limited == 5
        # было (без учёта отказов): wasted_searches = 5; стало: отказы исключены
        assert c3.wasted_searches == 2

        c4 = _steps(report, S_C4)
        assert c4.searches_total == 4
        assert c4.searches_rate_limited == 2
        assert c4.wasted_searches == 1

        c2 = _steps(report, S_C2)
        assert c2.searches_total == 8
        assert c2.searches_rate_limited == 0
        assert c2.wasted_searches == 4  # прежние числа сессии без отказов лимита не изменились

        for steps in (c3, c4, c2):
            assert steps.searches_official + steps.searches_other == steps.searches_total
    finally:
        await engine.dispose()


async def test_totals_share_is_counted_from_searches_that_reached_the_engine(migrated_db) -> None:
    """Итог: 22 поиска, 7 отказов лимита; доля пустых — от 15 поисков, дошедших до движка."""
    scratch_url, _fixture_engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        question_ids = await _seed(engine)
        report = await _collect(engine, question_ids)

        assert report.totals["searches_total"] == 22
        assert report.totals["searches_rate_limited"] == 7
        assert report.totals["wasted_searches"] == 7  # было 12 по всем трём сессиям
        assert report.totals["share_wasted_searches"] == round(7 / (22 - 7), 4)
    finally:
        await engine.dispose()


async def test_markdown_names_limit_refusals_and_the_share_formula(migrated_db) -> None:
    """В markdown отказы лимита названы отдельной строкой, формула доли приведена в тексте."""
    scratch_url, _fixture_engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        question_ids = await _seed(engine)
        report = await _collect(engine, question_ids)
        markdown = render_markdown(report)

        assert "отклонено лимитом поиска узла: 5" in markdown
        assert "отклонено лимитом поиска узла: 2" in markdown
        assert "отклонено лимитом поиска узла: 0" in markdown
        assert "отклонено лимитом поиска узла 7" in markdown
        assert "wasted_searches / (searches_total - searches_rate_limited)" in markdown
    finally:
        await engine.dispose()


async def test_baseline_without_the_new_metric_shows_no_data(migrated_db) -> None:
    """Baseline прежней формы (control-прогон до T7.127): новой метрики в нём нет — «нет данных»."""
    scratch_url, _fixture_engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        question_ids = await _seed(engine)
        report = await _collect(engine, question_ids)
        baseline = load_baseline(str(FIXTURES / "control_totals_before_t7127.json"))
        markdown = render_markdown(report, baseline)

        assert "| searches_rate_limited | нет данных | 7 | — |" in markdown
        # прежняя метрика сравнивается числом: в control-прогоне было 12 «пустых» поисков
        assert "| wasted_searches | 12 | 7 | -5 |" in markdown
    finally:
        await engine.dispose()


async def test_refusals_are_not_attributed_outside_the_session_lifetime(migrated_db) -> None:
    """Числы не из воздуха: вне интервала жизни сессии отказ ей не приписывается."""
    scratch_url, _fixture_engine = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        question_ids = await _seed(engine, shift_c3_out_of_window=True)
        report = await _collect(engine, question_ids)

        c3 = _steps(report, S_C3)
        assert c3.searches_rate_limited == 0
        assert c3.wasted_searches == 5  # прежние числа: ни один поиск не исключён
        assert report.totals["searches_rate_limited"] == 2  # чужие отказы (c4) остались при своей
    finally:
        await engine.dispose()
