"""Scenario (DB): устаревшие прогнозы в карточке ответа (T7.135, ADR-0035 §22).

Стендовая карта `c3-a370a68` (срез менеджера, `tests/fixtures/t7135/`) показывала четыре
прогноза инфляции в России на конец 2026 года одним списком без всякого порядка:
ВТБ от 3 октября 2026, ВТБ без даты, SberCIB от 20 октября 2026 и SberCIB от
29 сентября 2026. Читатель не мог понять, какой прогноз актуальный.

Здесь та же форма воспроизводится на базе: карточка вопроса обязана

* назвать самый поздний по дате прогноза записи каждой серии актуальным, более ранний —
  устаревшим с датой более свежего, а запись без даты — записью без даты;
* поставить устаревшую и недатированную запись после актуальной своей серии;
* показать ВСЕ записи: ни одна не исчезает, оценки и связи не меняются, новых строк в базе
  карточка не пишет (только показ, как и свёртка дублей T7.99);
* не трогать карточку без прогнозов — ни одного нового поля в её строках.

Никаких внешних запросов: знание сеждается напрямую в scratch-базу теми же хелперами,
что и остальные карточные тесты.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from tests.scenario.test_web_answer_api import _client, _make, _seed_events, _seed_question, _seed_session
from tests.scenario.test_web_producer_badge import _execute, _worker_assessment
from tests.unit.test_forecast_series_t7135 import (
    ACTUAL_SBER,
    ACTUAL_VTB,
    ALFA_SINGLE,
    CBR_NO_TARGET,
    GPB_SINGLE,
    OUTDATED_SBER,
    SBER_CURRENT,
    SBER_STALE,
    UNDATED_VTB,
    VTB_DATED,
    VTB_UNDATED,
)

pytestmark = [pytest.mark.scenario]

_TABLES = ("claims", "claim_assessments", "claim_assessment_heads", "audit_events", "evidence", "sources")

FORECAST_STATEMENTS = (VTB_DATED, VTB_UNDATED, SBER_CURRENT, SBER_STALE, ALFA_SINGLE, GPB_SINGLE, CBR_NO_TARGET)


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    out: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in _TABLES:
            out[table] = (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
    return out


async def _forecast_claim(
    engine: AsyncEngine,
    *,
    claim_id: uuid.UUID,
    statement: str,
    as_of: str | None,
    created_in_session: uuid.UUID | None = None,
) -> None:
    """Действующая запись прогноза: дата `as_of` — записанный якорь (или его отсутствие),
    `created_in_session` — связь с вопросом (иначе запись попадает в блок «из знаний»)."""
    await _execute(
        engine,
        "INSERT INTO claims (id, statement, claim_type, freshness_status, as_of, created_in_session) "
        "VALUES (:id, :s, 'external_fact', 'unknown', CAST(:d AS DATE), :c)",
        id=claim_id,
        s=statement,
        d=None if as_of is None else dt.date.fromisoformat(as_of),
        c=created_in_session,
    )
    await _worker_assessment(
        engine, claim_id=claim_id, grade="E1", epistemic="hypothesis", confidence=0.15, reasons=[]
    )


async def _card(engine: AsyncEngine) -> tuple[uuid.UUID, dict[str, Any]]:
    """Карточка вопроса: один прогноз записан этим вопросом, шесть — оперты ответа."""
    question_id, session_id = uuid.uuid4(), uuid.uuid4()
    await _seed_question(engine, question_id=question_id, state="verified")
    await _seed_session(
        engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
    )
    await _forecast_claim(
        engine,
        claim_id=VTB_DATED_ID,
        statement=VTB_DATED,
        as_of="2026-10-03",
        created_in_session=session_id,
    )
    relied = [
        (VTB_UNDATED_ID, VTB_UNDATED, None),
        (SBER_CURRENT_ID, SBER_CURRENT, "2026-10-20"),
        (SBER_STALE_ID, SBER_STALE, "2026-09-29"),
        (ALFA_ID, ALFA_SINGLE, "2026-08-01"),
        (GPB_ID, GPB_SINGLE, None),
        (CBR_ID, CBR_NO_TARGET, "2026-08-17"),
    ]
    for claim_id, statement, as_of in relied:
        await _forecast_claim(engine, claim_id=claim_id, statement=statement, as_of=as_of)
    await _seed_events(
        engine,
        session_id,
        [(30, "claim_created", {"relied_claim_ids": [str(claim_id) for claim_id, _, _ in relied]})],
    )
    return question_id, session_id


# id записей фиксированы: порядок блока «Использовано из знаний» идёт по id, и устаревший
# прогноз SberCIB заведомо стоит в нём ПЕРЕД актуальным — витрина обязана их поменять местами.
def _uid(index: int) -> uuid.UUID:
    return uuid.UUID(int=index)


VTB_DATED_ID = _uid(1)
VTB_UNDATED_ID = _uid(2)
SBER_STALE_ID = _uid(3)
SBER_CURRENT_ID = _uid(4)
ALFA_ID = _uid(5)
GPB_ID = _uid(6)
CBR_ID = _uid(7)


def _by_id(items: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    return {str(item["id"]): item for item in items}


@pytest.mark.asyncio
async def test_card_names_the_current_forecast_and_labels_the_stale_ones(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _ = await _card(engine)
        before = await _counts(engine)
        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            knowledge = (await client.get("/api/v1/knowledge/claims?limit=200")).json()
        after = await _counts(engine)

        created = card["claims"][0]
        assert created["statement"] == VTB_DATED
        assert created["forecast_series"] == "actual"
        assert created["forecast_series_label"] == "Актуальный прогноз"
        assert created["forecast_series_hint"]
        assert created["forecast_series_text"] == ACTUAL_VTB

        relied = _by_id(card["relied_claims"])
        assert relied[str(SBER_CURRENT_ID)]["forecast_series_text"] == ACTUAL_SBER
        assert relied[str(SBER_STALE_ID)]["forecast_series_text"] == OUTDATED_SBER
        assert relied[str(SBER_STALE_ID)]["forecast_series"] == "outdated"
        assert relied[str(VTB_UNDATED_ID)]["forecast_series_text"] == UNDATED_VTB
        assert relied[str(VTB_UNDATED_ID)]["forecast_series"] == "undated"

        # сравнения нет: на этой карте у Альфа-Банка, Газпромбанка и Банка России по
        # одной записи (комментарий T7.138: запись Банка России молчит как одиночная,
        # а не из-за отсутствия фразы-горизонта)
        for claim_id in (ALFA_ID, GPB_ID, CBR_ID):
            for key in ("forecast_series", "forecast_series_label", "forecast_series_hint", "forecast_series_text"):
                assert key not in relied[str(claim_id)], (claim_id, key)

        # витрина ничего не скрывает: показаны все семь записей карточки
        assert len(card["claims"]) + len(card["relied_claims"]) == len(FORECAST_STATEMENTS)
        shown = {str(item["statement"]) for item in [*card["claims"], *card["relied_claims"]]}
        assert shown == set(FORECAST_STATEMENTS)

        # знание не тронуто: ни одной новой строки, все записи остаются в списке знаний
        assert before == after, (before, after)
        stored = {str(item["statement"]) for item in knowledge["claims"]}
        assert set(FORECAST_STATEMENTS) <= stored
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_current_forecast_is_shown_before_the_stale_one_of_its_series(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Записи блока идут по id; устаревшая заведомо раньше актуальной — карточка обязана
    поставить устаревшую сразу после актуальной своей серии, не трогая остальные строки."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _ = await _card(engine)
        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
        order = [str(item["id"]) for item in card["relied_claims"]]
        assert order.index(str(SBER_CURRENT_ID)) < order.index(str(SBER_STALE_ID)), order
        assert order.index(str(SBER_STALE_ID)) == order.index(str(SBER_CURRENT_ID)) + 1, order
        # относительный порядок несерийных записей не тронут
        assert [i for i in order if i not in {str(SBER_CURRENT_ID), str(SBER_STALE_ID)}] == [
            str(VTB_UNDATED_ID),
            str(ALFA_ID),
            str(GPB_ID),
            str(CBR_ID),
        ], order
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_card_without_forecasts_gains_no_new_fields(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Карточка без прогнозов остаётся прежней: ни одного нового поля ни в одной строке."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        measured = "Годовая инфляция в России по итогам 2025 года составила 5,6% (декабрь к декабрю)."
        source = "Росстат опубликовал годовую инфляцию в России за 2025 год на уровне 5,6%."
        await _forecast_claim(
            engine, claim_id=_uid(11), statement=measured, as_of="2025-12-31", created_in_session=session_id
        )
        await _forecast_claim(engine, claim_id=_uid(12), statement=source, as_of="2026-01-21")
        await _seed_events(engine, session_id, [(30, "claim_created", {"relied_claim_ids": [str(_uid(12))]})])

        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{question_id}/answer")
            card = response.json()
        assert "forecast_series" not in json.dumps(card, ensure_ascii=False), card.keys()
        assert len(card["claims"]) + len(card["relied_claims"]) == 2
    finally:
        await app_engine.dispose()
