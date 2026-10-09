"""T7.84 (часть B): датless-пересказ куратора склеивается ПЕРЕД проверкой опорной даты.

Стендовая сессия ed36f4a0 (.92, config-v20, curator-v10, 2026-10-09): исследователь ответил
верно (5,59% Росстата; 14,5% наблюдаемой инфляции по опросу инФОМ из обзора ЦБ), а куратор
принёс два `temporal_fact` БЕЗ `as_of` и БЕЗ `existing_claim_id` — пересказы уже записанных
утверждений `9266248e…` (5,59%, as_of 2026-01-21) и `391c4388…` (14,5%, as_of 2025-12-31).
Гейт `validate_against` стоял ДО гейта перепроверок и гейта дублей по значению, поэтому
«temporal_fact requires as_of» отклонил всё предложение: 0 утверждений, вопрос
`partially_answered`, карточка «ответа нет». Гейт T7.83a превратил бы эти операции в
перепроверки (где дату опустить можно — дату якоря сохраняет `packages/memory/reverify.py`,
T7.73), но был недостижим.

Проверяются оба варианта реального контекста стенда: в пакет этой сессии попали ОБА
5,59%-claim'а (`9266248e…` и записанный ранее дубль `e189c80d…`). Если куратор объявил
опорой оба — гейт видит двух строгих кандидатов, оставляет операцию как предложено, и
отсутствие даты по-прежнему роняет всё предложение (политику отказа часть B не меняет),
теперь с дайджестом. Если только один — склейка.

Порядок после правки: структурные проверки (индексы связей, бюджеты, пустые search_statements)
— первыми; проверка «temporal_fact требует as_of» — после разрешения перепроверок, relied-id
и гейта дублей по значению, то есть после того, как вид операции определён окончательно.
"""

from __future__ import annotations

import copy
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

# ── тексты стенда .92 (SELECT из noezema-dev, фикстуры fixtures-t784/fixtures-t783) ──
CANDIDATE_OFFICIAL = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59% "
    "(Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
CANDIDATE_OBSERVED = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ для Банка России)."
)
#: дубль по значению, записанный ПРЕЖНЕЙ сессией (e189c80d…): то же значение того же показателя
DUP_559 = "Официальная годовая инфляция в России за 2025 год составила 5,59% (по данным Росстата)."

#: пересказы куратора ed36f4a0: те же значения, та же формулировка показателя — но БЕЗ даты
RESTATED_OFFICIAL = (
    "Официальная годовая инфляция в России по итогам 2025 года (декабрь к декабрю) составила "
    "5,59% (по данным Росстата)."
)
RESTATED_OBSERVED = (
    "Наблюдаемая населением годовая инфляция в России по опросу инФОМ для Банка России в декабре "
    "2025 года составила 14,5%."
)
#: новое утверждение: значения нет ни у одного кандидата — гейт дублей молчит
NEW_RATE = "Средняя ключевая ставка Банка России за 2025 год составила 21,5%."

OFFICIAL_AS_OF = "2026-01-21T00:00:00"
OBSERVED_AS_OF = "2025-12-31T00:00:00"

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
        "infOM survey published by the central bank: observed annual inflation in December 2025 "
        "was 14.5 percent."
    ),
    "http://zeta.example/obzor": (
        "По\xa0данным Росстата, годовая инфляция в России по итогам 2025 года (декабрь к декабрю) "
        "составила 5,59 процента."
    ),
}

#: вопрос называет оба утверждения лексикой — иначе они не попадут в контекст-пак (T7.73),
#: и собственная дата вопроса отсутствует: ровно та форма, которая стирала якорь (T7.73).
SEED_QUESTION = (
    "Годовая инфляция в России по итогам 2025 года (декабрь к декабрю) и наблюдаемая населением "
    "инфляция по опросу инФОМ для Банка России в декабре 2025 года: приведи официальные данные "
    "и независимую оценку. Источники: http://alpha.example/cpi http://beta.example/cbr "
    "http://gamma.example/press http://delta.example/review http://epsilon.example/survey "
    "http://zeta.example/obzor"
)
COMPARE_QUESTION = (
    "Сравни официальную годовую инфляцию в России по итогам 2025 года (Росстат) с наблюдаемой "
    "населением инфляцией по опросу инФОМ для Банка России за декабрь 2025 года: приведи обе "
    "цифры, первоисточник каждой и величину расхождения; пересказы официальных данных не считай "
    "независимыми. Источники: http://epsilon.example/survey http://zeta.example/obzor"
)

