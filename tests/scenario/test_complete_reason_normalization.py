"""T7.49 (ADR-0022): session terminal status from the NORMALIZED
completion reason.

- "goal_reached — <text>" → succeeded (the T7.37b false partial), the
  question verified, sessions.termination_reason = "goal_reached", the
  complete audit event carries BOTH the raw and the normalized reason;
- free-form text without a token → succeeded_partial (previous behavior,
  unchanged), termination_reason keeps the raw string;
- unknown action outcome + "goal_reached — <text>" → failed
  (unknown_action_outcome), as before.
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
from packages.domain.models.enums import CompleteReason, IdempotencyClass, QuestionOrigin, QuestionState, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.schemas.observation import Observation
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

# SMOKE-V13-K2 0d0c1b3f shape: explicit token + em dash + free text.
COMPLETE_SUFFIXED = "goal_reached — утверждение: последняя стабильная версия Go — Go 1.27.1"
# SMOKE-V13-K2 67a6f0a4 shape: free-form text without any token (class D).
COMPLETE_FREEFORM = (
    "Вопрос отвечен и подтверждён двумя указанными источниками: "
    "193 государства-члена ООН на 15 апреля 2026 года."
)

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
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
    "new_questions": [],
}


def _make_orchestrator(
    scratch_url: str,
    fake: FakeLLM,
    workspace: Path,
    *,
    executor: object | None = None,
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
        executor=executor if executor is not None else StubToolExecutor(workspace),
    )
    return orch, gateway, engine


async def _seed_question(scratch_url: str, text: str = "Сколько будет 6*7?") -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(db, ORMQuestion(text=text, origin=QuestionOrigin.SEEDED.value))
            return q.id
    finally:
        await engine.dispose()


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> object:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _complete_audit(scratch_url: str, session_id: uuid.UUID) -> dict | None:
    """The explorer-complete audit event (session_state_changed carrying
    complete_reason) — None when absent."""
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text(
                        "SELECT payload FROM audit_events "
                        "WHERE session_id=:s AND type='session_state_changed' "
                        "AND payload->>'complete_reason' IS NOT NULL"
                    ),
                    {"s": str(session_id)},
                )
            ).fetchall()
    finally:
        await engine.dispose()
    assert len(row) == 1, f"expected exactly one complete audit event, got {len(row)}"
    return row[0][0]


class _UnknownOnceExecutor:
    """Delegates to the stub but loses the first python.execute process
    (the T2.21 unknown-outcome failpoint, cf. test_failpoints.py)."""

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


@pytest.mark.asyncio
async def test_suffixed_goal_reached_is_succeeded(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    """Class B (token + separator + suffix) → succeeded, the question
    VERIFIED, termination_reason the CANONICAL token, and the audit event
    carries both the raw and the normalized reason (raw text preserved)."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {
                "content": {
                    "public_rationale": "Вопрос отвечен",
                    "decision": {"kind": "complete", "reason": COMPLETE_SUFFIXED},
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

    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.termination_reason == "goal_reached"  # canonical token, not the raw text

    row = await _scalar(
        scratch_url,
        "SELECT state, termination_reason FROM sessions WHERE id=:s",
        {"s": str(outcome.session_id)},
    )
    assert row is not None
    assert row[0] == SessionState.SUCCEEDED.value
    assert row[1] == "goal_reached"

    q_state = await _scalar(
        scratch_url, "SELECT state FROM questions WHERE id=:q", {"q": str(question_id)}
    )
    assert q_state is not None
    assert q_state[0] == QuestionState.VERIFIED.value

    payload = await _complete_audit(scratch_url, outcome.session_id)
    assert payload is not None
    # the raw model text is NEVER lost — both fields ride in the audit
    assert payload["complete_reason"] == COMPLETE_SUFFIXED
    assert payload["normalized_reason"] == CompleteReason.GOAL_REACHED.value


@pytest.mark.asyncio
async def test_freeform_reason_stays_partial(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    """Class D (free text, no token) → succeeded_partial exactly as
    before (the safe direction): the host must not infer success from
    meaning. termination_reason keeps the raw string."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {
                "content": {
                    "public_rationale": "Вопрос отвечен",
                    "decision": {"kind": "complete", "reason": COMPLETE_FREEFORM},
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
    assert outcome.termination_reason == COMPLETE_FREEFORM  # raw, as before

    q_state = await _scalar(
        scratch_url, "SELECT state FROM questions WHERE id=:q", {"q": str(question_id)}
    )
    assert q_state is not None
    assert q_state[0] == QuestionState.PARTIALLY_ANSWERED.value

    payload = await _complete_audit(scratch_url, outcome.session_id)
    assert payload is not None
    assert payload["complete_reason"] == COMPLETE_FREEFORM
    assert payload["normalized_reason"] is None  # null — unrecognizable


@pytest.mark.asyncio
async def test_unknown_action_still_fails_despite_suffixed_goal(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T2.21 safe boundary is untouched: an unknown action outcome makes
    the session failed even when the completion reason normalizes to
    goal_reached (unknown_action_outcome → failed)."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_PYTHON},  # this one is lost
            {
                "content": {
                    "public_rationale": "Вопрос отвечен",
                    "decision": {"kind": "complete", "reason": COMPLETE_SUFFIXED},
                }
            },
            {"content": CURATOR_OK},
        ]
    )
    orch, gateway, engine = _make_orchestrator(
        scratch_url, fake_llm, tmp_path / "ws", executor=_UnknownOnceExecutor(StubToolExecutor(tmp_path / "ws"))
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.FAILED
    assert outcome.termination_reason == "unknown_action_outcome"

    row = await _scalar(
        scratch_url,
        "SELECT state, termination_reason FROM sessions WHERE id=:s",
        {"s": str(outcome.session_id)},
    )
    assert row is not None
    assert row[0] == SessionState.FAILED.value
    assert row[1] == "unknown_action_outcome"
