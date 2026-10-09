"""Scenario (fake LLM, curated research): побайтово та же формулировка с другим `claim_type`
в живой сессии (T7.85, ADR-0033 дополнение).

Стендовая форма (.92, сессия a09977e1): одно и то же предложение записано дважды —
`external_fact` 2d010d53 («Прогноз аналитиков … составил 6,3%.») и побайтово identical
`temporal_fact` 58e2d5d4 с той же формулировкой, оценённый отдельно (E1/0.15). На карточке —
две строки с одним текстом и разными бейджами.

Почему прежние механизмы это не ловят:
* побайтовый дедуп T7.9 требует того же `claim_type` → молчит;
* числовой гейт значений T7.83/T7.83a (ADR-0033) тоже требует того же типа → молчит;
* оставаясь «новой» операцией `temporal_fact` без `as_of`, предложение падало на предпроверке
  дат T7.84 (`temporal_as_of_problems`) — то есть второй записи вообще не было бы, а честная
  перепроверка существующего утверждения была бы отвергнута (замер ed36f4a0).

Хост теперь решает раньше: операция без `existing_claim_id`, чья формулировка побайтово совпадает
ровно с одним утверждением контекст-пака, но тип другой, — это ТО ЖЕ утверждение; она конвертируется
в его перепроверку и валидируется/оценивается по типу ЯКОРЯ (T7.34/ADR-0018), поэтому отсутствующая
`as_of` законна (ADR-0018). Фильтр «кандидат обязан быть объявлен в `relied_claim_ids`» (T7.83a) здесь
не нужен: при побайтовом совпадении показатель, период и значения совпадают по построению — проверено
отдельно, опора в этом сценарии НЕ объявлена.

Третий случай — честный отказ склеивать: та же формулировка принесена как ОПРОВЕРЖЕНИЕ якоря.
Связи решают (гейт T7.83), хост не подменяет спор чужой записью; в журнале остаётся причина.

Внешних запросов нет: HTTP-слой research proxy подменён (`FakeFetchClient`). Страницы — на разных
registrable domains (ловушка T7.75 про `example.ru`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_reverify_temporal_scenario import (
    _COMPLETE,
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = pytest.mark.scenario

# ── тексты стенда .92 (фикстура T7.85): identical формулировка при разных типах ──
ANCHOR_FORECAST = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского "
    "макроэкономического опроса Банка России составил 6,3%."
)
#: та же строка байт в байт, другой тип, без даты и без `existing_claim_id`
SAME_TEXT_OTHER_TYPE = ANCHOR_FORECAST

_OFFICIAL_PAGE = "http://cbrforecast.example/dec-macro-survey"
_RETOLD_PAGE = "http://forbesforecast.example/552221"
_DISPUTE_PAGE = "http://disputeforcast.example/comment"

_PAGES: dict[str, str] = {
    _OFFICIAL_PAGE: (
        "December macro survey published by the Central Bank of Russia: analysts' forecast for "
        "annual inflation at the end of 2025 is 6.3 percent."
    ),
    _RETOLD_PAGE: (
        "Analysts expect annual inflation in Russia at 6.3 percent by the end of 2025, according "
        "to the December survey published by the Central Bank of Russia."
    ),
    _DISPUTE_PAGE: (
        "Independent estimate: the reasonable forecast for annual inflation in Russia by the end "
        "of 2025 is 7,9 percent, not what the December survey printed."
    ),
}

#: вопрос сессии 1 цитирует формулировку утверждения — иначе FTS не принесёт её в контекст-пак
#: будущей сессии (ловушка T7.73)
SEED_QUESTION = (
    f"Чего ожидают аналитики по годовой инфляции России на конец 2025 года: {ANCHOR_FORECAST}? "
    f"Источники: {_OFFICIAL_PAGE}"
)

#: вопрос сессии 2 — форма стенда: та же формулировка, спросленная как факт с датой
COMPARE_QUESTION = (
    f"Подтверждается ли прогноз {ANCHOR_FORECAST} независимыми публикациями? "
    "Сопоставь его с другой оценкой того же периода; пересказ опроса Банка России не считай "
    f"независимым источником. Источники: {_RETOLD_PAGE} {_DISPUTE_PAGE}"
)


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 20  # 1 fetch (сессия 1) + 2 (сессия 2)
    return payload


async def _seed_anchor(scratch_url: str, fake_llm: FakeLLM, tmp_path: Path) -> uuid.UUID:
    """Сессия 1: якорь `external_fact` на одном источнике (E1/hypothesis)."""

    qid = await _seed_question(scratch_url, SEED_QUESTION)
    fake_llm.script(
        [
            _fetch(_OFFICIAL_PAGE),
            _COMPLETE,
            {
                "content": {
                    "summary": "Ожидание аналитиков по годовой инфляции.",
                    "claims": [
                        {
                            "statement": ANCHOR_FORECAST,
                            "claim_type": "external_fact",
                            "scope": {},
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"}
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws-seed", tmp_path / "art-seed", qid
    )
    assert outcome.final_state.value == "succeeded"

    row = await _scalar(
        scratch_url,
        "SELECT c.id::text, c.claim_type, a.effective_grade, h.epistemic_status FROM claims c "
        "JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id WHERE c.statement = :t",
        {"t": ANCHOR_FORECAST},
    )
    assert row is not None, "якорь не записан"
    assert (row[1], row[2], row[3]) == ("external_fact", "E1", "hypothesis"), row
    return uuid.UUID(row[0])


def _compare_curator(
    anchor_id: uuid.UUID,
    *,
    relation: str,
    explicit_reverify: bool = False,
    with_date: bool = False,
) -> dict[str, Any]:
    """Ответ куратора сессии 2: та же формулировка, тип `temporal_fact`.

    `explicit_reverify` — куратор сам указал `existing_claim_id` (хост не вмешивается);
    `with_date` — оператор просит дату; по умолчанию её нет: перепроверка без даты законна
    (ADR-0018: дату сохраняет якорь)."""

    claim: dict[str, Any] = {
        "statement": SAME_TEXT_OTHER_TYPE,
        "claim_type": "temporal_fact",
        "scope": {},
    }
    if with_date:
        claim["as_of"] = "2025-12-31T00:00:00"
    if explicit_reverify:
        claim["existing_claim_id"] = str(anchor_id)
    return {
        "summary": (
            "Прогноз 6,3% подтверждается пересказом того же опроса; независимая оценка выше."
        ),
        "claims": [claim],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": relation}],
        # опоры объявленными нет: побайтовое правило работает и без `relied_claim_ids` (T7.83a)
        "new_questions": [],
    }


async def _run_compare_session(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    anchor_id: uuid.UUID,
    *,
    relation: str = "supports",
    explicit_reverify: bool = False,
    with_date: bool = False,
) -> tuple[Any, uuid.UUID]:
    qid = await _seed_question(scratch_url, COMPARE_QUESTION)
    fake_llm.script(
        [
            _fetch(_RETOLD_PAGE),
            _fetch(_DISPUTE_PAGE),
            _COMPLETE,
            {
                "content": _compare_curator(
                    anchor_id,
                    relation=relation,
                    explicit_reverify=explicit_reverify,
                    with_date=with_date,
                )
            },
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws-compare", tmp_path / "art-compare", qid
    )
    return outcome, qid


async def _curator_event_payload(scratch_url: str, session_id: uuid.UUID) -> dict[str, Any] | None:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array' ORDER BY sequence",
        {"s": str(session_id)},
    )
    return dict(rows[0]["payload"]) if rows else None


async def _card_claims(scratch_url: str, tmp_path: Path, qid: uuid.UUID) -> list[dict[str, Any]]:
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{qid}/answer")
            assert response.status_code == 200
            return list(response.json()["claims"])
    finally:
        await app_engine.dispose()


# ── главный случай: identical формулировка с другим типом → перепроверка якоря ──


@pytest.mark.asyncio
async def test_identical_statement_of_other_type_becomes_reverify_of_the_anchor(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchor_id = await _seed_anchor(scratch_url, fake_llm, tmp_path)

    outcome, qid = await _run_compare_session(scratch_url, fake_llm, tmp_path, anchor_id)
    # до правки это предложение отбивалось предпроверкой дат T7.84: новая temporal_fact без as_of
    assert outcome.final_state.value == "succeeded", outcome.termination_reason

    session_id = outcome.session_id

    # (а) второй записи нет: утверждение на карточке ровно одно, тип якоря не изменён
    rows = await _all(
        scratch_url,
        "SELECT c.id::text, c.statement, c.claim_type FROM claims c ORDER BY c.id",
    )
    assert len(rows) == 1, rows
    assert rows[0]["id"] == str(anchor_id) and rows[0]["claim_type"] == "external_fact", rows

    # (б) staging: хост сам проставил перепроверку якоря (куратор её не просил)
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [str(anchor_id)], ops

    # (в) перепроверка записана обычным механизмом, тип и scope якоря сохранены
    reverified = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert len(reverified) == 1, reverified
    assert dict(reverified[0]["payload"])["resolved"] == str(anchor_id)

    # (г) решение хоста записано в журнал вместе с причиной релейбела
    payload = await _curator_event_payload(scratch_url, session_id)
    assert payload is not None
    decisions = payload.get("value_duplicates")
    assert decisions is not None and len(decisions) == 1, payload
    entry = decisions[0]
    assert (entry["claim_index"], entry["action"], entry["target"]) == (0, "reverified", str(anchor_id))
    assert entry["reason"].startswith(
        "same statement, type relabelled temporal_fact → external_fact"
    ), entry["reason"]

    # (д) карточка: формулировка показана один раз и как перепроверено этим вопросом
    claims = await _card_claims(scratch_url, tmp_path, qid)
    relations = {item["statement"]: item["relation"] for item in claims}
    assert relations == {ANCHOR_FORECAST: "reverified"}, relations


# ── контроль: куратор сам указал якорь — хост не вмешивается ──────────────────


@pytest.mark.asyncio
async def test_explicit_existing_claim_id_is_not_touched_by_the_host(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchor_id = await _seed_anchor(scratch_url, fake_llm, tmp_path)

    outcome, qid = await _run_compare_session(
        scratch_url, fake_llm, tmp_path, anchor_id, explicit_reverify=True
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    payload = await _curator_event_payload(scratch_url, session_id)
    assert payload is not None
    decisions = payload.get("value_duplicates") or []
    assert all("type relabelled" not in str(entry["reason"]) for entry in decisions), decisions

    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [str(anchor_id)]
    rows = await _all(scratch_url, "SELECT id::text FROM claims")
    assert len(rows) == 1

    relations = {item["statement"]: item["relation"] for item in await _card_claims(scratch_url, tmp_path, qid)}
    assert relations == {ANCHOR_FORECAST: "reverified"}, relations


# ── честный отказ: та же формулировка принесена как опровержение ───────────────


@pytest.mark.asyncio
async def test_same_text_as_counterevidence_is_not_merged_into_the_anchor(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchor_id = await _seed_anchor(scratch_url, fake_llm, tmp_path)

    outcome, qid = await _run_compare_session(
        scratch_url, fake_llm, tmp_path, anchor_id, relation="counters", with_date=True
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    # спор не превращается в перепроверку чужого утверждения: запись осталась новой операцией
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [None], ops

    payload = await _curator_event_payload(scratch_url, session_id)
    assert payload is not None
    decisions = payload.get("value_duplicates")
    assert decisions is not None and len(decisions) == 1, payload
    entry = decisions[0]
    assert entry["action"] == "kept" and entry["target"] is None, entry
    assert "a dispute stays a dispute" in entry["reason"], entry["reason"]

    # карточка этого вопроса: новая запись с той же формулировкой (хост не склеил спор и не скрыл его);
    # в памяти — два утверждения с одним текстом и разными типами, оба текущие
    relations = {item["statement"]: item["relation"] for item in await _card_claims(scratch_url, tmp_path, qid)}
    assert relations == {ANCHOR_FORECAST: "created"}, relations
    same_text = await _all(
        scratch_url, "SELECT id::text, claim_type FROM claims WHERE statement = :t ORDER BY claim_type",
        {"t": ANCHOR_FORECAST},
    )
    assert [row["claim_type"] for row in same_text] == ["external_fact", "temporal_fact"], same_text
