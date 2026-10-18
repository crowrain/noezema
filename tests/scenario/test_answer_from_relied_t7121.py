"""Scenario (fake LLM, curated research): витрина «Ответ по уже записанным знаниям»
(T7.121 часть 5, стендовый случай с4).

Стенд .92, сессия с4: куратор ответил на вопрос, опираясь на уже записанное утверждение
(`relied_claim_ids` — блок карточки «Использовано из знаний»), и не записал ни одного
нового. Карточка при этом говорила «Ответ не записан» — неправда того же рода, какую
T7.74 закрыл для перепроверенных утверждений: ответ БЫЛ, просто весь состоял из ранее
записанного знания.

Здесь та же форма воспроизводится на вымышленных числах (Тармения): сначала обычная
сессия записывает утверждение, затем вторая сессия приносит пустое по утверждениям
предложение куратора со ссылкой на уже записанное id. Карточка второй сессии:

* `result.kind == "answered_from_relied"` (прежде — `no_answer`);
* блок «Использовано из знаний» показывает ровно записанную строку с её прежней оценкой;
* записей этого вопроса нет, новая оценка не начислена, чужая строка не тронута.

Никаких внешних запросов: HTTP-слой research proxy подменён (FakeFetchClient).
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

SEED_CLAIM = (
    "Годовая инфляция в Тармении по итогам 2029 года (декабрь 2029 к декабрю 2028) "
    "составила 4,7% (обзорная группа)."
)

_PAGES: dict[str, str] = {
    "http://alpha.example/rate": (
        "Tarmania review group: annual inflation for 2029 (December to December) "
        "is 4.7 percent. Период наблюдения: декабрь 2029 к декабрю 2028."
    ),
    "http://beta.example/echo": (
        "Same review group memo reprinted: annual inflation for 2029 stayed 4.7 percent. "
        "Период наблюдения: декабрь 2029 к декабрю 2028."
    ),
}


@pytest.fixture()
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    for url, body in _PAGES.items():
        monkeypatch.setitem(PAGES, url, body)


def _payload() -> dict[str, Any]:
    payload = _curated_payload()
    payload["research_proxy"]["rate_limit_max"] = 6  # 1 fetch на сессию × 2 сессии
    return payload


async def _seed_corpus(
    scratch_url: str, fake_llm: FakeLLM, tmp_path: Path
) -> uuid.UUID:
    """Обычная сессия: единственная запись вопроса (один источник → честная оценка)."""
    qid = await _seed_question(
        scratch_url,
        "Годовая инфляция в Тармении по итогам 2029 года: приведи цифру и первоисточник. "
        "Источники: http://alpha.example/rate",
    )
    fake_llm.script(
        [
            _fetch("http://alpha.example/rate"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Запись обзорной группы Тармении.",
                    "claims": [
                        {
                            "statement": SEED_CLAIM,
                            "claim_type": "temporal_fact",
                            "as_of": "2029-12-31T00:00:00",
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
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-seed", tmp_path / "art-seed", qid)
    assert outcome.final_state.value == "succeeded"
    rows = await _all(scratch_url, "SELECT id::text FROM claims WHERE statement = :t", {"t": SEED_CLAIM})
    assert len(rows) == 1
    return uuid.UUID(rows[0]["id"])


@pytest.mark.asyncio
async def test_answer_only_from_relied_knowledge_shows_honest_result(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_fetch: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    activation = await _run_online(engine, _payload())
    assert activation.state == "active"

    seed_id = await _seed_corpus(scratch_url, fake_llm, tmp_path)

    # вторая сессия: ответ из уже записанного знания, новых утверждений куратор не несёт
    qid = await _seed_question(
        scratch_url,
        "Назови ещё раз уже записанную оценку годовой инфляции в Тармении по итогам 2029 года. "
        "Источники: http://beta.example/echo",
    )
    fake_llm.script(
        [
            _fetch("http://beta.example/echo"),
            _COMPLETE,
            {
                "content": {
                    "summary": "Ответ опирается на уже записанную оценку обзорной группы.",
                    "claims": [],
                    "evidence_links": [],
                    "new_questions": [],
                    "relied_claim_ids": [str(seed_id)],
                }
            },
        ]
    )
    outcome = await _run_session(scratch_url, fake_llm, tmp_path / "ws-echo", tmp_path / "art-echo", qid)
    assert outcome.final_state.value == "succeeded"
    session_id = outcome.session_id

    # (а) записей у этого вопроса нет: строка в claims по-прежнему одна
    rows = await _all(scratch_url, "SELECT id::text, statement FROM claims")
    assert len(rows) == 1 and rows[0]["id"] == str(seed_id)

    # (б) relied записан в payload уже существующего события (T7.82), без новой оценки seed-строки
    events = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s AND type = 'claim_created'",
        {"s": str(session_id)},
    )
    assert len(events) == 1
    assert list(events[0]["payload"].get("relied_claim_ids") or []) == [str(seed_id)]
    assessments = await _scalar(
        scratch_url,
        "SELECT count(*) FROM claim_assessments WHERE claim_id = :c AND created_in_session = :s",
        {"c": str(seed_id), "s": str(session_id)},
    )
    assert assessments is not None and assessments[0] == 0

    # (в) карточка: прежний `no_answer` («Ответ не записан») заменён честным исходом
    app, app_engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit.json")
    try:
        async with _client(app) as client:
            response = await client.get(f"/api/v1/questions/{qid}/answer")
            assert response.status_code == 200
            card = response.json()
    finally:
        await app_engine.dispose()

    result = card["result"]
    assert result["kind"] == "answered_from_relied", result
    entry = ui_labels.describe("answer_result", "answered_from_relied")
    assert result["label"] == entry["label"]
    assert result["hint"].startswith("Новых утверждений сессия не записала")
    assert result["active"] is False

    # (г) блок «Использовано из знаний» — ровно seed-строка с прежней связью relied
    assert card["claims"] == []
    relied = card["relied_claims"]
    assert [item["id"] for item in relied] == [str(seed_id)]
    assert relied[0]["relation"] == "relied"
    assert relied[0]["statement"] == SEED_CLAIM
