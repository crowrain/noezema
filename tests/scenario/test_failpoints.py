"""M2 failpoints (T2.22, T2.21): kill between action start/result and the
safe-boundary rule for unknown outcomes.

- docker: a container killed mid-command yields ``result_unknown`` — the
  action is outcome_unknown, never retried, never a clean failure;
- orchestrator: a session with an unknown action outcome ends ``failed``
  at the commit boundary, not ``succeeded_partial``.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.broker import SandboxToolBroker
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import IdempotencyClass, QuestionOrigin, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.schemas.observation import Observation
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM, _runtime, _sealed

pytestmark = [pytest.mark.scenario]


@pytest.mark.asyncio
async def test_container_kill_mid_execution_is_outcome_unknown(
    sandbox_image: str, tmp_path: Path
) -> None:
    """T2.22: kill -9 the sandbox while a non-idempotent command is
    running. The broker must report result_unknown (the command may have
    executed partially), never retry, and never call it a clean failure."""
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    base = tmp_path / "base"
    base.mkdir()

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)

        # a long command; the container is killed while it runs
        async def run_long() -> Observation:
            return await broker.execute("shell.execute", {"command": "sleep 30"})

        task = asyncio.create_task(run_long())
        await asyncio.sleep(1.5)  # let docker exec start inside the container
        # SIGKILL the container (docker kill takes the signal via -s)
        await rt._run(["kill", "-s", "KILL", handle.container_name], timeout=30)

        obs = await task
        assert not obs.ok
        assert obs.result_unknown
        assert not obs.transient  # unknown outcomes are never retried
        assert obs.idempotency_class is IdempotencyClass.NON_IDEMPOTENT
        # the container is really gone
        assert not await rt.is_running(handle)
    finally:
        await rt.destroy(handle)


class _UnknownOnceExecutor:
    """Delegates to the stub but loses the first python.execute process."""

    def __init__(self, inner: StubToolExecutor) -> None:
        self.inner = inner
        self.workspace_dir = inner.workspace_dir
        self.used = False

    async def execute(
        self, tool: str, arguments: JsonDict, *, db: object | None = None
    ) -> Observation:
        if not self.used and tool == "python.execute":
            self.used = True
            return Observation(
                tool=tool,
                ok=False,
                error="container gone mid-execution",
                result_unknown=True,
                idempotency_class=IdempotencyClass.NON_IDEMPOTENT,
            )
        return await self.inner.execute(tool, arguments, db=db)


def _make_orchestrator(scratch_url: str, fake: FakeLLM, workspace: Path) -> tuple[Orchestrator, LLMMiddleware, object]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=_UnknownOnceExecutor(StubToolExecutor(workspace)),
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


TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить",
    "expected_information": "6*7",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE: JsonDict = {
    "public_rationale": "Готово",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "один claim",
    "claims": [
        {"statement": "6*7=42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}
    ],
    "evidence_links": [],
    "new_questions": [],
}


@pytest.mark.asyncio
async def test_unknown_action_fails_session_at_commit(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T2.21 safe boundary: an action with an unknown outcome makes the
    session ``failed`` at the commit boundary — never succeeded_partial,
    and the staging changes are discarded by the reconciliation."""
    scratch_url, _ = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},  # this one is lost
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

    # the goal was "reached", but the unknown outcome overrides it
    assert outcome.final_state is SessionState.FAILED
    assert outcome.termination_reason == "unknown_action_outcome"

    engine = create_async_engine(scratch_url)
    sid = str(outcome.session_id)
    try:
        async with engine.connect() as conn:
            action_state = (
                await conn.execute(
                    text(
                        "SELECT state FROM actions WHERE session_id=:s AND tool='python.execute'"
                    ),
                    {"s": sid},
                )
            ).scalar_one()
            unknown_audit = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM audit_events "
                        "WHERE session_id=:s AND type='action_outcome_unknown'"
                    ),
                    {"s": sid},
                )
            ).scalar_one()
            # the curator's staging was NOT applied (the commit failed)
            applied = (
                await conn.execute(
                    text(
                        "SELECT COUNT(*) FROM session_staging "
                        "WHERE session_id=:s AND state='applied'"
                    ),
                    {"s": sid},
                )
            ).scalar_one()
            knowledge_rev = (
                await conn.execute(
                    text("SELECT revision FROM domain_revisions WHERE scope='knowledge'")
                )
            ).scalar_one()
        assert action_state == "outcome_unknown"
        assert unknown_audit == 1
        assert applied == 0  # no mixed state: staging is discarded
        assert knowledge_rev == 0  # no knowledge bump on a failed session
    finally:
        await engine.dispose()
