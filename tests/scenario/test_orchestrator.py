"""Scenario: full cognitive session through the orchestrator (T1.14-T1.18).

Runs a real Sealed session against PostgreSQL + the deterministic fake LLM.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin, QuestionState, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
}
TOOL_WRITE: JsonDict = {
    "public_rationale": "Записать вывод",
    "decision": {
        "kind": "tool",
        "tool": "workspace.write",
        "arguments": {"path": "notes.md", "content": "6*7=42"},
    },
}
COMPLETE: JsonDict = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [
        {
            "statement": "6*7 равно 42",
            "claim_type": "computed_result",
            "scope": {"expr": "6*7"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [
        {"text": "Почему 42?", "origin": "previous_result"}
    ],
}


def _make_orchestrator(
    scratch_url: str,
    fake: FakeLLM,
    workspace: Path,
) -> tuple[Orchestrator, LLMMiddleware, object]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
    )
    return orch, gateway, engine


async def _seed_question(scratch_url: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db, ORMQuestion(text="Сколько будет 6*7?", origin=QuestionOrigin.SEEDED.value)
            )
            return q.id
    finally:
        await engine.dispose()


async def _session_state(scratch_url: str, session_id: uuid.UUID) -> str:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(text("SELECT state FROM sessions WHERE id=:id"), {"id": str(session_id)})
            ).scalar_one()
            return row
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_full_sealed_session(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": TOOL_WRITE},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == question_id
    assert outcome.steps == 3
    assert outcome.evidence_count == 1  # only python.execute is an observation
    assert outcome.claims_proposed == 1
    assert outcome.termination_reason == "goal_reached"

    assert await _session_state(scratch_url, outcome.session_id) == "succeeded"

    # causal chain rows
    engine = create_async_engine(scratch_url)
    try:
        sid = str(outcome.session_id)
        async with engine.connect() as conn:
            runs = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM model_runs WHERE session_id=:s"), {"s": sid}
                )
            ).scalar_one()
            actions = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM actions WHERE session_id=:s"), {"s": sid}
                )
            ).scalar_one()
            audit = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM audit_events WHERE session_id=:s"), {"s": sid}
                )
            ).scalar_one()
            outbox = (await conn.execute(text("SELECT COUNT(*) FROM outbox_events"))).scalar_one()
            question_state = (
                await conn.execute(text("SELECT state FROM questions WHERE id=:q"), {"q": str(question_id)})
            ).scalar_one()
    finally:
        await engine.dispose()

    assert runs == 4  # 3 explorer + 1 curator
    assert actions == 2
    assert audit >= 8
    assert outbox == audit  # every audit event has an outbox twin
    assert question_state == QuestionState.VERIFIED.value


@pytest.mark.asyncio
async def test_no_question_fails(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    fake_llm.script([])  # nothing needed
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session()
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.FAILED
    assert outcome.termination_reason == "no_question"
    assert await _session_state(scratch_url, outcome.session_id) == "failed"


@pytest.mark.asyncio
async def test_budget_exhausted_partial(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    # one tool step, then complete with a non-goal reason -> succeeded_partial
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {
                "content": {
                    "public_rationale": "Бюджет исчерпан",
                    "decision": {"kind": "complete", "reason": "budget_exhausted"},
                }
            },
            {"content": CURATOR_OK},
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED_PARTIAL
    assert outcome.termination_reason == "budget_exhausted"
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            question_state = (
                await conn.execute(text("SELECT state FROM questions WHERE id=:q"), {"q": str(question_id)})
            ).scalar_one()
    finally:
        await engine.dispose()
    assert question_state == QuestionState.PARTIALLY_ANSWERED.value


@pytest.mark.asyncio
async def test_curator_failure_reports_but_commits(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"error": "invalid_json"},  # curator attempt 1
            {"error": "invalid_json"},  # curator attempt 2 (max_retries=2)
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    # curator failed but the session still commits with zero claims
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.claims_proposed == 0
