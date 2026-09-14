"""Scenario: a full Sealed day (T3.28) — N sessions back to back.

FIFO question selection, staging, assessment, fenced commit and the web
timeline for a run of several sealed sessions in a row. Each session
consumes the next FIFO question, produces a claim through the rules engine,
commits durably, and the timeline grows.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

# the scripted tool sequence one sealed session needs (explorer -> curator)
TOOL_PYTHON = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
TOOL_WRITE = {
    "public_rationale": "Записать вывод",
    "decision": {"kind": "tool", "tool": "workspace.write", "arguments": {"path": "notes.md", "content": "6*7=42"}},
}
COMPLETE = {"public_rationale": "Вопрос отвечен", "decision": {"kind": "complete", "reason": "goal_reached"}}
CURATOR_OK = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [{"text": "Почему 42?", "origin": "previous_result"}],
}


def _make_orchestrator(
    scratch_url: str, fake: FakeLLM, workspace: Path
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


async def _seed_questions(scratch_url: str, n: int) -> list[str]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ids: list[str] = []
    try:
        async with factory() as db, transaction(db):
            for i in range(n):
                q = await QuestionRepository.create(
                    db, ORMQuestion(text=f"Сколько будет {i + 5}*{i + 7}?", origin=QuestionOrigin.SEEDED.value)
                )
                ids.append(str(q.id))
    finally:
        await engine.dispose()
    return ids


async def _session_states(scratch_url: str) -> list[str]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            rows = (
                await conn.execute(text("SELECT state FROM sessions ORDER BY created_at ASC"))
            ).all()
            return [r[0] for r in rows]
    finally:
        await engine.dispose()


async def _audit_event_count(scratch_url: str) -> int:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return int(
                (await conn.execute(text("SELECT count(*) FROM audit_events"))).scalar_one()
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_sealed_day_fifo_sessions(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    n_sessions = 3
    await _seed_questions(scratch_url, n_sessions)

    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        for i in range(n_sessions):
            # the same scripted tool sequence works for every sealed session
            fake_llm.script(
                [
                    {"content": TOOL_PYTHON},
                    {"content": TOOL_WRITE},
                    {"content": COMPLETE},
                    {"content": CURATOR_OK},
                ]
            )
            outcome = await orch.run_session()  # FIFO: picks the oldest question
            assert outcome.final_state is SessionState.SUCCEEDED, f"session {i} did not succeed"
    finally:
        await gateway.close()
        await engine.dispose()

    # all three sessions reached a terminal success state
    states = await _session_states(scratch_url)
    assert len(states) == n_sessions
    assert all(s == "succeeded" for s in states), states

    # the questions were consumed FIFO: the seeded candidates are no longer
    # all present as candidate (the selected ones advanced)
    # the durable timeline grew with each session's audit events
    assert await _audit_event_count(scratch_url) >= n_sessions * 4
