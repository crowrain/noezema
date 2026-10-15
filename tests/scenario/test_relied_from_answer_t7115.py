"""Scenario (fake LLM, curated research): опора из ИТОГА исследования (случай .92
96a0c535, T7.115, ADR-0032 дополнение).

Воспроизводится стендовый случай: финальный ответ исследователя ссылается на уже
записанное знание в форме `c:<id>`, куратор при этом предложил 0 claims и не назвал
опору — карточка показала ноль утверждений и ни одной связи «использовано из знаний»,
хотя ответ лежит в знании: запрос куратора не содержит финального ответа исследователя,
и честная ссылка терялась.

После правки хост:
* сохраняет rationale шага `complete` целиком в памяти сессии (не аудит-обрезок);
* на границе куратора извлекает из НЕОСРЕДСТВЕННО финальной rationale формы
  `c:<полный uuid>` и `c:<hex-префикс не короче 8>` и разрешает их тем же
  разрешателем `resolve_claim_reference` по id контекст-пака `_pack_claim_ids`;
* id, который является операцией этой сессии (перепроверка/склейка), не дублируется;
* объединяет с опорой куратора без дублей: опора куратора в приоритете, общий потолок
  `RELIED_CLAIMS_LIMIT`; отказы — в `relied_claims_rejected` с префиксом `answer[k]`;
* аддитивный ключ `relied_from_explorer` называет, какие id добавил итог; ключи
  появляются только непустыми — сессии без ссылок остаются байт в байт прежними;
* промежуточные шаги в расчёт не берутся.

Ни одного внешнего запроса: HTTP-слой research proxy подменён.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

import apps.orchestrator.orchestrator as orch
from apps.web import labels as ui_labels
from tests.conftest import FakeLLM
from tests.scenario.test_curator_correction_round_t7106 import (
    _claim_created_payloads,
    _curator_answer,
    _curator_calls,
    _db_statements,
    _links,
    _rejection_events,
)
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

pytestmark = [pytest.mark.scenario]

# ── страницы и assertions якорной сессии (вымышленные акторы) ───────────────────

_ANCHOR_URLS = [
    "http://veerstat.example/inf",
    "http://gelios.example/inf",
    "http://veerfund.example/inf",
]

_PAGES: dict[str, str] = {
    _ANCHOR_URLS[0]: (
        "Bank Weer independent estimate: annual inflation for 2029 is 6.5 percentage points."
    ),
    _ANCHOR_URLS[1]: (
        "Helios model estimate: annual inflation for 2029 is 7.8 percentage points."
    ),
    _ANCHOR_URLS[2]: (
        "Weer Fund estimate: annual inflation for 2029 is 6.7 percentage points."
    ),
    "http://method.example/notes": (
        "Methodological note: independent estimates of annual inflation are compared on the "
        "recorded figures 6.5, 7.8 and 6.7."
    ),
    "http://refresh.example/inf": (
        "Refreshed Bank Weer estimate: annual inflation for 2029 stays at 6.5 percentage points."
    ),
}

S1 = "Оценка годовой инфляции банка «Веер» на 2029 год составила 6,5 процентного пункта"
S2 = "Оценка годовой инфляции сервиса «Гелиос» на 2029 год составила 7,8 процентного пункта"
S3 = "Оценка годовой инфляции фонда «Веер-два» на 2029 год составила 6,7 процентного пункта"

#: Вопрос называет формулировки лексикой — иначе строки знания не попадают в
#: контекстный пакет (тот же урок T7.73), и опора не может быть разрешена.
ANCHOR_QUESTION = (
    "Собери независимые оценки годовой инфляции на 2029 год: банк «Веер», сервис «Гелиос» "
    "и фонд «Веер-два». Источники: " + " ".join(_ANCHOR_URLS)
)

RELIED_QUESTION = (
    "Сопоставь записанные независимые оценки годовой инфляции на 2029 год — банк «Веер», "
    "сервис «Гелиос» и фонд «Веер-два» — и зафиксируй вывод с опорой на уже записанное "
    "знание. Источники: http://method.example/notes"
)

REVERIFY_QUESTION = (
    "Перепроверь по свежему источнику оценку годовой инфляции банка «Веер» на 2029 год "
    "и сопоставь её с оценками сервиса «Гелиос» и фонда «Веер-два». "
    "Источники: http://refresh.example/inf"
)

#: выдуманные id (их нет в пакете)
FABRICATED_UUID = str(uuid.uuid4())

# фиксированная пара id с общим первым сегментом — неоднозначный префикс (т3)
CID_A = uuid.UUID("7f4e2b18-1111-4c0a-9b3d-000000000001")
CID_B = uuid.UUID("7f4e2b18-2222-4c0a-9b3d-000000000002")
SEED_S1 = "Зафиксированная оценка выручки торговой сети «Лаванда» за 2028 год составила 12,4 миллиарда рублей"
SEED_S2 = "Зафиксированная оценка выручки торговой сети «Орхидея» за 2028 год составила 9,1 миллиарда рублей"

SEED_QUESTION = (
    "Сопоставь зафиксированные оценки выручки торговых сетей «Лаванда» и «Орхидея» за "
    "2028 год. Источники: http://method.example/notes"
)


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же подменный HTTP-слой, что у тестов research proxy."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 20  # с запасом на несколько сессий теста
    return payload


def _complete(text: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": text,
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


def _anchor_curator() -> dict[str, Any]:
    return _curator_answer(
        [
            {"statement": S1, "claim_type": "external_fact", "scope": {"объект": "годовая инфляция Веер"}},
            {"statement": S2, "claim_type": "external_fact", "scope": {"объект": "годовая инфляция Гелиос"}},
            {"statement": S3, "claim_type": "external_fact", "scope": {"объект": "годовая инфляция Веер-два"}},
        ],
        _links((0, 0), (1, 1), (2, 2)),
        summary="Три независимые оценки годовой инфляции на 2029 год",
    )


async def _seed_anchors(
    scratch_url: str, fake_llm: FakeLLM, tmp_path: Path
) -> dict[str, uuid.UUID]:
    """Одна полная сессия записывает три утверждения знания (те же head-оценки,
    что у обычных сессий) — возвращает statement -> id."""
    qid = await _seed_question(scratch_url, ANCHOR_QUESTION)
    fake_llm.script([_fetch(u) for u in _ANCHOR_URLS] + [_COMPLETE, _anchor_curator()])
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "wsA", tmp_path / "artA", qid
    )
    assert outcome.final_state.value == "succeeded"
    rows = await _all(scratch_url, "SELECT id, statement FROM claims")
    by_statement: dict[str, uuid.UUID] = {}
    for row in rows:
        cid = row["id"] if isinstance(row["id"], uuid.UUID) else uuid.UUID(str(row["id"]))
        by_statement[str(row["statement"])] = cid
    assert set(by_statement) == {S1, S2, S3}, by_statement
    return by_statement


async def _run_relied(
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_text: str,
    complete_text: str,
    curator_answers: list[dict[str, Any]],
    fetch_url: str = "http://method.example/notes",
) -> Any:
    qid = await _seed_question(scratch_url, question_text)
    fake_llm.script([_fetch(fetch_url), _complete(complete_text), *curator_answers])
    outcome = await _run_session(
        scratch_url,
        fake_llm,
        tmp_path / f"ws{qid.hex[:6]}",
        tmp_path / f"art{qid.hex[:6]}",
        qid,
    )
    assert outcome.final_state.value == "succeeded"
    return outcome


async def _curator_payload(scratch_url: str, session_id: uuid.UUID) -> dict[str, Any]:
    payloads = await _claim_created_payloads(scratch_url, session_id)
    assert len(payloads) == 1, "CLAIM_CREATED обязан писаться и при 0 claims куратора"
    return payloads[0]


async def _seed_claim_pair(scratch_url: str) -> None:
    """Два утверждения знания с фиксированными id (общий первые 8 hex) и действующей
    головой на ЭФФЕКТИВНОМ снапшоте — та же форма фикстуры test_web_knowledge."""
    engine = create_async_engine(scratch_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db, db.begin():
            for cid, statement in ((CID_A, SEED_S1), (CID_B, SEED_S2)):
                await db.execute(
                    text(
                        "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                        "VALUES (:id, :s, 'external_fact', 'fresh')"
                    ),
                    {"id": cid, "s": statement},
                )
                aid = uuid.uuid4()
                await db.execute(
                    text(
                        "INSERT INTO claim_assessments (id, claim_id, effective_grade, "
                        "epistemic_status, rules_version, rules_hash, evidence_set_hash, "
                        "assessed_scope, confidence, valid) VALUES (:a, :c, 'E2', "
                        "'supported', 'rules-v1', 'h', 'ev', '{}', 0.9, true)"
                    ),
                    {"a": aid, "c": cid},
                )
                await db.execute(
                    text(
                        "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, "
                        "assessment_state, current_assessment_id, epistemic_status, prepared_by) "
                        "VALUES (:c, (SELECT active_config_snapshot_id FROM runtime_config_heads "
                        "WHERE scope = 'global'), 'current', :a, 'supported', 'rules_activation')"
                    ),
                    {"c": cid, "a": aid},
                )
    finally:
        await engine.dispose()


# ── т1: реплей случая 96a0c535 — 0 claims куратора, три id из финального ответа ──


@pytest.mark.asyncio
async def test_answer_reliance_resolves_three_pack_ids_and_shows_on_the_card(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    assessments_before = await _all(
        scratch_url, "SELECT claim_id, count(*) AS n FROM claim_assessments GROUP BY claim_id"
    )
    assert all(row["n"] == 1 for row in assessments_before)

    id1, id2, id3 = anchors[S1], anchors[S2], anchors[S3]
    complete_text = (
        f"Все три оценки уже записаны в знании: c:{id1} (банк «Веер»), c:{id2} (сервис "
        f"«Гелиос») и c:{str(id3)[:8]} (фонд «Веер-два»); перепроверка не требовалась, "
        "сопоставление готово."
    )
    outcome = await _run_relied(
        scratch_url,
        fake_llm,
        tmp_path,
        RELIED_QUESTION,
        complete_text,
        [_curator_answer([], [], summary="Сопоставление трёх записанных оценок")],
    )

    payload = await _curator_payload(scratch_url, outcome.session_id)
    # порядок — порядок ссылок в финальном ответе; префикс разрешён в полный id
    assert payload["relied_claim_ids"] == [str(id1), str(id2), str(id3)]
    assert payload["relied_from_explorer"] == [str(id1), str(id2), str(id3)]
    assert "relied_claims_rejected" not in payload

    # опора — не операция: ни перепроверок, ни новых утверждений, ни staging
    reverified = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = 'claim_reverified' AND session_id = :s",
        {"s": str(outcome.session_id)},
    )
    assert reverified is not None and reverified[0] == 0
    assert await _db_statements(scratch_url) == sorted([S1, S2, S3])
    ops = await _all(
        scratch_url, "SELECT op FROM session_staging WHERE session_id = :s", {"s": str(outcome.session_id)}
    )
    assert ops == []

    # карточка вопроса показывает все три строки связи «использовано из знаний»
    relied_qid = await _scalar(
        scratch_url,
        "SELECT question_id FROM sessions WHERE id = :s",
        {"s": str(outcome.session_id)},
    )
    assert relied_qid is not None
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        card_response = await client.get(f"/api/v1/questions/{relied_qid[0]}/answer")
        assert card_response.status_code == 200
        card = card_response.json()
    await app_engine.dispose()

    relied_rows = card["relied_claims"]
    assert {row["id"] for row in relied_rows} == {str(id1), str(id2), str(id3)}
    assert {row["statement"] for row in relied_rows} == {S1, S2, S3}
    assert all(row["relation"] == "relied" for row in relied_rows)
    label = ui_labels.describe("claim_relation", "relied")["label"]
    assert all(row["relation_label"] == label for row in relied_rows)


# ── т2: id вне контекст-пака — честный отказ в relied_claims_rejected ────────────


@pytest.mark.asyncio
async def test_answer_reference_outside_the_pack_is_honestly_rejected(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    id1, id2 = anchors[S1], anchors[S2]
    complete_text = (
        f"Опора на записанное знание: c:{id1} и c:{id2}. Кроме того в черновике остались "
        f"несуществующие ссылки c:{FABRICATED_UUID} и c:deadbeef."
    )
    outcome = await _run_relied(
        scratch_url,
        fake_llm,
        tmp_path,
        RELIED_QUESTION,
        complete_text,
        [_curator_answer([], [], summary="Сопоставление двух записанных оценок")],
    )

    payload = await _curator_payload(scratch_url, outcome.session_id)
    assert payload["relied_claim_ids"] == [str(id1), str(id2)]
    assert payload["relied_from_explorer"] == [str(id1), str(id2)]
    rejected = payload["relied_claims_rejected"]
    assert any(FABRICATED_UUID in reason and reason.startswith("answer[") for reason in rejected)
    assert any("deadbeef" in reason and reason.startswith("answer[") for reason in rejected)


# ── т3: неоднозначный hex-префикс — отказ, а не догадка хоста ────────────────────


@pytest.mark.asyncio
async def test_ambiguous_prefix_in_the_answer_is_rejected(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"
    await _seed_claim_pair(scratch_url)

    outcome = await _run_relied(
        scratch_url,
        fake_llm,
        tmp_path,
        SEED_QUESTION,
        "Обе записи видны в контексте: c:7f4e2b18 — но этот префикс не различает их.",
        [_curator_answer([], [], summary="Сопоставление двух записей выручки")],
    )

    payload = await _curator_payload(scratch_url, outcome.session_id)
    assert "relied_claim_ids" not in payload  # догадки нет: неразрешённый id не становится строкой
    rejected = payload["relied_claims_rejected"]
    assert any("ambiguous" in reason and "7f4e2b18" in reason for reason in rejected)
    assert await _db_statements(scratch_url) == sorted([SEED_S1, SEED_S2])


# ── т4: куратор сам назвал часть опоры — объединение без дублей, приоритет куратора


@pytest.mark.asyncio
async def test_curator_reliance_and_answer_reliance_merge_without_duplicates(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    monkeypatch.setattr(orch, "RELIED_CLAIMS_LIMIT", 2)

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    id1, id2, id3 = anchors[S1], anchors[S2], anchors[S3]
    curator_answer = _curator_answer([], [], summary="Сопоставление оценок")
    curator_answer["content"]["relied_claim_ids"] = [str(id1)[:8], FABRICATED_UUID]

    # ответ называет id2 и id3 РАНЬШЕ id1 — приоритет всё равно за опорой куратора
    complete_text = (
        f"Сопоставление: c:{id2} и c:{id3}; та же опора, что назвал куратор — c:{id1}."
    )
    outcome = await _run_relied(
        scratch_url, fake_llm, tmp_path, RELIED_QUESTION, complete_text, [curator_answer]
    )

    payload = await _curator_payload(scratch_url, outcome.session_id)
    # лимит 2: опора куратора (id1) + первый id из ответа (id2); id3 за лимитом — отказ
    assert payload["relied_claim_ids"] == [str(id1), str(id2)]
    assert payload["relied_from_explorer"] == [str(id2)]
    rejected = payload["relied_claims_rejected"]
    assert any(reason.startswith("rely[") and FABRICATED_UUID in reason for reason in rejected)
    assert any(
        reason.startswith("answer[") and "more than 2 relied ids" in reason for reason in rejected
    )


# ── т5: id операции этой сессии (перепроверка) не дублируется в опоре ────────────


@pytest.mark.asyncio
async def test_operation_of_the_session_is_not_repeated_as_reliance(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    id1, id2, id3 = anchors[S1], anchors[S2], anchors[S3]
    curator_answer = _curator_answer(
        [{"statement": S1, "claim_type": "external_fact", "scope": {}, "existing_claim_id": str(id1)}],
        _links((0, 0)),
        summary="Перепроверка оценки банка «Веер»",
    )
    complete_text = (
        f"Якорь перепроверен и остаётся действующим: c:{id1}. В сопоставление вошли также "
        f"записи знания c:{str(id2)[:8]} и c:{id3}."
    )
    outcome = await _run_relied(
        scratch_url,
        fake_llm,
        tmp_path,
        REVERIFY_QUESTION,
        complete_text,
        [curator_answer],
        fetch_url="http://refresh.example/inf",
    )

    reverified = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = 'claim_reverified' AND session_id = :s",
        {"s": str(outcome.session_id)},
    )
    assert reverified is not None and reverified[0] == 1, "перепроверка сессии обязана остаться операцией"

    payload = await _curator_payload(scratch_url, outcome.session_id)
    # id1 — операция этой сессия: в опору он не попадает повторно
    assert payload["relied_claim_ids"] == [str(id2), str(id3)]
    assert payload["relied_from_explorer"] == [str(id2), str(id3)]
    assert any(str(id1) in reason and reason.startswith("answer[") for reason in payload["relied_claims_rejected"])


# ── т6: ссылка только в промежуточном шаге — не берётся (байтовая совместимость) ──


@pytest.mark.asyncio
async def test_intermediate_step_reference_does_not_become_reliance(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    id1 = anchors[S1]

    qid = await _seed_question(scratch_url, RELIED_QUESTION)
    step_fetch = {
        "content": {
            "public_rationale": f"В знании уже есть c:{id1}; уточню методическую страницу.",
            "expected_information": "Методическая заметка",
            "decision": {
                "kind": "tool",
                "tool": "research.fetch",
                "arguments": {"url": "http://method.example/notes"},
            },
        }
    }
    fake_llm.script(
        [
            step_fetch,
            _COMPLETE,
            _curator_answer([], [], summary="Сопоставление без новых ссылок"),
        ]
    )
    outcome = await _run_session(
        scratch_url, fake_llm, tmp_path / "wsI", tmp_path / "artI", qid
    )
    assert outcome.final_state.value == "succeeded"

    payload = await _curator_payload(scratch_url, outcome.session_id)
    # финальный ответ ссылок не содержит: payload байт в байт прежний, ключей опоры нет
    assert set(payload) == {"claims", "evidence_links", "new_questions", "summary"}


# ── т7: раунд исправления куратора — опора из итога сохраняется ──────────────────


@pytest.mark.asyncio
async def test_answer_reliance_survives_the_correction_round(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    anchors = await _seed_anchors(scratch_url, fake_llm, tmp_path)
    id1, id2, id3 = anchors[S1], anchors[S2], anchors[S3]

    bad = _curator_answer(
        [
            {
                "statement": "Совокупная оценка годовой инфляции на 2029 год составила 7,0 процентного пункта",
                "claim_type": "temporal_fact",  # temporal_fact без as_of — отказ целиком
            }
        ],
        _links((0, 0)),
        summary="Черновик вывода",
    )
    fixed = _curator_answer([], [], summary="Сопоставление трёх записанных оценок инфляции")

    complete_text = (
        f"Готово: три оценки уже в знании — c:{id1}, c:{id2} и c:{id3}; своей датой "
        "перепроверка не потребовалась."
    )
    calls_before = len(_curator_calls(fake_llm))  # журнал fake-сервера общий для всех сессий теста
    outcome = await _run_relied(
        scratch_url, fake_llm, tmp_path, RELIED_QUESTION, complete_text, [bad, fixed]
    )

    assert len(_curator_calls(fake_llm)) - calls_before == 2  # ровно один раунд; третьего вызова нет
    assert fake_llm.state()["queue_left"] == 0
    fixations = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'session_state_changed' "
        "AND payload ? 'curator_correction_requested' ORDER BY sequence",
        {"s": str(outcome.session_id)},
    )
    assert len(fixations) == 1
    assert await _rejection_events(scratch_url, outcome.session_id) == []

    payload = await _curator_payload(scratch_url, outcome.session_id)
    assert "correction_round" in payload
    assert payload["relied_claim_ids"] == [str(id1), str(id2), str(id3)]
    assert payload["relied_from_explorer"] == [str(id1), str(id2), str(id3)]
