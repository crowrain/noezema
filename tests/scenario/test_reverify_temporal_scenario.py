"""Scenario (fake LLM, curated research): кейс 1 → кейс 2 сквозь полную сессию
(T7.73, уточнение ADR-0018).

Что здесь воспроизводится дословно — форма подставки .92:

* сессия 1: относительный вопрос («на текущую дату») + два независимых
  источника → `temporal_fact` доходит до E3 supported;
* сессия 2: перепроверка по хостовому id, вопросом **без даты**, с третьим
  источником и без `as_of` в предложении (ровно то, что велит правило
  «Перепроверка»).

Прежний код на этой ветке безусловно пересчитывал опорную дату из вопроса
перепроверки: `(None, none)` → rules engine честно докладывал `as_of_missing`,
объявленные источники сужались до одного хоста (`scope_not_covered` следом), и
вывод падал до E1 hypothesis. То есть честная перепроверка пониже оценки, чем
то, что она проверила.

Тест красный на прежнем коде (проверено временным откатом, STATUS T7.73): там
там E1 с `as_of_missing` и ни одной сохранённой даты.

Пункты (з)–(и) — витрина перепроверки (T7.74): карточка вопроса перепроверки обязана
показать действующее утверждение как свой ответ («перепроверено этим вопросом») и то же
должно видеть «Мои вопросы». Их краснота на коде до T7.74 проверена отдельным временным
откатом (STATUS T7.74).

Никаких внешних запросов: HTTP-слой research proxy подменён (`FakeFetchClient`),
страницы добавляет этот тест.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyService
from apps.web import labels as ui_labels
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_research_evidence import PAGES, FakeFetchClient
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question
from tests.scenario.test_web_answer_api import _client, _make

pytestmark = [pytest.mark.scenario]

STATEMENT = "Годовая инфляция по итогам года составила 5,59 процентного пункта"

ANCHOR_QUESTION = (
    f"{STATEMENT}? Уточни на текущую дату по источникам "
    "http://alpha.example/cpi и http://beta.example/cpi"
)
#: Перепроверка: своей даты у вопроса нет — именно эта форма и стирала якорь.
#: Утверждение называется прямо: иначе оно не попадает в контекстный пакет, и
#: оркестратор отклоняет перепроверку fail-closed («reverify reference … is not
#: visible to the session») ещё до фиксации — ни до дефекта, ни до исправления.
REVERIFY_QUESTION = (
    f"Перепроверь по независимому источнику http://gamma.example/news: {STATEMENT}?"
)

_PAGES: dict[str, str] = {
    "http://alpha.example/cpi": (
        "Annual consumer price growth for the year was reported at 5.59 percentage points."
    ),
    "http://beta.example/cpi": (
        "The yearly inflation rate came out at 5.59 percent, independent calculation shows."
    ),
    "http://gamma.example/news": (
        "A second look at the yearly figure: independent estimate puts it near 5.6 percent."
    ),
}


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же подменный HTTP-слой, что у тестов research proxy (+ страницы этого
    теста на трёх разных registrable domains)."""
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _fetch(url: str) -> dict[str, Any]:
    """Один scripted-ответ модели: вызов `research.fetch` (форма fake-сервера)."""
    return {
        "content": {
            "public_rationale": "Источник из вопроса",
            "expected_information": "Текст источника",
            "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": url}},
        }
    }


_COMPLETE = {
    "content": {
        "public_rationale": "Данные собраны",
        "decision": {"kind": "complete", "reason": "goal_reached"},
    }
}


def _curated_payload() -> dict[str, Any]:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = "http://127.0.0.1:8888"  # не используется: fetch подменён
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    return payload