_GRADE_RANK = {"E0": 0, "E1": 1, "E2": 2, "E3": 3, "E4": 4, "E5": 5}


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Подменный HTTP-слой research proxy (страницы на шести разных registrable domains)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 20  # 6 fetch (посев) + 2 (сессия сравнения)
    return payload


async def _assessment(scratch_url: str, claim_id: uuid.UUID) -> dict[str, Any]:
    rows = await _all(
        scratch_url,
        "SELECT c.as_of::text AS as_of, a.effective_grade AS grade, a.epistemic_status AS status, "
        "a.confidence::text AS confidence FROM claims c "
        "JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id WHERE c.id = :c",
        {"c": str(claim_id)},
    )
    assert len(rows) == 1
    return dict(rows[0])


async def _seed_candidates(scratch_url: str, fake_llm: FakeLLM, tmp_path: Path) -> dict[str, uuid.UUID]:
    """Посев знания стенда: официальный кандидат (temporal_fact, as_of 2026-01-21, четыре
    независимых источника → E4), наблюдательный кандидат (temporal_fact, as_of 2025-12-31) и
    дубль по значению 5,59% (e189c80d… — записан ПРЕЖДЕ, чем куратор принёс пересказ)."""
    question_id = await _seed_question(scratch_url, SEED_QUESTION)
    fake_llm.script(
        [
            *[_fetch(url) for url in (
                "http://alpha.example/cpi",
                "http://beta.example/cbr",
                "http://gamma.example/press",
                "http://delta.example/review",
                "http://epsilon.example/survey",
                "http://zeta.example/obzor",
            )],
            _COMPLETE,
            {
                "content": {
                    "summary": "Официальная и наблюдаемая оценки годовой инфляции.",
                    "claims": [
                        {"statement": CANDIDATE_OFFICIAL, "claim_type": "temporal_fact", "as_of": OFFICIAL_AS_OF},
                        {"statement": CANDIDATE_OBSERVED, "claim_type": "temporal_fact", "as_of": OBSERVED_AS_OF},
                        {"statement": DUP_559, "claim_type": "temporal_fact", "as_of": OBSERVED_AS_OF},
                    ],
                    "evidence_links": [
                        {"evidence_index": i, "claim_index": 0, "relation": "supports"} for i in range(4)
                    ]
                    + [
                        {"evidence_index": 4, "claim_index": 1, "relation": "supports"},
                        {"evidence_index": 5, "claim_index": 2, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws1", tmp_path / "art1", question_id)
    assert outcome.final_state.value == "succeeded"

    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims ORDER BY statement")
    by_statement = {row["statement"]: uuid.UUID(row["id"]) for row in rows}
    assert set(by_statement) == {CANDIDATE_OFFICIAL, CANDIDATE_OBSERVED, DUP_559}
    official = await _assessment(scratch_url, by_statement[CANDIDATE_OFFICIAL])
    assert (official["grade"], official["status"]) == ("E4", "supported")
    return {
        "official": by_statement[CANDIDATE_OFFICIAL],
        "observed": by_statement[CANDIDATE_OBSERVED],
        "dup": by_statement[DUP_559],
    }


def _curator_answer(
    ids: dict[str, uuid.UUID],
    *,
    statements: list[str],
    relied_ids: list[str],
) -> dict[str, Any]:
    """Форма предложения ed36f4a0: temporal_fact без `as_of` и без `existing_claim_id`."""
    links = [
        {
            "evidence_index": 1 if statement in (RESTATED_OFFICIAL, NEW_RATE) else 0,
            "claim_index": index,
            "relation": "supports",
        }
        for index, statement in enumerate(statements)
    ]
    return {
        "content": {
            "summary": (
                "Ответ опирается на уже записанные утверждения контекст-пака: официальная оценка "
                "5,59% и наблюдаемая 14,5%."
            ),
            "claims": [
                {"statement": statement, "claim_type": "temporal_fact", "scope": {}} for statement in statements
            ],
            "evidence_links": links,
            "new_questions": [],
            "relied_claim_ids": relied_ids,
        }
    }


async def _run_compare_session(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    ids: dict[str, uuid.UUID],
    *,
    statements: list[str],
    relied_ids: list[str],
) -> tuple[Any, uuid.UUID]:
    question_id = await _seed_question(scratch_url, COMPARE_QUESTION)
    fake_llm.script(
        [
            _fetch("http://epsilon.example/survey"),
            _fetch("http://zeta.example/obzor"),
            _COMPLETE,
            _curator_answer(ids, statements=statements, relied_ids=relied_ids),
        ]
    )
    outcome = await _run_session(
        scratch_url,
        fake_llm,
        tmp_path / f"ws{question_id.hex[:6]}",
        tmp_path / f"art{question_id.hex[:6]}",
        question_id,
    )
    return outcome, question_id


async def _card(scratch_url: str, tmp_path: Path, question_id: uuid.UUID) -> dict[str, Any]:
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{question_id}/answer")
            assert response.status_code == 200
            return dict(response.json())
    finally:
        await app_engine.dispose()


async def _rejection_events(scratch_url: str, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'session_state_changed' "
        "AND (payload ? 'curator_rejected' OR payload ? 'curator_rejected_by_rules') ORDER BY sequence",
        {"s": str(session_id)},
    )
    return [dict(row["payload"]) for row in rows]


# ── главный сценарий: оба пересказа — перепроверки своих якорей ─────────────────

@pytest.mark.asyncio
async def test_two_dateless_restatements_become_reverifies_of_their_anchors(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    ids.pop("dup")  # в опорном списке только якоря: склейка разрешена с объявленной опорой (T7.83a)
    before = {name: await _assessment(scratch_url, claim_id) for name, claim_id in ids.items()}

    outcome, question_id = await _run_compare_session(
        scratch_url,
        fake_llm,
        tmp_path,
        ids,
        statements=[RESTATED_OFFICIAL, RESTATED_OBSERVED],
        relied_ids=[str(ids["official"]), str(ids["observed"])],
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"

    # (а) отказа нет: проверка даты исполнена после гейтов и обе операции — перепроверки
    assert await _rejection_events(scratch_url, session_id) == []

    # (б) ни одного нового утверждения: значение записано ровно один раз (ни official, ни dup
    # не получили второй claim рядом с собой)
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims ORDER BY statement")
    assert len(rows) == 3
    assert {row["statement"] for row in rows} == {CANDIDATE_OFFICIAL, CANDIDATE_OBSERVED, DUP_559}

    # (в) staged-операции стали перепроверками своих якорей
    ops = await _all(
        scratch_url,
        "SELECT payload->>'existing_claim_id' AS ref FROM session_staging WHERE session_id = :s "
        "AND op = 'claim' ORDER BY seq",
        {"s": str(session_id)},
    )
    assert [row["ref"] for row in ops] == [str(ids["official"]), str(ids["observed"])]

    # (г) хост сохранил якорные даты: датless-предложение их не стёрло (T7.73)
    reverifies = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified' "
        "ORDER BY sequence",
        {"s": str(session_id)},
    )
    assert len(reverifies) == 2
    resolved = {str(dict(row["payload"])["resolved"]) for row in reverifies}
    assert resolved == {str(ids["official"]), str(ids["observed"])}
    for row in reverifies:
        payload = dict(row["payload"])
        assert payload["anchor_kept"] is True
    stored = {
        str(dict(row["payload"])["resolved"]): str(dict(row["payload"])["stored_as_of"])
        for row in reverifies
    }
    assert stored[str(ids["official"])].startswith("2026-01-21")
    assert stored[str(ids["observed"])].startswith("2025-12-31")

    # (д) оценки не ниже прежних (union улик monotone, якорная дата и scope сохранены)
    for name, claim_id in ids.items():
        after = await _assessment(scratch_url, claim_id)
        assert _GRADE_RANK[after["grade"]] >= _GRADE_RANK[before[name]["grade"]], (name, before, after)
        assert float(after["confidence"]) >= float(before[name]["confidence"]), (name, before, after)
        assert str(after["as_of"])[:10] == str(before[name]["as_of"])[:10], name

    # (е) честная запись гейта в уже существующем событии
    payload_rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(session_id)},
    )
    assert len(payload_rows) == 1
    curator_payload = dict(payload_rows[0]["payload"])
    assert set(curator_payload) == {
        "claims",
        "evidence_links",
        "new_questions",
        "summary",
        "relied_claim_ids",
        "value_duplicates",
    }
    decisions = curator_payload["value_duplicates"]
    assert sorted((entry["claim_index"], entry["action"], entry["target"]) for entry in decisions) == [
        (0, "reverified", str(ids["official"])),
        (1, "reverified", str(ids["observed"])),
    ]

    # (ж) карточка: каждое значение ровно один раз, оба — «перепроверено этим вопросом»
    card = await _card(scratch_url, tmp_path, question_id)
    statements = [item["statement"] for item in card["claims"]]
    assert sorted(statements) == sorted([CANDIDATE_OFFICIAL, CANDIDATE_OBSERVED])
    assert sum(1 for statement in statements if "5,59" in statement) == 1
    assert sum(1 for statement in statements if "14,5" in statement) == 1
    assert {item["relation"] for item in card["claims"]} == {"reverified"}


# ── реальный контекст стенда: два 5,59%-кандидата в опорном списке → отказ всего ──

@pytest.mark.asyncio
async def test_two_relied_candidates_for_one_value_refuse_the_proposal_with_a_digest(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Замер поведения на реальной форме ed36f4a0: в контекст-паке ОБА 5,59%-claim'а
    (`9266248e…` и дубль `e189c80d…`), куратор объявил опорой оба. Гейт T7.83a видит двух
    строгих кандидатов и НЕ выбирает один (эвристики выбора нет) — операция остаётся новой,
    даты у неё нет → отказ всего предложения, как и раньше. Отличие от прежнего кода только в
    том, что журнал теперь показывает отклонённое содержимое."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    before = {name: await _assessment(scratch_url, claim_id) for name, claim_id in ids.items()}

    outcome, question_id = await _run_compare_session(
        scratch_url,
        fake_llm,
        tmp_path,
        ids,
        statements=[RESTATED_OFFICIAL, RESTATED_OBSERVED],
        relied_ids=[str(ids["official"]), str(ids["dup"]), str(ids["observed"])],
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 0

    events = await _rejection_events(scratch_url, session_id)
    assert len(events) == 1
    payload = events[0]
    # прежняя причина и прежний ключ —policy не изменилась
    assert payload["curator_rejected"] == ["claim[0]: temporal_fact requires as_of"]

    # неоднозначность названа: хост не выбирал один из двух кандидатов (эвристики выбора нет).
    # Вторая операция тем временем была склеена законно (один объявленный опора-кандидат того
    # же показателя) — решение гейта записано честно, но отказ всего предложения его отменяет:
    # staging нет ни у той, ни у другой.
    decisions = payload["value_duplicates"]
    assert sorted((entry["claim_index"], entry["action"]) for entry in decisions) == [
        (0, "kept"),
        (1, "reverified"),
    ]
    ambiguous = next(entry for entry in decisions if entry["claim_index"] == 0)
    assert ambiguous["target"] is None
    assert "ambiguous value duplicate" in ambiguous["reason"]
    merged = next(entry for entry in decisions if entry["claim_index"] == 1)
    assert merged["target"] == str(ids["observed"])

    # содержимое отклонённого предложения записано: что именно модель предлагала
    digest = payload["rejected_proposal"]
    assert [entry["claim_index"] for entry in digest["claims"]] == [0, 1]
    assert digest["claims"][0]["as_of"] is None
    assert digest["claims"][0]["existing_claim_id"] is None
    assert "5,59" in digest["claims"][0]["statement"]
    assert "14,5" in digest["claims"][1]["statement"]
    assert sorted(digest["relied_claim_ids"]) == sorted(
        [str(ids["official"]), str(ids["dup"]), str(ids["observed"])]
    )

    # ни staging, ни знания: fail-closed сохранён полностью
    staging = await _scalar(
        scratch_url, "SELECT count(*) FROM session_staging WHERE session_id = :s", {"s": str(session_id)}
    )
    assert staging is not None and staging[0] == 0
    reverifies = await _all(
        scratch_url,
        "SELECT id FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert reverifies == []
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 3
    for name, claim_id in ids.items():
        assert await _assessment(scratch_url, claim_id) == before[name]

    # что видит оператор: ответа нет, и это названо
    state = await _scalar(scratch_url, "SELECT state FROM questions WHERE id=:q", {"q": str(question_id)})
    assert state is not None and state[0] == "partially_answered"
    card = await _card(scratch_url, tmp_path, question_id)
    assert card["claims"] == []


# ── вариант relied: только 9266248e — дубль в пакете не создаёт ложной неоднозначности ──

@pytest.mark.asyncio
async def test_relying_on_one_of_two_same_value_claims_still_merges(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    before = await _assessment(scratch_url, ids["official"])
    dup_before = await _assessment(scratch_url, ids["dup"])

    outcome, _question_id = await _run_compare_session(
        scratch_url,
        fake_llm,
        tmp_path,
        ids,
        statements=[RESTATED_OFFICIAL],
        relied_ids=[str(ids["official"])],
    )
    session_id = outcome.session_id
    assert outcome.claims_proposed == 1

    # (а) склейка с объявленной опорой: ни отказа, ни нового claim'а
    assert await _rejection_events(scratch_url, session_id) == []
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 3

    # (б) якорная дата кандидата сохранена
    reverifies = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_reverified'",
        {"s": str(session_id)},
    )
    assert len(reverifies) == 1
    payload = dict(reverifies[0]["payload"])
    assert payload["resolved"] == str(ids["official"])
    assert payload["anchor_kept"] is True
    assert str(payload["stored_as_of"]).startswith("2026-01-21")

    # (в) оценка кандидата не ниже прежней, чужой дубль не тронут этой сессией
    after = await _assessment(scratch_url, ids["official"])
    assert _GRADE_RANK[after["grade"]] >= _GRADE_RANK[before["grade"]]
    assert float(after["confidence"]) >= float(before["confidence"])
    assessments = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claim_assessments WHERE claim_id = :c AND created_in_session = :s",
        {"c": str(ids["dup"]), "s": str(session_id)},
    )
    assert assessments is not None and assessments[0] == 0
    assert await _assessment(scratch_url, ids["dup"]) == dup_before


# ── новая датless temporal_fact без кандидата: отказ прежний + дайджест ──────────

@pytest.mark.asyncio
async def test_new_dateless_temporal_fact_without_a_candidate_is_still_refused(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    outcome, _question_id = await _run_compare_session(
        scratch_url,
        fake_llm,
        tmp_path,
        ids,
        statements=[NEW_RATE],
        relied_ids=[str(ids["official"])],
    )
    session_id = outcome.session_id
    assert outcome.claims_proposed == 0

    events = await _rejection_events(scratch_url, session_id)
    assert len(events) == 1
    payload = events[0]
    assert payload["curator_rejected"] == ["claim[0]: temporal_fact requires as_of"]
    # гейт дублей молчит, когда кандидатов нет: прежнего ключа в отказе не появилось
    assert "value_duplicates" not in payload

    digest = payload["rejected_proposal"]
    entry = digest["claims"][0]
    assert entry["as_of"] is None
    assert entry["existing_claim_id"] is None
    assert "21,5" in entry["statement"]
    staging = await _scalar(
        scratch_url, "SELECT count(*) FROM session_staging WHERE session_id = :s", {"s": str(session_id)}
    )
    assert staging is not None and staging[0] == 0


# ── датless-операция с as_of: поведение и payload не изменились вовсе ───────────

@pytest.mark.asyncio
async def test_temporal_fact_with_its_own_date_is_untouched_by_the_reordering(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    ids = await _seed_candidates(scratch_url, fake_llm, tmp_path)
    question_id = await _seed_question(scratch_url, COMPARE_QUESTION)
    answer = copy.deepcopy(
        _curator_answer(ids, statements=[NEW_RATE], relied_ids=[str(ids["official"])])
    )
    answer["content"]["claims"][0]["as_of"] = "2025-12-31T00:00:00"
    fake_llm.script(
        [
            _fetch("http://epsilon.example/survey"),
            _fetch("http://zeta.example/obzor"),
            _COMPLETE,
            answer,
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws-new", tmp_path / "art-new", question_id
    )
    session_id = outcome.session_id
    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 1

    assert await _rejection_events(scratch_url, session_id) == []
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 4
    payload_rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created' "
        "AND jsonb_typeof(payload -> 'claims') = 'array'",
        {"s": str(session_id)},
    )
    curator_payload = dict(payload_rows[0]["payload"])
    # ни дайджеста, ни решений гейта: ключи прежней формы байт в байт прежние
    assert set(curator_payload) == {
        "claims",
        "evidence_links",
        "new_questions",
        "summary",
        "relied_claim_ids",
    }
