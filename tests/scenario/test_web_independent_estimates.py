"""Scenario (DB): подраздел «Независимые оценки» карточки ответа (T7.88, ADR-0035 вариант D).

Стендовые репродюсеры — те же формулировки и формы улик, что замерены на .92
(STATUS T7.86, `~/fixtures-t786`): `9266248e…` (официальная инфляция 5,59%, кластер Росстата),
`391c4388…` (наблюдаемая населением инфляция 14,5%, опрос инФОМ для ЦБ), `2d010d53…`
(прогноз аналитиков 6,3%), `e189c80d…` (официальная дублирующая запись со ссылкой на Росстат).

Подборщик карточки ничего не оценивает: он перечитывает уже записанные утверждения, их
действующие оценки, запечатанные группы независимости и записи об атрибуции значений
(тот же указатель, что питает подпись T7.87). Оценку кандидата витрина берёт из его собственной
головы — на гипотетическом расходящемся кандидате она «Спорно» потому, что в базе посеяна
оценка `disputed` с опровергающей уликой, а не потому, что витрина так захотела.

Отдельного запроса на утверждение здесь нет: весь пул кандидатов — один SELECT на карточку,
это измеряется тестом бюджета.
"""

from __future__ import annotations

import datetime as dt
import re
import uuid
from typing import Any

import pytest
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web.producer_view import INDEPENDENCE_SHORTFALL_REASONS
from apps.web.reliability import PRODUCER_LABEL
from tests.scenario.test_web_answer_api import _client, _make, _seed_claim
from tests.scenario.test_web_producer_badge import (
    CBR_URI,
    _attribution_scope,
    _current_assessment,
    _evidence,
    _execute,
    _fix_groups,
    _question_with_worker_assessment,
    _seed_rosstat_cluster,
    _source,
    _worker_assessment,
)

pytestmark = [pytest.mark.scenario]

EFF = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"

# ─── подлинные формулировки стенда .92 (fixtures-t786) и гипотезы ADR-0035 §6.3/§6.4 ──

CPI_FULL = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
OBSERVED = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ для Банка России)."
)
FORECAST = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского "
    "макроэкономического опроса Банка России составил 6,3%."
)
OFFICIAL_DUP = (
    "Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59% (по данным Росстата)."
)
# Гипотезы §6.3/§6.4: то же ядро формулировки, другой записанный производитель; независимость
# выводится не из текста, а из указателя атрибуции значения на первоисточник Банка России.
CAND_MATCH = (
    "Банк России: годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59%."
)
CAND_DIVERGE = CAND_MATCH.replace("7,9%", "7,9%").replace("5,59%", "7,9%")
UNSAFE_KNOWN = (
    "Банк России: годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 7,9% (проверено профильным ведомством)."
)
NO_VALUE_KNOWN = "Годовая инфляция в России по итогам 2025 года ускорилась до 8%."

HEADING = "Независимые оценки"
NO_INDEPENDENT = "В знаниях системы нет независимых измерений этого показателя за этот период."

# дословно ожидаемые строки (выведены из словаря подписей; закреплены и в юнит-слое)
ROW_ADJACENT_OBSERVED = (
    "Смежный показатель: Наблюдаемая населением годовая инфляция в России в декабре 2025 года "
    "составила… — другой показатель, не является независимым измерением этого показателя."
)
ROW_FORECAST_2D0 = (
    "Смежный прогноз, не измерение: Прогноз аналитиков по годовой инфляции в России на конец 2025 "
    "года по данным… — прогноз реализованного значения; независимым измерением этого показателя он "
    "не является."
)
ROW_OTHER_MATCH = (
    "Другая запись того же показателя: Годовая инфляция в России по итогам 2025 года (декабрь 2025 "
    "к декабрю 2024)…, значение 5,59% — совпадает с 5,59%"
)
ROW_ADJACENT_OFFICIAL = (
    "Смежный показатель: Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к… "
    "— другой показатель, не является независимым измерением этого показателя."
)
ROW_ADJACENT_CANDIDATE = (
    "Смежный показатель: Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024)… "
    "— другой показатель, не является независимым измерением этого показателя."
)
ROW_INDEP_MATCH = (
    "Независимая оценка того же показателя: Банк России: годовая инфляция в России по итогам 2025 "
    "года (декабрь 2025 к…, значение 5,59% — совпадает с 5,59%"
)
ROW_INDEP_DIVERGE = (
    "Независимая оценка того же показателя: Банк России: годовая инфляция в России по итогам 2025 "
    "года (декабрь 2025 к…, значение 7,9% — расходится на 2,31 п.п."
)

