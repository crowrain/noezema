"""Scenario (DB): свёртка уже записанных дублей на карточке ответа (T7.99, ADR-0033 §13).

Решение пользователя 2026-10-12: повторные записи того же значения показывать на карточке
свёрнутыми под основной — ТОЛЬКО витрина. Знание не трогается: обе записи остаются в базе,
в `/knowledge` и в своих карточках; гейт T7.83 не вызывается, ничего не склеивается,
`backfill` нет (ADR-0033 §12.4).

Репродюсеры — обе пары стенда .92 (фикстуры `fixtures-t799`, срез 2026-10-12):
* карта 22d1748e: `9266248e…` (CPI_FULL, relied, «Опубликовано производителем») — якорь,
  `e189c80d…` (OFFICIAL_DUP, создана вопросом, «Подтверждено слабо») — свёрнута под ним;
* карта c3d5cd03: `c6729d4c…` (6,0–7,0%, relied, «Подтверждено слабо») — якорь,
  `136a0ca0…` (6–7%, создана вопросом, «Опубликовано производителем») — свёрнута под ним.
Якорь — более РАННЯЯ запись (`claims.created_at`), а не более сильная метка: витрина не
имеет права выбирать, чья оценка представляет факт. Метки пары при этом разные — это
записанное расхождение подтверждений (ADR-0035 §12), и картка показывает обе честно.

Зеркальная строка «другая запись того же показателя» пары подавляется: одна и та же пара
на карточке один раз. Отдельных запросов свёртка не добавляет (бюджет T7.74): те же SELECT,
в них добавлены только колонки `as_of` и `assessed_scope`.
"""

from __future__ import annotations

import datetime as dt
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web.producer_view import INDEPENDENCE_SHORTFALL_REASONS
from apps.web.reliability import PRODUCER_LABEL
from tests.scenario.test_web_answer_api import (
    _client,
    _make,
    _seed_claim,
    _seed_events,
    _seed_question,
    _seed_session,
)
from tests.scenario.test_web_independent_estimates import (
    CPI_FULL,
    OBSERVED,
    OFFICIAL_DUP,
    _knowledge_claim,
)
from tests.scenario.test_web_producer_badge import (
    _current_assessment,
    _execute,
    _seed_cbr_cluster,
    _seed_rosstat_cluster,
    _worker_assessment,
)

pytestmark = [pytest.mark.scenario]

_SHORTFALL = sorted(INDEPENDENCE_SHORTFALL_REASONS)

# подлинные формулировки второй пары стенда (fixtures-t799/claims_live.tsv)
CBR_6070 = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
CBR_RAISED = "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%."
SBERCIB_65 = "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года на уровне 6,5%."
# близкие промахи (значения равны, период покрыт — вето показателя/типа сохраняет обе строки)
PRESS_56 = "В пресс-релизе Банка России (event id 28251) годовая инфляция в 2025 году указана как 5,6%."
SBER_56 = (
    "Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: инфляция в России "
    "по итогам 2025 года составила 5,6% — расхождений с официальными данными нет"
)
FORECAST_63 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского "
    "макроэкономического опроса Банка России составил 6,3%."
)

WEAK_LABEL = "Подтверждено слабо"
DUP_TEXT_LEAD = "Повторная запись того же значения: "
DUP_LINK = "открыть повторную запись"
LEAD_ONE = "и 1 повторная запись того же значения"

_TABLES = (
    "claims",
    "claim_assessments",
    "claim_assessment_heads",
    "evidence",
    "audit_events",
    "assessment_evidence",
    "source_independence_members",
    "sources",
)


async def _counts(engine: AsyncEngine) -> dict[str, int]:
    out: dict[str, int] = {}
    async with engine.connect() as conn:
        for table in _TABLES:
            out[table] = (await conn.execute(text(f"SELECT COUNT(*) FROM {table}"))).scalar_one()
    return out


