"""Scenario (fake LLM, curated research): опора ответа на уже записанное знание
(T7.82(б), ADR-0032).

Воспроизводится форма дефекта стенда .92 (сессия f0d7e844, config-v18): вопрос про
годовую инфляцию по независимым оценкам; в контекст-паке уже лежит строка знания о
прежней цифре, ответ куратора на неё опирается — но карточка показывала только новые
claims, а связь «ответ использовал это знание» нигде не была видна.

После правки (кураторский prompt curator-v9 + хостовый разрешатель):

* куратор может принести факультативное поле `relied_claim_ids`;
* хост принимает ТОЛЬКО id, видимые в контекст-паке этой сессии: полный UUID или
  однозначный hex-префикс; дубли склеиваются; выдуманный id — честная отказная
  причина в payload уже существующего события `claim_created`, предложение в целом
  остаётся действительным (в отличие от того же гейта перепроверки);
* опора — НЕ перепроверка: нет события `claim_reverified`, нет новой оценки, строка
  claim и её head не меняются, staging-операций по ней ноль;
* карточка вопроса показывает опертое утверждение ОТДЕЛЬНОЙ связью «использовано из
  знаний» под записанными выводами (подпись — только из словаря labels).

Никаких внешних запросов: HTTP-слой research proxy подменён, страницы чужого теста.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from apps.web import labels as ui_labels
from apps.web.reliability import LEVEL_VERIFIED
from packages.domain.schemas.staging import CuratorProposal
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_reverify_temporal_scenario import (
    _COMPLETE,
    ANCHOR_QUESTION,
    STATEMENT,
    _curated_payload,
    _fetch,
    _run_session,
)
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = [pytest.mark.scenario]


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же подменный HTTP-слой, что у тестов research proxy (+ страницы этого
    теста на трёх разных registrable domains)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in {
        "http://alpha.example/cpi": (
            "Annual consumer price growth for the year was reported at 5.59 percentage points."
        ),
        "http://beta.example/cpi": (
            "The yearly inflation rate came out at 5.59 percent, independent calculation shows."
        ),
        "http://gamma.example/news": (
            "A second look at the yearly figure: independent estimate puts it near 5.6 percent."
        ),
    }.items():
        monkeypatch.setitem(PAGES, url, body)

INDEP_STATEMENT = "Независимая оценка годовой инфляции близка к 5,6 процентного пункта по стороннему расчёту"

#: вопрос второй сессии называет statement якоря лексикой — иначе строка знания не
#: попадает в контекстный пакет (тот же урок T7.73), и куратор не может на опереться.
RELIED_QUESTION = (
    f"Сверь независимую оценку по http://gamma.example/news: {STATEMENT}? "
    f"Если сторонний расчёт подтверждает её порядок, зафиксируй отдельный вывод: {INDEP_STATEMENT}."
)

#: правдоподобно выдуманный куратором id (в пакете его нет) и мусор короче префикса
FABRICATED_UUID = str(uuid.uuid4())


def _curator_output(anchor_id: uuid.UUID) -> dict[str, Any]:
    """Ответ куратора curator-v9: новый claim по свежей странице + опора на якорь
    (полным id и тем же id hex-префиксом — дубл обязан склеиться) плюс выдуманный id."""
    return {
        "content": {
            "summary": "Сторонняя оценка подтверждает порядок прежней цифры",
            "claims": [
                {
                    "statement": INDEP_STATEMENT,
                    "claim_type": "external_fact",
                    "scope": {"объект": "независимая оценка годовой инфляции"},
                }
            ],
            "evidence_links": [
                {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
            ],
            "new_questions": [],
            "relied_claim_ids": [str(anchor_id), str(anchor_id)[:8], FABRICATED_UUID, "deadbeef"],
        }
    }


@pytest.mark.asyncio
async def test_relied_claims_land_in_existing_audit_event_and_on_the_card(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _curated_payload())
    assert activation.state == "active"

    # ── сессия 1: якорь уже записан (тот же путь, что у теста перепроверки) ───
    anchor_qid = await _seed_question(scratch_url, ANCHOR_QUESTION)
    fake_llm.script(
        [
            _fetch("http://alpha.example/cpi"),
            _fetch("http://beta.example/cpi"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Годовая инфляция",
                    "claims": [
                        {
                            "statement": STATEMENT,
                            "claim_type": "temporal_fact",
                            "as_of": "2026-01-21T00:00:00+00:00",
                            "scope": {"объект": "годовая инфляция"},
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    anchor = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws1", tmp_path / "artifacts", anchor_qid
    )
    assert anchor.final_state.value == "succeeded"

    claim_row = await _scalar(scratch_url, "SELECT id FROM claims")
    assert claim_row is not None
    anchor_id = claim_row[0] if isinstance(claim_row[0], uuid.UUID) else uuid.UUID(str(claim_row[0]))
    assessments_before = await _scalar(
        scratch_url, "SELECT count(*) FROM claim_assessments WHERE claim_id = :c", {"c": str(anchor_id)}
    )
    assert assessments_before is not None and assessments_before[0] == 1

    # ── сессия 2: ответ опирается на якорь, но НЕ перепроверяет его ───────────
    relied_qid = await _seed_question(scratch_url, RELIED_QUESTION)
    fake_llm.script(
        [
            _fetch("http://gamma.example/news"),
            _COMPLETE,
            _curator_output(anchor_id),
        ]
    )
    relied = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws2", tmp_path / "artifacts", relied_qid
    )
    assert relied.final_state.value == "succeeded"

    # (а) новый claim записан, якорь остался единственным прежним: итого два
    n_claims = await _scalar(scratch_url, "SELECT count(*) FROM claims")
    assert n_claims is not None and n_claims[0] == 2

    # (б) опора записана в payload УЖЕ СУЩЕСТВУЮЩЕГО события claim_created:
    #     полный id разрешён, префикс того же claim склеен в дубль, выдуманный id и
    #     мусор — честные отказы; событие `claim_reverified` отсутствует вовсе.
    audit = await _scalar(
        scratch_url,
        "SELECT payload->'relied_claim_ids', payload->'relied_claims_rejected' FROM audit_events "
        "WHERE type = 'claim_created' AND session_id = :s",
        {"s": str(relied.session_id)},
    )
    assert audit is not None, "CLAIM_CREATED event missing for the relied session"
    accepted = list(audit[0] or [])
    rejected = list(audit[1] or [])
    assert accepted == [str(anchor_id)], f"relied ids not resolved host-side: {accepted}"
    assert len(rejected) == 2, rejected
    assert any(FABRICATED_UUID in reason for reason in rejected), rejected
    assert any("deadbeef" in reason for reason in rejected), rejected

    reverified = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = 'claim_reverified' AND session_id = :s",
        {"s": str(relied.session_id)},
    )
    assert reverified is not None and reverified[0] == 0, "reliance must not produce a reverify"

    # (в) оценка якоря не тронута: ни новой строки claim_assessments, head тот же
    assessments_after = await _scalar(
        scratch_url, "SELECT count(*) FROM claim_assessments WHERE claim_id = :c", {"c": str(anchor_id)}
    )
    assert assessments_after is not None and assessments_after[0] == 1
    head_still = await _scalar(
        scratch_url,
        "SELECT a.created_in_session::text FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id WHERE h.claim_id = :c",
        {"c": str(anchor_id)},
    )
    assert head_still is not None and head_still[0] == str(anchor.session_id)

    # (г) staging не разводился: опора — не операция (операции сессии 2 — только
    #     новый claim и его evidence-связь)
    ops = await _all(
        scratch_url,
        "SELECT op FROM session_staging WHERE session_id = :s ORDER BY op",
        {"s": str(relied.session_id)},
    )
    assert sorted(row["op"] for row in ops) == ["claim", "evidence"]

    # ── карточка: «использовано из знаний» отдельной связью под записанным ────
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        card_response = await client.get(f"/api/v1/questions/{relied_qid}/answer")
        assert card_response.status_code == 200
        card = card_response.json()
    await app_engine.dispose()

    assert [item["relation"] for item in card["claims"]] == ["created"]
    relied_rows = card["relied_claims"]
    assert [item["id"] for item in relied_rows] == [str(anchor_id)]
    row = relied_rows[0]
    assert row["relation"] == "relied"
    assert row["relation_label"] == ui_labels.describe("claim_relation", "relied")["label"]
    assert row["relation_label"] == "использовано из знаний"
    assert row["statement"] == STATEMENT
    # бейдж — перевод уже вычисленной оценки, без пересчёта: якорь остаётся E3 supported
    assert row["grade_label"] == ui_labels.describe("evidence_grade", "E3")["label"]
    assert row["reliability"]["level"] == LEVEL_VERIFIED
    # карточка не утверждает проверку там, где её не было в этой сессии: строка
    # подтверждений опёртого утверждения описывает его прежние доказательства (их ≥1)
    assert len(row["verification"]) >= 1


@pytest.mark.asyncio
async def test_curator_proposal_without_reliance_field_is_unchanged(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """curator-v8-форма предложения (без нового поля) работает по-прежнему, а payload
    события — байт в байт прежний: ключи relied_* не появляются."""
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _curated_payload())
    assert activation.state == "active"

    qid = await _seed_question(scratch_url, ANCHOR_QUESTION)
    fake_llm.script(
        [
            _fetch("http://alpha.example/cpi"),
            _fetch("http://beta.example/cpi"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Годовая инфляция",
                    "claims": [
                        {
                            "statement": STATEMENT,
                            "claim_type": "temporal_fact",
                            "as_of": "2026-01-21T00:00:00+00:00",
                            "scope": {},
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                        {"evidence_index": 1, "claim_index": 0, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    run = await _run_session(scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", qid)
    assert run.final_state.value == "succeeded"

    audit = await _scalar(
        scratch_url,
        "SELECT payload FROM audit_events WHERE type = 'claim_created' AND session_id = :s",
        {"s": str(run.session_id)},
    )
    assert audit is not None
    payload = audit[0]
    assert "relied_claim_ids" not in payload
    assert "relied_claims_rejected" not in payload
    assert set(payload) == {"claims", "evidence_links", "new_questions", "summary"}


@pytest.mark.unit
def test_curator_proposal_schema_accepts_optional_reliance_field() -> None:
    """Схема предложения: поле факультативное, unknown-поля по-прежнему запрещены."""
    plain = CuratorProposal.model_validate({"summary": "s"})
    assert plain.relied_claim_ids == []
    with_ids = CuratorProposal.model_validate(
        {"summary": "s", "relied_claim_ids": ["118b76b3-fe02-44d0-b1de-6d036c48646b"]}
    )
    assert with_ids.relied_claim_ids == ["118b76b3-fe02-44d0-b1de-6d036c48646b"]
    with pytest.raises(ValueError):
        CuratorProposal.model_validate({"summary": "s", "relied_ids": ["x"]})
