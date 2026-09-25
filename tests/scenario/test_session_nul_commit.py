"""Scenario: T7.46a — a NUL byte in python.execute output does not kill
the session commit.

Reproduces SMOKE-V12-K2 §3 end-to-end: the model runs a tool whose
stdout carries a NUL byte BEYOND the 1000-char ``_cap_args`` cap of the
early per-action audit inserts but WITHIN the 2000-char evidence-payload
cap, so the NUL first reaches JSONB in the final report audit event.
Before the fix that INSERT raised UntranslatableCharacterError and the
whole phase-1 transaction rolled back (all steps lost); after the fix
the session COMMITS: the audit event is recorded, the evidence carries
the visible marker, and the durable identity hash is consistent with the
stored (masked) value.
"""

from __future__ import annotations

import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.canonical import canonical_sha256
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.domain.sanitization import NUL_MARKER
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.evidence import computation_identity
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

# the child prints 1200 chars, then a NUL byte, then 500 chars: the NUL
# sits past the 1000-char _cap_args cap (the early action audit inserts
# survive) and inside the 2000-char evidence-payload cap (the report
# event carries it) — the exact SMOKE-V12-K2 defect position.
NUL_CODE = "import sys; sys.stdout.write('x' * 1200 + '\\x00' + 'y' * 500)"

TOOL_PYTHON_NUL: JsonDict = {
    "public_rationale": "Проверить вывод с управляющим байтом",
    "expected_information": "Длина вывода",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": NUL_CODE},
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
    "new_questions": [],
}


def _make_orchestrator(
    scratch_url: str, fake: FakeLLM, workspace: Path
) -> tuple[Orchestrator, LLMMiddleware, Any]:
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
        lease_ttl=timedelta(minutes=10),
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


@pytest.mark.asyncio
async def test_nul_in_python_output_commits_session(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script([
        {"content": TOOL_PYTHON_NUL},
        {"content": COMPLETE},
        {"content": CURATOR_OK},
    ])
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    # the session COMMITS: before the fix the report audit INSERT raised
    # UntranslatableCharacterError and this whole phase-1 transaction
    # rolled back (run_session would have raised / no row would exist)
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.evidence_count == 1
    assert outcome.claims_proposed == 1

    engine2 = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine2, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            state = (
                await db.execute(
                    text("SELECT state FROM sessions WHERE id=:id"), {"id": str(outcome.session_id)}
                )
            ).scalar_one()
            assert state == "succeeded"

            # the FINAL report audit event is recorded — the exact INSERT
            # that killed the SMOKE-V12-K2 session
            report = (
                await db.execute(
                    text(
                        "SELECT payload FROM audit_events "
                        "WHERE session_id = :id AND type = 'session_state_changed' "
                        "AND public_summary LIKE 'report:%' ORDER BY sequence DESC LIMIT 1"
                    ),
                    {"id": str(outcome.session_id)},
                )
            ).first()
            assert report is not None, "the report audit event must be recorded"
            report_payload = report[0]
            ev = report_payload["evidence"][0]
            # the evidence carries the VISIBLE marker — the NUL did not
            # disappear silently
            assert "\x00" not in str(report_payload)
            assert ev["payload"]["stdout"] == f"{'x' * 1200}{NUL_MARKER}{'y' * 500}"
            # the session-time identity hash is computed over the MASKED
            # stdout (the same value family stored in the payload)
            masked = f"{'x' * 1200}{NUL_MARKER}{'y' * 500}"
            assert ev["identity_hash"] == canonical_sha256(
                {
                    "tool": "python.execute",
                    "code": NUL_CODE,
                    "exit_code": 0,
                    "stdout": masked[:4000],
                }
            )

            # the claim committed WITH its evidence; the DURABLE identity
            # (recomputed by the trusted host at the commit boundary from
            # the payload + the artifact hash) must equal the
            # recomputation from the STORED values — no divergence
            row = (
                await db.execute(
                    text(
                        "SELECT e.identity_hash, a.sha256, e.observation_artifact_id "
                        "FROM evidence e "
                        "JOIN artifacts a ON a.id = e.observation_artifact_id "
                        "WHERE e.claim_id = (SELECT id FROM claims WHERE statement = :s)"
                    ),
                    {"s": "6*7 равно 42"},
                )
            ).first()
            assert row is not None, "the claim committed without its computation evidence"
            stored_identity, artifact_sha, artifact_id = row
            assert artifact_id is not None
            assert (
                computation_identity(
                    ev["payload"]["stdout"], ev["payload"]["code"], artifact_sha
                )
                == stored_identity
            )

            # staging survived the commit: the session's ops were applied
            staging = (
                await db.execute(
                    text("SELECT count(*)::int FROM session_staging WHERE session_id = :id"),
                    {"id": str(outcome.session_id)},
                )
            ).scalar_one()
            assert staging >= 2  # claim + evidence link
    finally:
        await engine2.dispose()