_SHORTFALL = sorted(INDEPENDENCE_SHORTFALL_REASONS)
_VERIFICATION_WORD = re.compile(r"(?<!не )\bпроверен")


# ─── посев утверждений знания без вопроса (кандидатный пул карточки) ──────────


async def _knowledge_claim(
    engine: AsyncEngine, *, statement: str, claim_type: str, as_of: str, reasons: list[str] | None = None
) -> uuid.UUID:
    """Запись знания с действующей оценкой рабочего переоценки: голова `current`, тип и дата как
    на стенде (уровень E1 — как у стендовых утверждений этих карт). Никаких новых колонок —
    только уже существующие таблицы прод-контура."""
    claim_id = uuid.uuid4()
    await _execute(
        engine,
        "INSERT INTO claims (id, statement, claim_type, freshness_status, as_of) "
        "VALUES (:id, :s, :t, 'unknown', CAST(:d AS DATE))",
        id=claim_id,
        s=statement,
        t=claim_type,
        d=dt.date.fromisoformat(as_of),  # asyncpg не принимает строку для date-параметра
    )
    await _worker_assessment(
        engine,
        claim_id=claim_id,
        grade="E1",
        epistemic="hypothesis",
        confidence=0.15,
        reasons=reasons or [],
    )
    return claim_id


async def _attribute_producer(
    engine: AsyncEngine, *, claim_id: uuid.UUID, assessment_id: uuid.UUID, tag: str
) -> uuid.UUID:
    """Улика с ролью `support` и записью об атрибуции значения на первоисточник Банка России —
    ровно в той форме, в какой её пишет прод-контур (тот же scope, что кормит подпись T7.87)."""
    anchor = await _source(engine, uri="https://www.cbr.ru/press/reginfl/?id=64837", tag=f"{tag}-anchor")
    derivative = await _source(
        engine, uri=f"https://example-v2.example/news/{tag}", tag=tag, parent=anchor
    )
    evidence_id = await _evidence(
        engine,
        claim_id=claim_id,
        source_id=derivative,
        tag=f"{tag}-ev",
        scope=_attribution_scope("cbr", "Банк России", CBR_URI, anchor),
    )
    await _execute(
        engine,
        "INSERT INTO assessment_evidence (assessment_id, evidence_id, role) VALUES (:a, :e, 'support')",
        a=assessment_id,
        e=evidence_id,
    )
    return evidence_id


def _card_claim(card: dict[str, Any], statement: str) -> dict[str, Any]:
    matches = [item for item in card["claims"] if item["statement"] == statement]
    assert len(matches) == 1, [item["statement"] for item in card["claims"]]
    return matches[0]


# ─── стендовая карта 7ceb8047: честная строка + смежный 14,5% + помеченный прогноз 6,3% ──


