"""T7.131 (части 4б, 4в, 4г): отчёт честности — три состояния ответа и честная длительность.

Стендовый прогон `929bdcc-v29` (`.92`, config-v29) показал два расхождения отчёта с фактами:

* вопрос c1 (`929bdcc-v29`) имел карточку `answered_from_relied` (8 опор, новых записей 0), а
  отчёт писал «Ответ не записан» и не считал его в `questions_answered`;
* сессия c2 имела в журнале ≈18 минут работы, а отчёт напечатал «длительность: 295 c» — потому
  что считал разницу `sessions.finished_at - sessions.started_at`, а строку сессии и её события
  фиксирует одна длинная транзакция фазы 1 (AGENTS §7 про `created_at` таких строк).

Закреплено тестом:

* (б) сессия с одними опорами -> «Ответ по уже записанным знаниям», вопрос считается и в
  `questions_answered`, и в новой метрике `questions_answered_relied_only`; предикат отчёта
  сверяется с НАСТОЯЩЕЙ карточкой `apps/web/answer.py` на тех же записях (packages/ не
  импортирует apps/, поэтому совпадение закрепляется тестом, а не импортом);
* (в) длительность — по реальному времени инструментов сессии (`actions`), а не по строке
  `sessions`; если действий со штампом нет — «длительность недоступна» вместо ложного числа;
* (г) baseline прежней формы (без новой метрики) читается и даёт «нет данных», а не 0 и не
  ошибку (`SCHEMA_VERSION` аддитивной метрикой не поднят).

Тестовая БД (`migrated_db`), без LLM и сети. Все счётчики читаются ровно теми же запросами,
что пишет продукт.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.answer import question_answer
from packages.memory.honesty_report import (
    SCHEMA_VERSION,
    HonestyReport,
    collect_honesty_report,
    load_baseline,
    render_markdown,
)
from tests.scenario.test_honesty_report_t7118 import _count, _execute, _snapshot_id

pytestmark = pytest.mark.scenario

# В отчёте id усекаются до первых 8 hex-знаков — у констант различается первая группа.
Q_SEED = uuid.UUID("d1000000-0000-4000-8000-000000000131")  # вопрос прежнего прогона (не в отборе)
Q_RELIED = uuid.UUID("d2000000-0000-4000-8000-000000000131")  # ответ целиком из уже записанного
Q_OWN = uuid.UUID("d3000000-0000-4000-8000-000000000131")  # есть своя запись
S_SEED = uuid.UUID("e1000000-0000-4000-8000-000000000131")
S_RELIED = uuid.UUID("e2000000-0000-4000-8000-000000000131")
S_OWN = uuid.UUID("e3000000-0000-4000-8000-000000000131")
C_SEED = uuid.UUID("f1000000-0000-4000-8000-000000000131")  # уже записанное, на которое оперлись
C_OWN = uuid.UUID("f2000000-0000-4000-8000-000000000131")  # запись этого вопроса
A_SEED = uuid.UUID("00000000-0000-4000-8000-000000000761")  # оценка опоры
A_OWN = uuid.UUID("00000000-0000-4000-8000-000000000762")  # оценка своей записи

TEXT_RELIED = "Что будет с ценами на продукты? (контроль t7131)"
TEXT_OWN = "Какая инфляция в Тармении? (контроль t7131)"
TEXT_SEED = "Уровень безработицы в Тармении за 2029 год"

#: Прежние сессии (им принадлежит опора и своя запись вопроса).
SEED_STAMP = "2026-10-20T10:00:00+00:00"
SEED_STAMP_END = "2026-10-20T10:20:00+00:00"

#: Строка сессии c2 стендового прогона: обе метки — время ФИКСАЦИИ транзакции (295 с «работы»).
ROW_STAMP = "2026-10-22T08:55:16+00:00"
ROW_STAMP_END = "2026-10-22T09:00:11+00:00"
#: Реальные штампы инструментов той же сессии: 08:37:52 … 08:55:05 = 1033 с.
ACTION_FIRST = "2026-10-22T08:37:52+00:00"
ACTION_LAST = "2026-10-22T08:55:05+00:00"


async def _seed_claim(
    engine: AsyncEngine,
    *, claim_id: uuid.UUID, statement: str, session_id: uuid.UUID, grade: str, assessment_id: uuid.UUID,
) -> None:
    """Строка знания с действующей оценкой и головой в активном снимке — как её пишет продукт."""
    snapshot = await _snapshot_id(engine)
    await _execute(
        engine,
        "INSERT INTO claims (id, statement, claim_type, freshness_status, created_in_session) "
        "VALUES (:id, :s, 'external_fact', 'fresh', :c)",
        id=claim_id, s=statement, c=session_id,
    )
    await _execute(
        engine,
        "INSERT INTO claim_assessments (id, claim_id, effective_grade, epistemic_status, "
        "rules_version, rules_hash, evidence_set_hash, assessed_scope, confidence, valid, "
        "created_in_session) VALUES (:a, :c, :g, 'supported', 'rules-v2', 'h', 'ev', "
        "CAST('{\"x\": 1}' AS JSONB), 0.9, true, :s)",
        a=assessment_id, c=claim_id, g=grade, s=session_id,
    )
    await _execute(
        engine,
        "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
        "current_assessment_id, epistemic_status, prepared_by) VALUES "
        "(:c, :s, 'current', :a, 'supported', 'rules_activation')",
        c=claim_id, s=snapshot,
        a=assessment_id,
    )


async def _seed_world(engine: AsyncEngine) -> None:
    """Два вопроса прогона и одно уже записанное знание постороннего вопроса."""
    snapshot = await _snapshot_id(engine)
    for qid, text_value in ((Q_SEED, TEXT_SEED), (Q_RELIED, TEXT_RELIED), (Q_OWN, TEXT_OWN)):
        await _execute(
            engine,
            "INSERT INTO questions (id, text, origin, state, priority) "
            "VALUES (:id, :t, 'seeded', 'verified', 9)",
            id=qid, t=text_value,
        )
    # Сессия прежнего прогона (ей принадлежит опора) + сессия, которая только оперлась.
    # Времена — литералы SQL: asyncpg не принимает строку в `CAST(:p AS TIMESTAMPTZ)` (AGENTS §7).
    await _execute(
        engine,
        "INSERT INTO sessions (id, state, question_id, config_snapshot_id, termination_reason, "
        "started_at, finished_at) VALUES "
        f"(:s_seed, 'succeeded', :q_seed, :c, 'goal_reached', '{SEED_STAMP}', '{SEED_STAMP_END}'),"
        f"(:s_relied, 'succeeded', :q_relied, :c, 'goal_reached', '{ROW_STAMP}', '{ROW_STAMP_END}'),"
        f"(:s_own, 'succeeded', :q_own, :c, 'goal_reached', '{SEED_STAMP}', '{SEED_STAMP_END}')",
        s_seed=S_SEED, s_relied=S_RELIED, s_own=S_OWN, c=snapshot,
        q_seed=Q_SEED, q_relied=Q_RELIED, q_own=Q_OWN,
    )
    await _seed_claim(
        engine, claim_id=C_SEED,
        statement="Безработица в Тармении в 2029 году составила 6,1 процента",
        session_id=S_SEED, grade="E3", assessment_id=A_SEED,
    )
    await _seed_claim(
        engine, claim_id=C_OWN,
        statement="Инфляция в Тармении в 2026 году составила 4,2 процента",
        session_id=S_OWN, grade="E3", assessment_id=A_OWN,
    )
    # куратор ответа-опоры: 0 новых записей, объявленная опора (форма payload — как у хоста)
    async with engine.connect() as conn:
        for session_id, payload in (
            (S_RELIED, {"relied_claim_ids": [str(C_SEED)], "claims_applied": 0, "claims_proposed": 0}),
            (S_OWN, {"claim_id": str(C_OWN), "claims_applied": 1, "claims_proposed": 1}),
        ):
            await conn.execute(
                text(
                    "INSERT INTO audit_events (id, session_id, sequence, type, payload) "
                    "VALUES (:id, :s, 5, 'claim_created', CAST(:payload AS JSONB))"
                ),
                {"id": uuid.uuid4(), "s": session_id, "payload": json.dumps(payload)},
            )
        await conn.commit()


async def _seed_actions(engine: AsyncEngine, session_id: uuid.UUID, span: tuple[str, str]) -> None:
    """Два инструмента сессии с РЕАЛЬНЫМИ штампами (так их пишет оркестратор каждым шагом)."""
    async with engine.connect() as conn:
        for index, (tool, started, finished) in enumerate(
            (("web.search", span[0], span[0]), ("research.fetch", span[0], span[1]))
        ):
            run_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO model_runs (id, session_id, turn_id, phase, model_fingerprint, "
                    "input_tokens, output_tokens) VALUES (:id, :s, :t, 'explorer', '{}', 100, 20)"
                ),
                {"id": run_id, "s": session_id, "t": uuid.uuid4()},
            )
            await conn.execute(
                text(
                    "INSERT INTO actions (id, session_id, model_run_id, idempotency_key, "
                    "idempotency_class, tool, arguments_hash, policy_decision, state, "
                    "started_at, finished_at) VALUES (:id, :s, :r, :k, 'observation', :tool, "
                    f"'h', 'allow', 'completed', '{started}', '{finished}')"
                ),
                {
                    "id": uuid.uuid4(), "s": session_id, "r": run_id,
                    "k": f"t7131-{index}-{tool}", "tool": tool,
                },
            )
        await conn.commit()


async def _collect(engine: AsyncEngine, **selection: Any) -> HonestyReport:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return await collect_honesty_report(db, **selection)


def _find(report: HonestyReport, question_id: uuid.UUID) -> Any:
    for question in report.questions:
        if question.question_id == str(question_id)[:8]:
            return question
    raise AssertionError(f"вопрос {question_id} не найден в отчёте")


# ─── (б) опора вместо новой записи — третий честный статус ─────────────────────────────


async def test_relied_only_answer_is_named_and_counted(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_RELIED, Q_OWN])

        relied = _find(report, Q_RELIED)
        assert relied.answer_state == "relied_only"
        assert relied.answered is True  # `questions_answered` считает оба первых состояния
        assert relied.answered_relied_only is True
        assert [claim.claim_id for claim in relied.claims["relied"]] == [str(C_SEED)[:8]]
        assert relied.claims["created"] == []

        own = _find(report, Q_OWN)
        assert own.answer_state == "answered"
        assert own.answered_relied_only is False

        assert report.totals["questions_answered"] == 2
        assert report.totals["questions_answered_relied_only"] == 1

        markdown = render_markdown(report)
        assert f"## Вопрос {str(Q_RELIED)[:8]} — Ответ по уже записанным знаниям" in markdown
        assert f"## Вопрос {str(Q_OWN)[:8]} — Ответ есть" in markdown
        assert "новых записей этот срез не сделал" in markdown
        assert f"только опора на уже записанное {1}" in markdown
    finally:
        await engine.dispose()


async def test_relied_only_state_matches_the_vitrine_card(migrated_db) -> None:
    """Предикат отчёта обязан совпадать с карточкой `apps/web/answer.py` на одних данных."""
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            card = await question_answer(db, Q_RELIED)
            report = await collect_honesty_report(db, question_ids=[Q_RELIED])
        assert card is not None
        assert card["result"]["kind"] == "answered_from_relied", card["result"]
        assert _find(report, Q_RELIED).answer_state == "relied_only"

        # та же сверка для вопроса со своей записью: карточка `answered`, отчёт `answered`
        async with factory() as db:
            card_own = await question_answer(db, Q_OWN)
            report_own = await collect_honesty_report(db, question_ids=[Q_OWN])
        assert card_own is not None
        assert card_own["result"]["kind"] in {"answered", "reverified", "reused"}, card_own["result"]
        assert _find(report_own, Q_OWN).answer_state == "answered"
    finally:
        await engine.dispose()


# ─── (в) длительность по реальному времени инструментов, иначе — «недоступна» ───────────


async def test_duration_comes_from_action_stamps_not_the_session_row(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        await _seed_actions(engine, S_OWN, (ACTION_FIRST, ACTION_LAST))
        report = await _collect(engine, question_ids=[Q_OWN, Q_RELIED])

        own = _find(report, Q_OWN).sessions[0]
        # 1033 с реальных против 295 «с» из строки `sessions`, зафиксированной одной транзакцией
        assert own.duration_seconds == pytest.approx(1033.0)
        assert own.duration_source == "actions"
        markdown = render_markdown(report)
        assert "длительность: 1033 c" in markdown
        assert "295 c" not in markdown

        # сессия без единого действия со штампом: надёжного источника нет — честная метка
        relied = _find(report, Q_RELIED).sessions[0]
        assert relied.duration_seconds is None
        assert relied.duration_source is None
        assert "длительность недоступна" in markdown
    finally:
        await engine.dispose()


# ─── (г) baseline прежней формы: новая метрика — «нет данных», не 0 и не ошибка ─────────


async def test_baseline_without_new_metric_shows_no_data(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    try:
        await _seed_world(engine)
        report = await _collect(engine, question_ids=[Q_RELIED, Q_OWN])
    finally:
        await engine.dispose()

    old_totals = {key: value for key, value in report.totals.items() if key != "questions_answered_relied_only"}
    assert "questions_answered_relied_only" not in old_totals
    baseline_file = tmp_path / "baseline-t7131.json"
    baseline_file.write_text(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "generated_at": report.generated_at,
                "selection": report.selection,
                "totals": old_totals,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    baseline = load_baseline(str(baseline_file))  # схема та же: аддитивная метрика версию не поднимает
    markdown = render_markdown(report, baseline)
    assert "| questions_answered_relied_only | нет данных | 1 | — |" in markdown
    assert "| questions_answered | 2 | 2 | +0 |" in markdown


# ─── отчёт ничего не пишет (T7.118 инвариант проверен и здесь, на новом коде) ──────────


async def test_collect_does_not_write(migrated_db) -> None:
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    tables = (
        "questions", "sessions", "claims", "claim_assessments", "claim_assessment_heads",
        "model_runs", "actions", "audit_events",
    )
    try:
        await _seed_world(engine)
        await _seed_actions(engine, S_OWN, (ACTION_FIRST, ACTION_LAST))
        before = {table: await _count(engine, table) for table in tables}
        await _collect(engine, question_ids=[Q_RELIED, Q_OWN])
        after = {table: await _count(engine, table) for table in tables}
        assert before == after
    finally:
        await engine.dispose()