async def _run_session(
    scratch_url: str,
    fake_llm: FakeLLM,
    workspace: Path,
    artifacts: Path,
    question_id: uuid.UUID,
) -> Any:
    """Одна полная сессия на реальном оркестраторе (свой engine на сессию)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
        research_service=ResearchProxyService(factory, FilesystemArtifactStore(artifacts)),
    )
    try:
        return await orchestrator.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_dateless_reverify_keeps_the_E3_of_a_temporal_fact(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    """Перепроверка без собственной даты не имеет права понижать вывод.

    Ожидание после исправления: якорная дата сохранена, head current/supported/E3,
    доказательственная база — объединение (две старые страницы + новая), а лента
    называет, что дата якоря осталась прежней.
    """
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _curated_payload())
    assert activation.state == "active"

    # ── сессия 1: якорь (E3 supported) ────────────────────────────────────────
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
                            # модельная дата — только аудит: дату решает хост
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
    anchor = await _run_session(scratch_url, fake_llm, tmp_path / "ws1", tmp_path / "artifacts", anchor_qid)
    assert anchor.final_state.value == "succeeded"

    baseline = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade, c.as_of::date = "
        "(SELECT created_at::date FROM sessions WHERE id = :s) "
        "FROM claims c JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id",
        {"s": str(anchor.session_id)},
    )
    assert baseline is not None, "the anchor claim has no head assessment"
    assert baseline[0] == "current" and baseline[1] == "supported"
    assert baseline[2] == "E3", f"the E3 baseline broke: {baseline[2]}"
    assert baseline[3] is True, "the anchor's reference date is not the session's day"

    claim_row = await _scalar(scratch_url, "SELECT id, as_of FROM claims")
    assert claim_row is not None
    claim_id = claim_row[0] if isinstance(claim_row[0], uuid.UUID) else uuid.UUID(str(claim_row[0]))
    anchor_as_of = claim_row[1]

    # ── сессия 2: перепроверка вопросом без даты ──────────────────────────────
    reverify_qid = await _seed_question(scratch_url, REVERIFY_QUESTION)
    fake_llm.script(
        [
            _fetch("http://gamma.example/news"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Перепроверка годовой инфляции",
                    "claims": [
                        {
                            "statement": STATEMENT,
                            # форма подставки: тип не назван, даты нет — хост assessит по типу якоря
                            "claim_type": "external_fact",
                            "scope": {},
                            "existing_claim_id": str(claim_id),
                        }
                    ],
                    "evidence_links": [
                        {"evidence_index": 0, "claim_index": 0, "relation": "supports"},
                    ],
                    "new_questions": [],
                }
            },
        ]
    )
    reverify = await _run_session(
        scratch_url, fake_llm, tmp_path / "ws2", tmp_path / "artifacts", reverify_qid
    )
    assert reverify.final_state.value == "succeeded"

    # (а) новое утверждение не заводилось: перепроверка прошла по существующему
    n_claims = await _scalar(scratch_url, "SELECT count(*) FROM claims")
    assert n_claims[0] == 1

    # (б) оценка НЕ понижена
    head = await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade, a.confidence "
        "FROM claim_assessment_heads h JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": str(claim_id)},
    )
    assert head is not None
    assert head[0] == "current", head
    assert head[1] == "supported", f"epistemic status lowered: {head[1]}"
    assert head[2] == "E3", f"grade lowered after a reverify that only added evidence: {head[2]}"

    # (в) опорная дата якоря целая
    stored = await _scalar(scratch_url, "SELECT as_of FROM claims WHERE id = :c", {"c": str(claim_id)})
    assert stored is not None and stored[0] is not None, "the reverify erased the anchor's date"
    assert stored[0].date() == anchor_as_of.date()

    # (г) доказательства объединены: три ряда с трёх разных registrable domains
    sources = await _all(
        scratch_url,
        "SELECT DISTINCT s.canonical_uri FROM evidence e JOIN sources s ON s.id = e.source_id "
        "WHERE e.claim_id = :c ORDER BY 1",
        {"c": str(claim_id)},
    )
    assert [row["canonical_uri"] for row in sources] == [
        "http://alpha.example/cpi",
        "http://beta.example/cpi",
        "http://gamma.example/news",
    ]

    # (д) scope текущей оценки: дата якоря + объединение объявленных источников
    assessed = await _all(
        scratch_url,
        "SELECT a.assessed_scope, a.created_at FROM claim_assessments a WHERE a.claim_id = :c "
        "ORDER BY a.created_at",
        {"c": str(claim_id)},
    )
    assert len(assessed) == 2
    current_scope = dict(assessed[-1]["assessed_scope"])
    assert current_scope["scope_schema"] == "host-scope-v1"
    assert current_scope["as_of"] == anchor_as_of.date().isoformat()
    assert current_scope["source_domains"] == ["alpha.example", "beta.example", "gamma.example"]

    # (е) причина текущей оценки — требования выполнены, а не отсутствующая дата
    reasons = await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events WHERE type = 'claim_assessed' "
        "AND session_id = :s",
        {"s": str(reverify.session_id)},
    )
    assert reasons is not None
    assert reasons[0] == ["requirements_met"], reasons[0]

    # (ж) лента перепроверки называет, что случилось с датой: оставлена якорная
    audit = await _scalar(
        scratch_url,
        "SELECT payload->>'anchor_kept', payload->>'stored_as_of', payload->>'assessed_as_of', "
        "payload->>'date_anchor' FROM audit_events WHERE type = 'claim_reverified' AND session_id = :s",
        {"s": str(reverify.session_id)},
    )
    assert audit is not None, "no claim_reverified audit event for the reverify session"
    assert audit[0] == "true"
    assert audit[1] == anchor_as_of.isoformat()
    assert audit[2] == anchor_as_of.isoformat()
    assert audit[3] == "relative"

    # ── (з) карточка вопроса перепроверки называет ответ (T7.74) ───────────────
    # На прежнем витринном коде здесь был пустой список утверждений и итог
    # «Ответ не записан», хотя сессия перепровера действовала (краснота проверена
    # временным откатом, STATUS T7.74).
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    async with _client(app) as client:
        card_response = await client.get(f"/api/v1/questions/{reverify_qid}/answer")
        assert card_response.status_code == 200
        card = card_response.json()
        queue_response = await client.get("/api/v1/questions")
        assert queue_response.status_code == 200
        rows = {row["id"]: row for row in queue_response.json()["questions"]}
    await app_engine.dispose()

    assert [item["relation"] for item in card["claims"]] == ["reverified"]
    assert card["claims"][0]["relation_label"] == ui_labels.describe("claim_relation", "reverified")["label"]
    assert card["result"]["kind"] == "reverified"
    assert card["result"]["label"] == ui_labels.describe("answer_result", "reverified")["label"]
    assert "Ответ не записан" not in card["result"]["label"]
    # оценка не изменилась (E3 supported → E3 supported): «было → стало» не выдумывается
    assert card["claims"][0]["reverify_history"] == []
    # шаги и итог рассказывают про сессию перепроверки, а не про сессию-якорь
    assert card["work"]["session_id"] == str(reverify.session_id)

    # (и) список «Мои вопросы» говорит то же, что карточка (тот же итог, та же связь)
    row = rows[str(reverify_qid)]
    assert row["answer"]["kind"] == card["result"]["kind"]
    assert row["answer"]["label"] == card["result"]["label"]
    assert row["answer"]["relation_label"] == card["claims"][0]["relation_label"]
    assert row["answer"]["statement"] is not None and row["answer"]["statement"].startswith(STATEMENT[:20])
    assert row["answer"]["reliability"]["label"] == "Проверено"

    # карточка вопроса-якоря от этой правки не изменилась: там утверждение создано им
    anchor_app, anchor_engine = await _make(scratch_url, tmp_path / "host2", tmp_path / "unit2.json")
    async with _client(anchor_app) as client:
        anchor_card = (await client.get(f"/api/v1/questions/{anchor_qid}/answer")).json()
    await anchor_engine.dispose()
    assert anchor_card["claims"][0]["relation"] == "created"
    assert anchor_card["result"]["kind"] == "answered"
