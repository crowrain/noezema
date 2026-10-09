"""T7.84 (часть A): отказ куратора обязан сказать, ЧТО именно было отклонено.

Стендовый замер (сессия ed36f4a0, .92, config-v20, 2026-10-09): куратор принёс два
`temporal_fact` без `as_of`; гейт схемы отклонил всё предложение; `claim_created` не
написан, сырой ответ модели в `model_runs` не сохранён. Оператор видит две строки
причины и ничего о содержимом — проверить, была ли это перепроверка уже записанных
утверждений или выдуманное новое, было нельзя.

Правка: в payload ТОГО ЖЕ существующего события отказа появляется ключ `rejected_proposal`
— компактный дайджест отклонённого предложения (ops, связи, опоры, число вопросов,
summary). Новых типов событий нет, миграции нет, прежние ключи событий не меняются,
текст модели не попадает ни в улики, ни в знание — только в журнал.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest

from tests.conftest import FakeLLM
from tests.scenario.test_commit_boundary import (
    READING_MD,
    _complete,
    _curator,
    _make_orchestrator,
    _seed_question,
    _tool,
)
from tests.scenario.test_reverify_curator import _anchor_claim_id
from tests.scenario.test_scope_coverage import _all

pytestmark = pytest.mark.scenario


async def _rejection_payloads(scratch_url: str, session_id: uuid.UUID) -> list[dict[str, Any]]:
    rows = await _all(
        scratch_url,
        "SELECT payload FROM audit_events WHERE session_id = :s "
        "AND (payload ? 'curator_rejected' OR payload ? 'curator_rejected_by_rules')",
        {"s": str(session_id)},
    )
    return [dict(row["payload"]) for row in rows]


async def _run(scratch_url: str, fake_llm: FakeLLM, workspace: Path, question_id: uuid.UUID) -> Any:
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, workspace)
    try:
        return await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_temporal_fact_without_as_of_records_what_was_rejected(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Отказ по отсутствующей дате: журнал называет операцию — формулировку, тип, пустую
    дату, отсутствие ссылки перепроверки и ключи scope."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Какая официальная инфляция в России за 2025 год?")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print('rosstat')"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": (
                            "Официальная годовая инфляция в России по итогам 2025 года "
                            "составила 5,59% (по данным Росстата)."
                        ),
                        "claim_type": "temporal_fact",
                        "scope": {"объект": "инфляция"},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    outcome = await _run(scratch_url, fake_llm, tmp_path / "ws1", question_id)
    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 0

    payloads = await _rejection_payloads(scratch_url, outcome.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    # прежний ключ на месте и прежней строкой причины
    assert any("temporal_fact requires as_of" in problem for problem in payload["curator_rejected"])

    digest = payload["rejected_proposal"]
    entry = digest["claims"][0]
    assert entry["claim_index"] == 0
    assert entry["claim_type"] == "temporal_fact"
    assert entry["as_of"] is None
    assert entry["existing_claim_id"] is None
    assert entry["scope_keys"] == ["объект"]
    assert "5,59" in entry["statement"]
    assert digest["evidence_links"] == [
        {"claim_index": 0, "evidence_index": 0, "relation": "supports"}
    ]
    # потолок размера дайджеста закреплён юнит-тестом (константа модуля); в журнале при этом
    # остаётся ровно та формулировка, которую куратор предложил хосту
    assert digest["claims"][0]["statement"].startswith("Официальная годовая инфляция")


@pytest.mark.asyncio
async def test_reverify_unresolved_rejection_records_what_was_rejected(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Отказ неразрешимой перепроверки (`reverify_unresolved`) несёт тот же дайджест:
    видно, на какой id указывала модель и что она обещала записать."""
    scratch_url, _engine = migrated_db
    await _anchor_claim_id(scratch_url, fake_llm, tmp_path / "ws1")

    unknown = uuid.uuid4()
    question_id = await _seed_question(scratch_url, "Сколько будет 6*7? Проверь ещё раз.")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42 (перепроверено)",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                        "existing_claim_id": str(unknown),
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    outcome = await _run(scratch_url, fake_llm, tmp_path / "ws2", question_id)
    assert outcome.claims_proposed == 0

    payloads = await _rejection_payloads(scratch_url, outcome.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["curator_reject_kind"] == "reverify_unresolved"

    entry = payload["rejected_proposal"]["claims"][0]
    assert entry["existing_claim_id"] == str(unknown)
    assert entry["claim_type"] == "computed_result"
    assert entry["scope_keys"] == ["expr"]


@pytest.mark.asyncio
async def test_rules_engine_rejection_records_what_was_rejected(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Отказ pre-commit проверки движка правил (`curator_rejected_by_rules`) тоже несёт
    дайджест: оператор видит rejected-операцию, а не только нарушенное правило.

    Форма — канонический repro T7.24 (computed_result + local_observation), та же, что в
    `tests/scenario/test_commit_boundary.py`."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url, "Сколько непустых строк в файле notes/reading.md?")
    fake_llm.script(
        [
            _tool("workspace.write", {"path": "notes/reading.md", "content": READING_MD}),
            _tool("workspace.read", {"path": "notes/reading.md"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "Количество непустых строк в файле notes/reading.md равно 3.",
                        "claim_type": "computed_result",
                        "scope": {"path": "notes/reading.md", "metric": "nonempty_lines", "value": 3},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    outcome = await _run(scratch_url, fake_llm, tmp_path / "ws1", question_id)
    assert outcome.claims_proposed == 0

    payloads = await _rejection_payloads(scratch_url, outcome.session_id)
    assert len(payloads) == 1
    payload = payloads[0]
    assert payload["curator_rejected_by_rules"]

    digest = payload["rejected_proposal"]
    assert digest["claims"][0]["claim_type"] == "computed_result"
    assert sorted(digest["claims"][0]["scope_keys"]) == sorted(["path", "metric", "value"])
    assert digest["relied_claim_ids"] == []
    assert digest["new_questions"] == 0
