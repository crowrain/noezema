"""Scenario (fake LLM, curated research): дубль по значению в предложении куратора
(T7.83, ADR-0033).

Воспроизводится форма дефекта стенда .92 (сессия f452a978, config-v19): вопрос
сравнивает официальную инфляцию 5,59% с наблюдаемой 14,5%; куратор в `summary`
обещает перепроверку обоих записанных утверждений, но обе операции приносит без
`existing_claim_id`. Byte-exact дедуп T7.9 такую форму не ловит (формулировки
разные), и рядом с E4-утверждением «5,59% … опубликовано 16–21 января 2026»
возникает новый claim «…составила 5,59% (по данным Росстата)» на единственном
пересказе — одно и то же значение одного показателя за тот же период дважды на
одной карточке с разными бейджами.

Хостовый гейт (после гейта перепроверки и разрешения relied-id, до staging):
операция без `existing_claim_id`, у которой ровно один кандидат контекст-пака с
тем же типом, тем же непустым множеством значимых чисел и совместным периодом,
конвертируется в перепроверку кандидата (variant (i) — свежее поддерживающее
evidence есть). Оценка кандидата не понижается: union улик monotонен по
support/groups, якорная дата и scope сохраняются (`packages/memory/reverify.py`).

После гейта:
* новый claim-дубль НЕ создаётся; кандидат получает `claim_reverified` со своей
  прежней датой (предложенная дата refused как `as_of_conflict`) и оценку не ниже
  прежней (здесь — ровно E4/0.95 supported, evergreen);
* утверждение про 14,5% остаётся новым (не дубль: и тип другой у кандидата, и
  значение другое) — со своей честной E1 за единственное evidence;
* payload события `claim_created` несёт `value_duplicates` с целью и причиной;
* карточка показывает значение «5,59» ровно один раз («перепроверено этим
  вопросом»), опертое наблюдение — отдельной связью «использовано из знаний».

Никаких внешних запросов: HTTP-слой research proxy подменён (`FakeFetchClient`).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.web import labels as ui_labels
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

# ── фикстурные тексты стенда .92 (SELECT из noezema-dev) ─────────────────────
CANDIDATE_OFFICIAL = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) "
    "составила 5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
DUP_CLAIM = (
    "Официальная годовая инфляция в России по итогам 2025 года "
    "(декабрь 2025 к декабрю 2024) составила 5,59% (по данным Росстата)."
)
CANDIDATE_OBSERVED = (
    "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года "
    "составила 13,1% для граждан с накоплениями и 15,6% для граждан без накоплений "
    "(по данным опроса «инФОМ»)."
)
NEW_OBSERVED_CLAIM = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года "
    "составила 14,5% (по данным опроса инФОМ для Банка России)."
)

#: опорная дата кандидата — из формы фикстуры («опубликовано 16–21 января 2026»);
#: стендовые часы (.87/.92 и тестовый Postgres) идут в одном 2026-10 времени,
#: predicate покрытия `evidence_at >= claim_day` выполняется на свежих уликах.
OFFICIAL_AS_OF = "2026-01-21T00:00:00"

_PAGES: dict[str, str] = {
    "http://alpha.example/cpi": (
        "Annual consumer price growth in Russia for 2025 (December to December) was 5.59 percent."
    ),
    "http://beta.example/cbr": (
        "Central bank report: yearly inflation in Russia came out at 5.59 percent for 2025."
    ),
    "http://gamma.example/press": (
        "Press release on 2025 results: annual inflation printed as 5.59 percent."
    ),
    "http://delta.example/review": (
        "Independent review of official figures: consumer prices rose by 5.59 percent in 2025."
    ),
    "http://epsilon.example/survey": (
        "infOM survey: observed annual inflation in December 2025 — 13.1 and 15.6 percent."
    ),
    "http://zeta.example/expectations": (
        "Bank of Russia expectations survey for December 2025: observed inflation 14.5 percent."
    ),
    # пересказ официальных данных («по данным Росстата») — как garant.ru в фикстуре
    "http://eta.example/obzor": (
        "По\xa0данным Росстата, годовая инфляция в России по итогам 2025 года "
        "(декабрь к декабрю) составила 5,59 процента."
    ),
}

#: вопрос сессии 1 называет утверждения лексикой и не содержит явной даты d.m.yyyy
ANCHOR_QUESTION = (
    "Годовая инфляция в России по итогам 2025 года и наблюдаемая населением инфляция "
    f"по опросу инФОМ: {CANDIDATE_OBSERVED}? "
    "Источники: http://alpha.example/cpi http://beta.example/cbr http://gamma.example/press "
    "http://delta.example/review http://epsilon.example/survey"
)

#: вопрос сессии 2 — форма фикстуры f452a978 (без собственной даты — case 3 per T7.73)
COMPARE_QUESTION = (
    "Сравни официальную годовую инфляцию в России за 2025 год (Росстат) с наблюдаемой "
    "населением инфляцией по опросу инФОМ для Банка России за декабрь 2025 года: приведи "
    "обе цифры, первоисточник каждой и величину расхождения; пересказы официальных данных "
    "не считай независимыми. "
    "Источники: http://zeta.example/expectations http://eta.example/obzor"
)


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же подменный HTTP-слой, что у тестов research proxy (страницы этого теста
    на семи разных registrable domains)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 20  # 5 fetch (сессия 1) + 2 (сессия 2)
    return payload


async def _seed_corpus(
    scratch_url: str, fake_llm: FakeLLM, tmp_path: Path
) -> tuple[uuid.UUID, uuid.UUID]:
    """Сессия 1: официальный кандидат (temporal_fact, 4 независимых источника → E4/0.95)
    и наблюдательный кандидат (external_fact, один источник → hypothesis)."""
    qid = await _seed_question(scratch_url, ANCHOR_QUESTION)
    fake_llm.script(
        [
            _fetch("http://alpha.example/cpi"),
            _fetch("http://beta.example/cbr"),
            _fetch("http://gamma.example/press"),
            _fetch("http://delta.example/review"),
            _fetch("http://epsilon.example/survey"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Две оценки годовой инфляции.",
                    "claims": [
                        {
                            "statement": CANDIDATE_OFFICIAL,
                            "claim_type": "temporal_fact",
                            "as_of": OFFICIAL_AS_OF,
                            "scope": {},
                        },
                        {
                            "statement": CANDIDATE_OBSERVED,
                            "claim_type": "external_fact",
                            "scope": {},
                        },
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 2, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 3, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 4, "claim_index": 1, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws1", tmp_path / "art1", qid
    )
    assert outcome.final_state.value == "succeeded"

    rows = await _all(
        scratch_url,
        "SELECT c.id::text, c.statement, a.effective_grade, a.epistemic_status, a.confidence::text "
        "FROM claims c JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id",
    )
    by_statement = {row["statement"]: row for row in rows}
    assert set(by_statement) == {CANDIDATE_OFFICIAL, CANDIDATE_OBSERVED}
    # E4/0.95 до гейта: min_grade_for_supported E3 + 4 группы (≥ 2×min_independence_groups) → boost
    official = by_statement[CANDIDATE_OFFICIAL]
    assert (official["effective_grade"], official["epistemic_status"]) == ("E4", "supported")
    assert round(float(official["confidence"]), 4) == 0.95
    observed = by_statement[CANDIDATE_OBSERVED]
    return uuid.UUID(official["id"]), uuid.UUID(observed["id"])


def _compare_curator(
    official_id: uuid.UUID,
    observed_id: uuid.UUID,
    *,
    with_duplicate: bool,
    explicit_reverify: bool = False,
) -> dict[str, Any]:
    """Ответ куратора — форма предложения f452a978 (существующие id взяты из
    контекст-пака). `with_duplicate=False` — контроль без дубля;
    `explicit_reverify=True` — куратор сам оформил перепроверку (гейт не вмешивается)."""
    claims: list[dict[str, Any]] = [
        {
            "statement": NEW_OBSERVED_CLAIM,
            "claim_type": "temporal_fact",
            "as_of": "2025-12-31T00:00:00",
            "scope": {},
        }
    ]
    if with_duplicate and explicit_reverify:
        claims.append(
            {
                "statement": DUP_CLAIM,
                "claim_type": "temporal_fact",
                "as_of": None,
                "existing_claim_id": str(official_id),
                "scope": {},
            }
        )
    elif with_duplicate:
        claims.append(
            {
                "statement": DUP_CLAIM,
                "claim_type": "temporal_fact",
                "as_of": "2025-12-31T00:00:00",
                "scope": {},
            }
        )
    links = [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}]
    if with_duplicate:
        links.append({"evidence_index": 1, "claim_index": 1, "relation": "supports"})
    return {
        "summary": (
            "Сверяются обе оценки: наблюдаемая 14,5% и официальная 5,59% по источнику-пересказу."
        ),
        "claims": claims,
        "evidence_links": links,
        "new_questions": [],
        "relied_claim_ids": [str(official_id), str(observed_id)],
    }


async def _run_compare_session(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    official_id: uuid.UUID,
    observed_id: uuid.UUID,
    *,
    with_duplicate: bool,
    explicit_reverify: bool = False,
) -> Any:
    qid = await _seed_question(scratch_url, COMPARE_QUESTION)
    fake_llm.script(
        [
            _fetch("http://zeta.example/expectations"),
            _fetch("http://eta.example/obzor"),
            _COMPLETE,
            {
                "content": _compare_curator(
                    official_id, observed_id, with_duplicate=with_duplicate,
                    explicit_reverify=explicit_reverify,
                )
            },
        ]
    )
    return await _run_session(
        scratch_url, fake_llm, tmp_path / f"ws{qid.hex[:6]}", tmp_path / f"art{qid.hex[:6]}", qid
    ), qid


async def _curator_event_payload(scratch_url: str, session_id: uuid.UUID) -> dict[str, Any]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array' ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(rows) == 1
    return dict(rows[0]["payload"])


# ── главный сценарий: дубль конвертируется в перепроверку кандидата ──────────

@pytest.mark.asyncio
async def test_value_duplicate_converted_to_reverify_of_candidate(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    official_id, observed_id = await _seed_corpus(scratch_url, fake_llm, tmp_path)

    outcome, qid = await _run_compare_session(
        scratch_url, fake_llm, tmp_path, official_id, observed_id, with_duplicate=True
    )
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    # (а) дубль не создан; новое наблюдение — создание; всего три утверждения
    dup_count = await _scalar(
        scratch_url, "SELECT count(*) FROM claims WHERE statement = :t", {"t": DUP_CLAIM}
    )
    assert dup_count is not None and dup_count[0] == 0
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 3
    new_row = next(row for row in rows if row["statement"] == NEW_OBSERVED_CLAIM)
    new_id = uuid.UUID(new_row["id"])

    # (б) staging: операция-дубль записана перепроверкой кандидата, остальное не тронуто
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [None, str(official_id)]

    # (в) перепроверка записана: дата кандидата сохранена, предложенная — refused
    rev = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert len(rev) == 1
    rv_payload = dict(rev[0]["payload"])
    assert rv_payload["resolved"] == str(official_id)
    assert rv_payload["anchor_kept"] is True
    assert str(rv_payload["stored_as_of"]).startswith("2026-01-21")
    assert "as_of_conflict" in rv_payload

    # (г) кандидат: строка прежняя (as_of), оценка не понижена — ровно E4/0.95 supported,
    #     переоценка этой сессии записана (перепроверка = свежая оценка union-набора)
    after = await _scalar(
        scratch_url,
        "SELECT c.as_of::text, a.effective_grade, a.epistemic_status, a.confidence::text, "
        "       h.current_assessment_id::text, a.created_in_session::text "
        "FROM claims c "
        "JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE c.id = :c",
        {"c": str(official_id)},
    )
    assert after is not None
    assert str(after[0]).startswith("2026-01-21")
    assert (after[1], after[2]) == ("E4", "supported")
    assert round(float(after[3]), 4) == 0.95
    assert after[5] == str(session_id)
    ev_count = await _scalar(
        scratch_url, "SELECT count(*) FROM evidence WHERE claim_id = :c", {"c": str(official_id)}
    )
    assert ev_count is not None and ev_count[0] == 5  # 4 прежних + пересказ

    # (д) новое наблюдение осталось новым и получило честную E1 за единственную улику
    new_grade = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id WHERE h.claim_id = :c",
        {"c": str(new_id)},
    )
    assert new_grade is not None and (new_grade[0], new_grade[1]) == ("E1", "hypothesis")

    # (е) payload события куратора: value_duplicates с целью и причиной; исходное
    #     предложение в аудит не переписывается (existing_claim_id там остаётся null)
    payload = await _curator_event_payload(scratch_url, session_id)
    decisions = payload.get("value_duplicates")
    assert decisions is not None and len(decisions) == 1
    entry = decisions[0]
    assert entry["claim_index"] == 1
    assert entry["target"] == str(official_id)
    assert entry["action"] == "reverified"
    assert entry["reason"]
    dumped_claims = payload["claims"]
    assert dumped_claims[1]["statement"] == DUP_CLAIM
    assert dumped_claims[1].get("existing_claim_id") is None
    assert set(payload) == {
        "claims",
        "evidence_links",
        "new_questions",
        "summary",
        "relied_claim_ids",
        "value_duplicates",
    }

    # (ж) карточка: «5,59» ровно один раз — как перепроверено этим вопросом;
    #     наблюдение 14,5% — создано; опертое наблюдение 13,1/15,6 — «использовано из знаний»
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            card_response = await client.get(f"/api/v1/questions/{qid}/answer")
            assert card_response.status_code == 200
            card = card_response.json()
    finally:
        await app_engine.dispose()

    relations = {item["statement"]: item["relation"] for item in card["claims"]}
    assert relations == {
        CANDIDATE_OFFICIAL: "reverified",
        NEW_OBSERVED_CLAIM: "created",
    }
    official_item = next(item for item in card["claims"] if item["statement"] == CANDIDATE_OFFICIAL)
    assert official_item["relation_label"] == ui_labels.describe("claim_relation", "reverified")["label"]
    assert sum(1 for item in card["claims"] if "5,59" in item["statement"]) == 1
    relied_rows = card["relied_claims"]
    assert [item["id"] for item in relied_rows] == [str(observed_id)]
    assert relied_rows[0]["relation_label"] == "использовано из знаний"

    # кандидата в relied-блоке нет: он уже показан как перепроверенный (T7.82)
    assert str(official_id) not in {item["id"] for item in relied_rows}


# ── контроль: предложение без дублей — payload байт в байт прежний ──────────

@pytest.mark.asyncio
async def test_proposal_without_value_duplicate_keeps_payload_unchanged(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    official_id, observed_id = await _seed_corpus(scratch_url, fake_llm, tmp_path)

    outcome, _qid = await _run_compare_session(
        scratch_url, fake_llm, tmp_path, official_id, observed_id, with_duplicate=False
    )
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    payload = await _curator_event_payload(scratch_url, session_id)
    assert set(payload) == {
        "claims",
        "evidence_links",
        "new_questions",
        "summary",
        "relied_claim_ids",
    }
    # перепроверки нет: кандидат не тронут этой сессией
    rev = await _all(
        scratch_url,
        "SELECT id FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert rev == []
    assessments = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claim_assessments WHERE claim_id = :c AND created_in_session = :s",
        {"c": str(official_id), "s": str(session_id)},
    )
    assert assessments is not None and assessments[0] == 0


# ── контроль: куратор сам указал existing_claim_id — гейт не вмешивается ────

@pytest.mark.asyncio
async def test_explicit_reverify_reference_is_left_to_the_existing_gate(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    official_id, observed_id = await _seed_corpus(scratch_url, fake_llm, tmp_path)

    outcome, _qid = await _run_compare_session(
        scratch_url, fake_llm, tmp_path, official_id, observed_id,
        with_duplicate=True, explicit_reverify=True,
    )
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    payload = await _curator_event_payload(scratch_url, session_id)
    # гейт не трогает операции с явным existing_claim_id и не заводит ключ value_duplicates
    assert "value_duplicates" not in payload
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging "
        "WHERE session_id = :s AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [None, str(official_id)]
    rev = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert len(rev) == 1
    assert dict(rev[0]["payload"])["resolved"] == str(official_id)