async def _created_claim(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    statement: str,
    claim_type: str,
    as_of: str,
    reasons: list[str],
) -> uuid.UUID:
    """Утверждение, созданное сессией этого вопроса (relation `created`), с действующей
    оценкой рабочего переоценки — та же форма, что на стендовых картах."""
    claim_id = uuid.uuid4()
    await _seed_claim(engine, claim_id=claim_id, session_id=session_id, statement=statement, head_state="none")
    await _execute(
        engine,
        "UPDATE claims SET claim_type = :t, as_of = CAST(:d AS DATE) WHERE id = :c",
        t=claim_type,
        d=dt.date.fromisoformat(as_of),
        c=claim_id,
    )
    await _worker_assessment(
        engine, claim_id=claim_id, grade="E1", epistemic="hypothesis", confidence=0.15, reasons=reasons
    )
    return claim_id


async def _relied_knowledge_claim(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    statement: str,
    claim_type: str,
    as_of: str,
    reasons: list[str],
) -> uuid.UUID:
    """Запись знания, объявленная куратором опорой ответа (`relied_claim_ids` в payload
    события `claim_created`), и заведомо БОЛЕЕ РАННЯЯ по `created_at`, чем записи вопроса
    (стенковые якоры записаны раньше — тест честно откатывает дату, а не меняет витрину)."""
    claim_id = await _knowledge_claim(
        engine, statement=statement, claim_type=claim_type, as_of=as_of, reasons=reasons
    )
    await _execute(engine, "UPDATE claims SET created_at = created_at - INTERVAL '3 days' WHERE id = :c", c=claim_id)
    await _seed_events(
        engine, session_id, [(30, "claim_created", {"relied_claim_ids": [str(claim_id)]})]
    )
    return claim_id


def _statements(items: list[dict[str, Any]]) -> list[str]:
    return [str(item["statement"]) for item in items]


# ─── карта 22d1748e: якорь relied «Опубликовано производителем», дубль создан вопросом ──


