"""Scenario (DB): единственная улика — публикация производителя (T7.100, ADR-0035 §18).

Репродюсеры стенда .92 (фикстура `~/fixtures-t7100`, карта 22d1748e):

* `6d9a12ff…` «Медианная оценка наблюдаемой инфляции …» — ОДНА улика (пересказ forbes.ru),
  якорь-родитель — документ Банка России (`cbr.ru/Content/…/forecast_260724.pdf`), одна
  записанная группа `parent:`; оценка E1/0,15 hypothesis записана рабочим переоценки с
  причиной `insufficient_evidence`. До правки витрина молчала «Подтверждено слабо», хотя
  подтверждение есть — публикация производителя и её пересказ. (С T7.116 (часть б) эта строка
  вернулась к «Подтверждено слабо»: утверждение производителя не называет — см. абзац ниже.)
* `7cc2b578…` — одна улика (sbercib.ru) с записью об атрибутции значения на Росстат;
  та же причина оценки.

Что проверяется: метка не зависит от того, какой из двух кодов нехватки подтверждений
записали правила; уровень, цвет, grade и причины оценки не меняются; там, где записанного
указателя нет (собственная оценка третьего актора, страница самого ЦБ без записанного
решения о происхождении значения), витрина молчит по-прежнему (fail-closed). Карточка,
список знаний, карточка утверждения и список вопросов показывают один и тот же бейдж.

T7.116 (часть б), ADR-0035: указатель строки называет производителя ТОЛЬКО если его словарь
стоит в самом утверждении. Репродюсер `6d9a12ff…` («Медианная оценка наблюдаемой инфляции …»
— в утверждении производителя нет) был подписан «Опубликовано производителем» по одному лишь
указателю на документ ЦБ; теперь он честный «Подтверждено слабо», как и стендовой случай
`85220305…`. Строки с записанной атрибуцией значения (`7cc2b578…`) и строки, где утверждение
называет производителя, подписываются по-прежнему — за это отвечают
`test_card_labels_single_evidence_with_recorded_attribution` и
`test_named_producer_in_statement_keeps_the_single_publication_label`.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.web.labels import MAX_HINT_CHARS
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL
from tests.scenario.test_web_answer_api import _client, _make
from tests.scenario.test_web_producer_badge import (
    _attribution_scope,
    _current_assessment,
    _evidence,
    _fix_groups,
    _question_with_worker_assessment,
    _source,
)

pytestmark = [pytest.mark.scenario]

WEAK_LABEL = "Подтверждено слабо"
FEW_EVIDENCE = ["insufficient_evidence"]
INDEPENDENCE_SHORTFALL = ["insufficient_independence"]

CBR_URI = "https://cbr.ru/"
ROSSTAT_URI = "https://rosstat.gov.ru/"
CBR_FORECAST_PDF = "https://www.cbr.ru/Content/Document/File/194258/forecast_260724.pdf"

FORBES = "https://www.forbes.ru/finansy/552221-cb-soobsil-o-roste-inflyatsionnyh-ozhidaniy"
SBERCIB = "https://sbercib.ru/publication/inflyatsiya-v-rossii-v-2026-godu"
ALFA = "https://internationalinvestment.biz/analytics/8583-alfa-bank-prognoz-inflyacii"
CBR_PAGE = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/"


async def _single_evidence_claim(
    engine: AsyncEngine,
    *,
    statement: str,
    reasons: list[str],
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """Утверждение вопроса с действующей оценкой рабочего (`insufficient_evidence`) и без улик:
    улики и группы добавляет конкретный тест."""
    question_id, claim_id = await _question_with_worker_assessment(
        engine,
        statement=statement,
        grade="E1",
        epistemic="hypothesis",
        confidence=0.15,
        reasons=reasons,
    )
    assessment_id = await _current_assessment(engine, claim_id)
    return question_id, claim_id, assessment_id


# ─── репродюсер 6d9a12ff: одна улика с якорем-родителем на документ ЦБ ─────────


@pytest.mark.asyncio
async def test_card_labels_single_retelling_with_producer_anchor(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Медианная оценка годовой наблюдаемой инфляции населением в декабре 2025 года составила 13,1%",
            reasons=FEW_EVIDENCE,
        )
        anchor = await _source(engine, uri=CBR_FORECAST_PDF, tag="cbr-anchor")
        source_id = await _source(engine, uri=FORBES, tag="forbes", parent=anchor)
        evidence_id = await _evidence(
            engine, claim_id=claim_id, source_id=source_id, tag="forbes-e", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(source_id, "g0", f"parent:{anchor}")],
            roles={evidence_id: "support"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        item = card["claims"][0]
        badge = item["reliability"]
        # T7.116 (часть б), ADR-0035: было — указатель строки называл «Банк России» и давал
        # метку «Опубликовано производителем», хотя утверждение никакого производителя не
        # называет (стенд 85220305 с его «Опубликовано производителем» по чужому указателю).
        # Стало — указатель имени не даёт: метка прежняя честная, уровня и причин нет изменений.
        assert badge["label"] == WEAK_LABEL, badge
        assert "Банк России" not in badge["hint"], badge
        assert "одна публикация" not in badge["hint"], badge
        assert badge["level"] == LEVEL_WEAK, badge
        assert len(badge["hint"]) <= MAX_HINT_CHARS, badge["hint"]
        # причина оценки — та же записанная, только подписанная словарём
        assert [r["code"] for r in item["grade_reasons"]] == FEW_EVIDENCE, item
        assert item["grade_reason_lines"] == ["причина: доказательств меньше нужного"], item
        assert item["verification_lead"] == "чем подтверждено", item
    finally:
        await app_engine.dispose()


# ─── репродюсер 7cc2b578: одна улика с записью об атрибутции значения ──────────


@pytest.mark.asyncio
async def test_card_labels_single_evidence_with_recorded_attribution(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Фактическая инфляция 2025 года (5,59%) оказалась ниже всех прогнозов аналитиков",
            reasons=FEW_EVIDENCE,
        )
        anchor = await _source(engine, uri="https://rosstat.gov.ru/press/cpi-2025.html", tag="rosstat-anchor")
        source_id = await _source(engine, uri=SBERCIB, tag="sbercib")
        evidence_id = await _evidence(
            engine,
            claim_id=claim_id,
            source_id=source_id,
            tag="sbercib-e",
            scope=_attribution_scope("rosstat", "Росстат", ROSSTAT_URI, anchor),
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(source_id, "g0", f"parent:{anchor}")],
            roles={evidence_id: "support"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] == PRODUCER_LABEL, badge
        assert "Росстат" in badge["hint"] and "одна публикация" in badge["hint"], badge
    finally:
        await app_engine.dispose()


# ─── fail-closed: называть производителя нечем — прежняя подпись ───────────────


@pytest.mark.asyncio
async def test_single_third_party_assessment_without_pointer_stays_weak(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Собственная оценка третьего актора (репродюсер 07126d06, Альфа-Банк): улика на чужой
    странице, ни атрибутции, ни якоря — «Опубликовано производителем» была бы выдумкой."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%",
            reasons=FEW_EVIDENCE,
        )
        source_id = await _source(engine, uri=ALFA, tag="alfa")
        evidence_id = await _evidence(
            engine, claim_id=claim_id, source_id=source_id, tag="alfa-e", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(source_id, "g0", "single")],
            roles={evidence_id: "support"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] == WEAK_LABEL, badge
        assert "производител" not in badge["hint"], badge
        assert badge["level"] == LEVEL_WEAK, badge
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_producer_own_page_without_recorded_pointer_stays_weak(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Страница самого ЦБ без записанного решения о происхождении значения (репродюсер
    c6729d4c): хост прочитанной страницы указателем не считается — случай не признан."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%",
            reasons=INDEPENDENCE_SHORTFALL,
        )
        members: list[tuple[uuid.UUID, str, str]] = []
        roles: dict[uuid.UUID, str] = {}
        for index, uri in enumerate((CBR_PAGE, "https://www.cbr.ru/analytics/dkp/inflationary_expectations/")):
            source_id = await _source(engine, uri=uri, tag=f"cbr-{index}")
            evidence_id = await _evidence(
                engine, claim_id=claim_id, source_id=source_id, tag=f"cbr-e{index}", scope='{"x": 1}'
            )
            roles[evidence_id] = "support"
            members.append((source_id, "g0", "domain:cbr.ru"))
        await _fix_groups(engine, assessment_id=assessment_id, members=members, roles=roles)

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        badge = card["claims"][0]["reliability"]
        assert badge["label"] == WEAK_LABEL, badge
        assert "производител" not in badge["hint"], badge
    finally:
        await app_engine.dispose()


# ─── витрины не расходятся: список знаний, карточка утверждения, список вопросов ───


@pytest.mark.asyncio
async def test_knowledge_list_and_claim_detail_carry_the_same_single_hint(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Медианная оценка годовой наблюдаемой инфляции населением в декабре 2025 года составила 13,1%",
            reasons=FEW_EVIDENCE,
        )
        anchor = await _source(engine, uri=CBR_FORECAST_PDF, tag="cbr-anchor")
        source_id = await _source(engine, uri=FORBES, tag="forbes", parent=anchor)
        evidence_id = await _evidence(
            engine, claim_id=claim_id, source_id=source_id, tag="forbes-e", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(source_id, "g0", f"parent:{anchor}")],
            roles={evidence_id: "support"},
        )

        async with _client(app) as client:
            listing = (await client.get("/api/v1/knowledge/claims?limit=50")).json()
            detail = (await client.get(f"/api/v1/knowledge/claims/{claim_id}")).json()
            questions = (await client.get("/api/v1/questions")).json()

        listed = [item for item in listing["claims"] if item["id"] == str(claim_id)]
        assert len(listed) == 1, listing
        badge = listed[0]["reliability"]
        # T7.116 (часть б): тот же флип, что у карточки ответа, во всех трёх витринах сразу —
        # указатель на страницу производителя не называет его в утверждении, не называет и бейдж.
        assert badge["label"] == WEAK_LABEL, badge
        assert "Банк России" not in badge["hint"] and "одна публикация" not in badge["hint"], badge
        assert badge["level"] == LEVEL_WEAK, badge

        assert detail["reliability"]["label"] == WEAK_LABEL, detail["reliability"]
        assert detail["reliability"]["hint"] == badge["hint"], detail["reliability"]

        rows = [row for row in questions["questions"] if row["id"] == str(question_id)]
        assert len(rows) == 1, questions
        summary = rows[0]["answer"]
        assert summary["reliability"]["label"] == WEAK_LABEL, summary
        assert summary["reliability"]["hint"] == badge["hint"], summary
    finally:
        await app_engine.dispose()


@pytest.mark.asyncio
async def test_named_producer_in_statement_keeps_the_single_publication_label(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Any
) -> None:
    """Контроль к двум предыдущим тестам (T7.116 часть б): та же конструкция улики и указателя,
    но утверждение называет производителя — метка «Опубликовано производителем» и честная
    форма «одна публикация» остаются. Гвард снимает имя с указателя только когда сам текст
    утверждения производителя не называет."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        question_id, claim_id, assessment_id = await _single_evidence_claim(
            engine,
            statement="Инфляция в России в декабре 2025 года составила 13,1% (по данным Банка России)",
            reasons=FEW_EVIDENCE,
        )
        anchor = await _source(engine, uri=CBR_FORECAST_PDF, tag="cbr-anchor")
        source_id = await _source(engine, uri=FORBES, tag="forbes", parent=anchor)
        evidence_id = await _evidence(
            engine, claim_id=claim_id, source_id=source_id, tag="forbes-e", scope='{"x": 1}'
        )
        await _fix_groups(
            engine,
            assessment_id=assessment_id,
            members=[(source_id, "g0", f"parent:{anchor}")],
            roles={evidence_id: "support"},
        )

        async with _client(app) as client:
            card = (await client.get(f"/api/v1/questions/{question_id}/answer")).json()

        item = card["claims"][0]
        badge = item["reliability"]
        assert badge["label"] == PRODUCER_LABEL, badge
        assert "Банк России" in badge["hint"], badge
        # честная форма единственной улики: одна публикация, а не «все подтверждения — кластер»
        assert "одна публикация" in badge["hint"] and "кластер" not in badge["hint"], badge
        assert "истинность не проверена" in badge["hint"], badge
        assert badge["level"] == LEVEL_WEAK, badge
        assert len(badge["hint"]) <= MAX_HINT_CHARS, badge["hint"]
    finally:
        await app_engine.dispose()