@pytest.mark.asyncio
async def test_stand_card_shape_shows_honest_row_with_marked_adjacents(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _anchor_claim = await _question_with_worker_assessment(
            engine,
            statement=CPI_FULL,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=_SHORTFALL,
        )
        observed = await _knowledge_claim(engine, statement=OBSERVED, claim_type="temporal_fact", as_of="2025-12-31")
        forecast = await _knowledge_claim(engine, statement=FORECAST, claim_type="external_fact", as_of="2025-12-24")

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        estimates = _card_claim(card, CPI_FULL)["estimates"]
        assert estimates["heading"] == HEADING
        rows = estimates["rows"]
        assert [row["kind"] for row in rows] == ["adjacent_metric", "forecast_adjacent", "no_independent"]
        assert [row["text"] for row in rows] == [ROW_ADJACENT_OBSERVED, ROW_FORECAST_2D0, NO_INDEPENDENT]
        # смежный и прогнозный кандидаты показаны вместе со своей подписью надёжности — это их
        # собственная голова, витрина ничего не пересчитывает
        assert rows[0]["claim_id"] == str(observed) and rows[1]["claim_id"] == str(forecast)
        assert rows[0]["reliability"]["label"] == "Подтверждено слабо", rows[0]
        assert rows[1]["reliability"]["label"] == "Подтверждено слабо", rows[1]
        # ни один кандидат не назван независимым измерением (кроме честной строки, где это и есть смысл)
        for row in rows:
            if row["kind"] != "no_independent":
                assert "независ" not in row["text"].split("—")[0]
        assert "claim_id" not in rows[2] and "reliability" not in rows[2]
        # исходная строка карточки живёт по-прежнему: подпись T7.87 не тронута
        badge = _card_claim(card, CPI_FULL)["reliability"]
        assert badge["label"] == "Подтверждено слабо" and "производител" not in badge["hint"], badge
    finally:
        await app_engine.dispose()


# ─── стендовая карта 22d1748e: дублирующая запись того же показателя со значением «совпадает» ──


@pytest.mark.asyncio
async def test_official_duplicate_card_shows_other_record_with_matching_value(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _official_claim = await _question_with_worker_assessment(
            engine,
            statement=OFFICIAL_DUP,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=["insufficient_evidence"],
        )
        async with engine.connect() as conn:
            session_row = (
                await conn.execute(
                    text("SELECT id FROM sessions WHERE question_id = :q LIMIT 1"), {"q": question_id}
                )
            ).scalar_one()
        # второе созданное вопросом утверждение — наблюдаемая инфляция (как на стенде)
        observed_id = uuid.uuid4()
        await _seed_claim(engine, claim_id=observed_id, session_id=session_row, statement=OBSERVED, head_state="none")
        await _execute(
            engine,
            "UPDATE claims SET claim_type = 'temporal_fact', as_of = DATE '2025-12-31' WHERE id = :c",
            c=observed_id,
        )
        await _worker_assessment(
            engine, claim_id=observed_id, grade="E1", epistemic="hypothesis", confidence=0.15, reasons=[]
        )
        # кандидат знания — официальная запись 9266248e с атрибуцией на Росстат (кластер T7.86)
        known = await _knowledge_claim(
            engine, statement=CPI_FULL, claim_type="temporal_fact", as_of="2026-01-21", reasons=_SHORTFALL
        )
        known_assessment = await _current_assessment(engine, known)
        await _seed_rosstat_cluster(engine, claim_id=known, assessment_id=known_assessment)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        official = _card_claim(card, OFFICIAL_DUP)
        rows = official["estimates"]["rows"]
        # официальная запись карточки: дубль 9266248e — «другая запись того же показателя» со
        # значением «совпадает», наблюдаемая инфляция — смежный показатель; независимого измерения
        # нет, но честная строка не нужна (запись того же показателя найдена)
        assert [row["kind"] for row in rows] == ["other_record", "adjacent_metric"]
        # смежный кандидат здесь — наблюдаемая инфляция (её запись карточки)
        assert [row["text"] for row in rows] == [ROW_OTHER_MATCH, ROW_ADJACENT_OBSERVED]
        # слово «независимая» в этой строке запрещено: группы/производители кандидатов пересекаются
        # (атрибуция обеих записей ведёт к Росстату), независимость не выводится
        assert "независим" not in ROW_OTHER_MATCH
        assert rows[0]["claim_id"] == str(known)
        assert rows[0]["reliability"]["label"] == PRODUCER_LABEL, rows[0]  # подпись T7.87 на кандидате
        # строка «нет независимых измерений» здесь не нужна: запись того же показателя найдена
        assert all(row["kind"] != "no_independent" for row in rows)

        observed = _card_claim(card, OBSERVED)
        kinds = [row["kind"] for row in observed["estimates"]["rows"]]
        # оба кандидата (официальная запись карточки и запись знания) — смежные показатели,
        # независимого измерения наблюдаемой инфляции здесь нет: плюс честная строка
        assert kinds == ["adjacent_metric", "adjacent_metric", "no_independent"], observed["estimates"]["rows"]
    finally:
        await app_engine.dispose()


# ─── гипотезы ADR-0035 §6.3 и §6.4: независимость из записанных групп/производителей ──


@pytest.mark.asyncio
async def test_hypothetical_independent_estimates_match_and_diverge(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """§6.3 — вторая независимая запись того же значения («совпадает с 5,59%»); §6.4 — расходящаяся
    оценка 7,9% («расходится на 2,31 п.п.») со своей подписью «Спорно», посеянной в базе."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id = await _question_with_worker_assessment(
            engine,
            statement=CPI_FULL,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=_SHORTFALL,
        )
        assessment_id = await _current_assessment(engine, claim_id)
        await _seed_rosstat_cluster(engine, claim_id=claim_id, assessment_id=assessment_id)

        matching = await _knowledge_claim(engine, statement=CAND_MATCH, claim_type="temporal_fact", as_of="2025-12-31")
        matching_assessment = await _current_assessment(engine, matching)
        await _attribute_producer(engine, claim_id=matching, assessment_id=matching_assessment, tag="cand-match")

        diverging = uuid.uuid4()
        await _execute(
            engine,
            "INSERT INTO claims (id, statement, claim_type, freshness_status, as_of) "
            "VALUES (:id, :s, 'temporal_fact', 'unknown', CAST('2025-12-31' AS DATE))",
            id=diverging,
            s=CAND_DIVERGE,
        )
        diverge_assessment = await _worker_assessment(
            engine, claim_id=diverging, grade="E1", epistemic="disputed", confidence=0.15, reasons=_SHORTFALL
        )
        # атрибуция значения на Банк России — записанный производитель этого кандидата
        await _attribute_producer(engine, claim_id=diverging, assessment_id=diverge_assessment, tag="cand-cbr")
        counter_source = await _source(engine, uri="https://reformer.ru/analysis/inflyaciya-spora", tag="cand-counter")
        counter_evidence = await _evidence(
            engine, claim_id=diverging, source_id=counter_source, tag="cand-counter-ev", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=diverge_assessment,
            members=[(counter_source, "g0", "single")],
            roles={counter_evidence: "counter"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        rows = {row["claim_id"]: row for row in _card_claim(card, CPI_FULL)["estimates"]["rows"]}
        assert set(rows) == {str(matching), str(diverging)}  # честная строка не нужна: оценки найдены

        assert rows[str(matching)]["text"] == ROW_INDEP_MATCH
        assert rows[str(matching)]["kind"] == "independent"
        assert rows[str(matching)]["reliability"]["label"] == "Подтверждено слабо", rows[str(matching)]

        assert rows[str(diverging)]["text"] == ROW_INDEP_DIVERGE
        assert rows[str(diverging)]["kind"] == "independent"
        # «Спорно» — не выдумка витрины: у кандидата посеяна оценка disputed с опровергающей уликой
        assert rows[str(diverging)]["reliability"]["label"] == "Спорно", rows[str(diverging)]

        # и взаимодействие с T7.87: подпись исходной карточки остаётся «Опубликовано производителем»
        badge = _card_claim(card, CPI_FULL)["reliability"]
        assert badge["label"] == PRODUCER_LABEL, badge
    finally:
        await app_engine.dispose()


# ─── честная строка: опасная формулировка и запись без существенного значения ──


@pytest.mark.asyncio
async def test_honest_row_when_only_unsafe_or_valueless_candidates_exist(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _anchor_claim = await _question_with_worker_assessment(
            engine,
            statement=CPI_FULL,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=_SHORTFALL,
        )
        unsafe = await _knowledge_claim(engine, statement=UNSAFE_KNOWN, claim_type="temporal_fact", as_of="2025-12-31")
        valueless = await _knowledge_claim(
            engine, statement=NO_VALUE_KNOWN, claim_type="temporal_fact", as_of="2025-12-31"
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        rows = _card_claim(card, CPI_FULL)["estimates"]["rows"]
        assert [row["kind"] for row in rows] == ["no_independent"]
        assert rows[0]["text"] == NO_INDEPENDENT
        # кандидат со словом «проверено» из карточки исключён целиком (ловушка T7.65),
        # кандидат без десятичного значения гейта — тем более
        cited = {row.get("claim_id") for row in rows}
        assert str(unsafe) not in cited and str(valueless) not in cited
        # ни одна строка подраздела не утверждает проверку
        for row in rows:
            assert not _VERIFICATION_WORD.search(row["text"])
    finally:
        await app_engine.dispose()


# ─── бюджет карточки: пул кандидатов — один запрос, N+1 запрещён ──────────────


@pytest.mark.asyncio
async def test_card_queries_do_not_grow_with_candidate_pool(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")

    statements: list[str] = []

    @event.listens_for(app_engine.sync_engine, "before_cursor_execute")
    def _capture(conn: Any, cursor: Any, stmt: str, params: Any, context: Any, many: Any) -> None:
        statements.append(stmt.strip())

    def _select_count() -> int:
        return sum(1 for stmt in statements if stmt.upper().lstrip().startswith(("SELECT", "WITH")))

    try:
        question_id, _anchor_claim = await _question_with_worker_assessment(
            engine,
            statement=CPI_FULL,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=_SHORTFALL,
        )

        async with _client(app) as client:
            statements.clear()
            await client.get(f"/api/v1/questions/{question_id}/answer")
            no_pool = _select_count()

            for index in range(4):
                variant = f"Годовая инфляция в России за 2025 год (оценка {index}) составила 7,{index + 1}%."
                await _knowledge_claim(
                    engine,
                    statement=variant,
                    claim_type="external_fact",
                    as_of="2025-12-31",
                )
            statements.clear()
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()
            with_pool = _select_count()

            async with engine.connect() as conn:
                session_row = (
                    await conn.execute(
                        text("SELECT id FROM sessions WHERE question_id = :q LIMIT 1"), {"q": question_id}
                    )
                ).scalar_one()
            extra_claim = uuid.uuid4()
            await _seed_claim(
                engine,
                claim_id=extra_claim,
                session_id=session_row,
                statement="Второе утверждение этого же вопроса",
                head_state="none",
            )
            await _worker_assessment(
                engine, claim_id=extra_claim, grade="E1", epistemic="hypothesis", confidence=0.15, reasons=_SHORTFALL
            )
            statements.clear()
            await client.get(f"/api/v1/questions/{question_id}/answer")
            two_claims = _select_count()

        assert len(card["claims"][0]["estimates"]["rows"]) >= 4, card["claims"][0]["estimates"]
        # четыре кандидата добавили строки в подраздел, но не добавили ни одного запроса
        assert with_pool == no_pool, f"N+1 по пулу кандидатов: {no_pool} → {with_pool}"
        assert two_claims == with_pool, f"N+1 по утверждениям карточки: {with_pool} → {two_claims}"
        # Потолок закреплён замером (T7.87: 10 SELECT'ов). Подбор оценок добавляет ровно один
        # фиксированный запрос `WITH pool …` на всю карточку — не на утверждение и не на кандидата.
        assert no_pool <= 11, statements
    finally:
        event.remove(app_engine.sync_engine, "before_cursor_execute", _capture)
        await app_engine.dispose()


# ─── витрина рисует подраздел из API-поля (страница ничего не оценивает) ──────


@pytest.mark.asyncio
async def test_answer_page_renders_section_from_api_field(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, _anchor_claim = await _question_with_worker_assessment(
            engine,
            statement=CPI_FULL,
            grade="E1",
            epistemic="hypothesis",
            confidence=0.15,
            reasons=_SHORTFALL,
        )
        await _knowledge_claim(engine, statement=OBSERVED, claim_type="temporal_fact", as_of="2025-12-31")

        async with _client(app) as client:
            page = (await client.get(f"/answer/{question_id}")).text
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        # сервер отдал поле — страница печатает его как есть, ничего не добавляя от себя
        assert _card_claim(card, CPI_FULL)["estimates"]["heading"] == HEADING
        assert "claim.estimates" in page and "estRow" in page
    finally:
        await app_engine.dispose()