@pytest.mark.asyncio
async def test_card_folds_created_duplicate_under_earlier_relied_record(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        observed = await _created_claim(
            engine, session_id=session_id, statement=OBSERVED, claim_type="temporal_fact",
            as_of="2025-12-31", reasons=[],
        )
        duplicate = await _created_claim(
            engine, session_id=session_id, statement=OFFICIAL_DUP, claim_type="temporal_fact",
            as_of="2025-12-31", reasons=["insufficient_evidence"],
        )
        anchor = await _relied_knowledge_claim(
            engine, session_id=session_id, statement=CPI_FULL, claim_type="temporal_fact",
            as_of="2026-01-21", reasons=_SHORTFALL,
        )
        anchor_assessment = await _current_assessment(engine, anchor)
        await _seed_rosstat_cluster(engine, claim_id=anchor, assessment_id=anchor_assessment)

        before = await _counts(engine)
        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            knowledge = (await client.get("/api/v1/knowledge/claims?limit=200")).json()
            claim_page_dup = await client.get(f"/api/v1/knowledge/claims/{duplicate}")
            claim_page_anchor = await client.get(f"/api/v1/knowledge/claims/{anchor}")
        after = await _counts(engine)

        # основной список: наблюдаемая инфляция; дубль убран из `claims`, якорь остался в `relied_claims`
        assert _statements(card["claims"]) == [OBSERVED], card["claims"]
        assert _statements(card["relied_claims"]) == [CPI_FULL], card["relied_claims"]
        primary = card["relied_claims"][0]

        # свёрнутый блок: одна повторная запись, названная счётчиком
        assert primary["duplicates_lead"] == LEAD_ONE
        duplicates = primary["duplicates"]
        assert len(duplicates) == 1
        dup = duplicates[0]
        assert dup["id"] == str(duplicate)
        assert dup["statement"] == OFFICIAL_DUP
        assert dup["text"] == DUP_TEXT_LEAD + OFFICIAL_DUP
        assert dup["duplicate_label"] == "Повторная запись того же значения"
        assert dup["duplicate_hint"]
        assert dup["link_label"] == DUP_LINK
        # своя связь с вопросом и СВОЯ оценка/метка сохранены: витрина не подменяет их
        assert dup["relation_label"] == "создано этим вопросом"
        assert dup["reliability"]["label"] == WEAK_LABEL, dup["reliability"]
        # якорь выбран по хронологии, а не по силе метки: у якоря — подпись производителя
        assert primary["reliability"]["label"] == PRODUCER_LABEL, primary["reliability"]
        assert "Росстат" in primary["reliability"]["hint"]

        # зеркальная строка пары подавлена: «другая запись того же показателя» на дубль
        kinds = [(row["kind"], row.get("claim_id")) for row in primary["estimates"]["rows"]]
        assert all(
            kind != "other_record" or claim_id != str(duplicate) for kind, claim_id in kinds
        ), kinds
        # свёрнутая запись не получает собственный подраздел (пара показана один раз)
        assert "estimates" not in dup

        # дубль не «потерян»: он по-прежнему кандидат в подразделе другой записи карточки
        observed_rows = card["claims"][0]["estimates"]["rows"]
        assert str(duplicate) in {row.get("claim_id") for row in observed_rows}, observed_rows

        # честный итог ответа учитывает свёрнутую запись: вопрос остался «отвечен»
        assert card["result"]["kind"] == "answered", card["result"]

        # знание не тронуто: обе записи в списке и в своих карточках; ни одной новой строки БД
        statements = {item["statement"] for item in knowledge["claims"]}
        assert CPI_FULL in statements and OFFICIAL_DUP in statements
        assert claim_page_dup.status_code == 200 and claim_page_anchor.status_code == 200
        assert before == after, (before, after)
        assert observed  # наблюдаемая инфляция — отдельная запись, свёртка её не касается
    finally:
        await app_engine.dispose()


# ─── карта c3d5cd03: якорь relied «Подтверждено слабо», дубль со знаком производителя ────


@pytest.mark.asyncio
async def test_anchor_is_earlier_record_even_when_its_label_weaker(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Якорь — ранняя запись, даже когда её метка СЛАБЕЕ метки дубля: витрина не имеет
    права решать, чья оценка представляет факт (иначе слабая честная оценка молча
    заменялась бы сильной — ровно то, чего запретил пользователь)."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        raised = await _created_claim(
            engine, session_id=session_id, statement=CBR_RAISED, claim_type="external_fact",
            as_of="2026-07-31", reasons=_SHORTFALL,
        )
        raised_assessment = await _current_assessment(engine, raised)
        await _seed_cbr_cluster(engine, claim_id=raised, assessment_id=raised_assessment)
        other = await _created_claim(
            engine, session_id=session_id, statement=SBERCIB_65, claim_type="external_fact",
            as_of="2026-12-31", reasons=[],
        )
        anchor = await _relied_knowledge_claim(
            engine, session_id=session_id, statement=CBR_6070, claim_type="external_fact",
            as_of="2026-07-31", reasons=["insufficient_evidence"],
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        assert _statements(card["claims"]) == [SBERCIB_65], card["claims"]
        primary = card["relied_claims"][0]
        assert primary["statement"] == CBR_6070
        assert primary["reliability"]["label"] == WEAK_LABEL, primary["reliability"]
        assert primary["duplicates_lead"] == LEAD_ONE
        dup = primary["duplicates"][0]
        assert dup["id"] == str(raised) and dup["id"] != str(other)
        # дубль сохранил свою сильную метку — она не переехала на якорь и не пропала
        assert dup["reliability"]["label"] == PRODUCER_LABEL, dup["reliability"]
        assert dup["text"] == DUP_TEXT_LEAD + CBR_RAISED
        assert dup["relation_label"] == "создано этим вопросом"
        assert primary["duplicates"][0]["id"] != str(other)  # 6,5% SberCIB — не пара
        assert str(anchor) == primary["id"]
        assert card["result"]["kind"] == "answered", card["result"]
    finally:
        await app_engine.dispose()


# ─── ложных свёрток нет: вето показателя и равенство типов работают как у гейта ─────────


@pytest.mark.asyncio
async def test_near_miss_pairs_stay_separate_rows(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Две записи «5,6%» (пресс-релиз ЦБ и оценки Сбера): тип равен, значение {5,6} равно,
    период покрыт — но словарь показателей пересекается слабее порога (вето T7.83a/T7.96).
    Байт-в-байт одинаковые формулировки прогноза 6,3% разного типа (external_fact/temporal_fact,
    прецедент T7.9) — не пара (условие равенства типа). Все четыре строки остаются."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        press = await _created_claim(
            engine, session_id=session_id, statement=PRESS_56, claim_type="external_fact",
            as_of="2025-12-31", reasons=[],
        )
        sber = await _created_claim(
            engine, session_id=session_id, statement=SBER_56, claim_type="external_fact",
            as_of="2025-12-31", reasons=[],
        )
        forecast_ext = await _created_claim(
            engine, session_id=session_id, statement=FORECAST_63, claim_type="external_fact",
            as_of="2025-12-31", reasons=[],
        )
        forecast_tmp = await _created_claim(
            engine, session_id=session_id, statement=FORECAST_63, claim_type="temporal_fact",
            as_of="2025-12-31", reasons=[],
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        assert _statements(card["claims"]) == [PRESS_56, SBER_56, FORECAST_63, FORECAST_63], card["claims"]
        assert all("duplicates" not in item and "duplicates_lead" not in item for item in card["claims"])
        assert card["result"]["kind"] == "answered", card["result"]
        assert {str(x) for x in (press, sber, forecast_ext, forecast_tmp)} == {
            item["id"] for item in card["claims"]
        }
    finally:
        await app_engine.dispose()


# ─── карточка без дублей не меняется вовсе (охрана прежних тестов витрины) ──────────────


@pytest.mark.asyncio
async def test_card_without_duplicates_has_no_duplicate_fields(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Прежняя guard-карточка T7.88 (`test_official_duplicate_card_…`): дубль знания НЕ на
    карте (кандидатный пул) — свёртка к нему не применяется, строка `other_record` на месте,
    полей свёртки нет. Тот же тест в прежнем файле остаётся зелёным без правок."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        official = await _created_claim(
            engine, session_id=session_id, statement=OFFICIAL_DUP, claim_type="temporal_fact",
            as_of="2025-12-31", reasons=["insufficient_evidence"],
        )
        # кандидат знания — та же запись-дубль 9266248e, НО не на карте: не создана вопросом
        # и не объявлена опорой — свёртка к кандидатам пула не применяется
        known = await _knowledge_claim(
            engine, statement=CPI_FULL, claim_type="temporal_fact", as_of="2026-01-21", reasons=_SHORTFALL
        )
        known_assessment = await _current_assessment(engine, known)
        await _seed_rosstat_cluster(engine, claim_id=known, assessment_id=known_assessment)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        item = card["claims"][0]
        assert item["id"] == str(official)
        assert "duplicates" not in item and "duplicates_lead" not in item
        rows = item["estimates"]["rows"]
        other = [row for row in rows if row["kind"] == "other_record"]
        assert [row["claim_id"] for row in other] == [str(known)], rows
    finally:
        await app_engine.dispose()

# ─── свёрнутая запись не исчезает из честного итога ответа ─────────────────────────


@pytest.mark.asyncio
async def test_folded_only_created_claim_keeps_the_answer_answered(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Единственная запись, созданную вопросом, сворачиваем под более ранней опорой.
    Если бы свёртка «забыла» её в честном счётчике, итог стал бы «Ответ не записан» —
    неправда: запись на месте, просто показана свёрнутой."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, session_id = uuid.uuid4(), uuid.uuid4()
        await _seed_question(engine, question_id=question_id, state="verified")
        await _seed_session(
            engine, session_id=session_id, question_id=question_id, state="succeeded", termination_reason="goal_reached"
        )
        duplicate = await _created_claim(
            engine, session_id=session_id, statement=OFFICIAL_DUP, claim_type="temporal_fact",
            as_of="2025-12-31", reasons=["insufficient_evidence"],
        )
        anchor = await _relied_knowledge_claim(
            engine, session_id=session_id, statement=CPI_FULL, claim_type="temporal_fact",
            as_of="2026-01-21", reasons=_SHORTFALL,
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        assert card["claims"] == []  # созданная запись свёрнута под якорем
        primary = card["relied_claims"][0]
        assert str(anchor) == primary["id"]
        assert [dup["id"] for dup in primary["duplicates"]] == [str(duplicate)]
        assert card["result"]["kind"] == "answered", card["result"]
        assert card["result"]["label"] == "Ответ есть", card["result"]
    finally:
        await app_engine.dispose()
