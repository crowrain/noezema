"""Scenario: full cognitive session through the orchestrator (T1.14-T1.18).

Runs a real Sealed session against PostgreSQL + the deterministic fake LLM.
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
    lease_ttl: timedelta | None = None,
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
        lease_ttl=lease_ttl,
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


async def _scalar(scratch_url: str, sql: str, params: dict | None = None) -> Any:
    """One-shot row read on the scratch DB (test plumbing only)."""
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
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

    # T7.7: started_at is set at creation (evaluation window anchor §22.2)
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            started = (
                await conn.execute(
                    text(
                        "SELECT started_at FROM sessions WHERE id=:id"
                    ),
                    {"id": str(outcome.session_id)},
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert started is not None, "sessions.started_at must be set at creation"
    assert started.tzinfo is not None


@pytest.mark.asyncio
async def test_memory_search_and_claim_reuse(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    """T7.7 (EVAL-2, failed gate significant_claim_reuse):

    1. memory.search executes against durable memory (no more stub);
    2. a claim proposed with `dependencies` on an existing claim commits
       a claim_dependencies edge (the reuse gate's numerator source).
    """
    scratch_url, _engine = migrated_db

    # session 1: seed the corpus with one claim
    q1 = await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake_llm.script([
        {"content": TOOL_PYTHON},
        {"content": TOOL_WRITE},
        {"content": COMPLETE},
        {"content": CURATOR_OK},
    ])
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws1")
    try:
        outcome1 = await orch.run_session(q1)
    finally:
        await gateway.close()
        await engine.dispose()
    assert outcome1.final_state is SessionState.SUCCEEDED

    row = await _scalar(
        scratch_url,
        "SELECT id FROM claims WHERE statement = :s",
        {"s": "6*7 равно 42"},
    )
    assert row is not None, "session 1 claim missing"
    claim1_id = str(row[0])

    # session 2: the related question; the model searches memory and
    # references the existing claim in dependencies
    q2 = await _seed_question(scratch_url, "Умножение 6 и 7, проверь результат.")
    TOOL_PYTHON2: JsonDict = {
        "public_rationale": "Пересчитать 6*7",
        "expected_information": "Результат 42",
        "decision": {
            "kind": "tool",
            "tool": "python.execute",
            "arguments": {"code": "print(6*7)"},
        },
    }
    TOOL_SEARCH: JsonDict = {
        "public_rationale": "Искать уже известные claims",
        "expected_information": "Существующие claims про 6*7",
        "decision": {
            "kind": "tool",
            "tool": "memory.search",
            "arguments": {"query": "6 7 42 произведение"},
        },
    }
    CURATOR_REUSE: JsonDict = {
        "summary": "Проверка: произведение 6 и 7",
        "claims": [
            {
                "statement": "Произведение 6 и 7 равно 42",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
                "dependencies": [{"claim_id": claim1_id}],
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }
    fake_llm.script([
        {"content": TOOL_PYTHON2},
        {"content": TOOL_SEARCH},
        {"content": COMPLETE},
        {"content": CURATOR_REUSE},
    ])
    orch2, gateway2, engine2 = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws2")
    try:
        outcome2 = await orch2.run_session(q2)
    finally:
        await gateway2.close()
        await engine2.dispose()
    assert outcome2.final_state is SessionState.SUCCEEDED

    # the reuse edge exists at the commit boundary
    dep = await _scalar(
        scratch_url,
        "SELECT from_claim_id, to_claim_id FROM claim_dependencies "
        "WHERE to_claim_id = :to",
        {"to": claim1_id},
    )
    assert dep is not None, "claim_dependencies edge for the reused claim missing"


@pytest.mark.asyncio
async def test_slow_llm_does_not_lose_commit_lease(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    """T3.30 regression (first real MVP session, qwen36-35b-a3b-q6-mtp):

    a model call longer than the lease TTL must not starve the fenced
    commit — the background guard renews the lease mid-call (§5.2.3).
    Without the guard this session ends commit_lease_lost → reconciled_abort.
    """
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    # every model call takes 2.5 s — more than 2x the 1 s lease TTL below
    fake_llm.script(
        [
            {"content": TOOL_PYTHON, "delay_seconds": 2.5},
            {"content": TOOL_WRITE, "delay_seconds": 2.5},
            {"content": COMPLETE, "delay_seconds": 2.5},
            {"content": CURATOR_OK, "delay_seconds": 2.5},
        ]
    )
    orch, gateway, engine = _make_orchestrator(
        scratch_url, fake_llm, tmp_path / "ws", lease_ttl=timedelta(seconds=1)
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state is SessionState.SUCCEEDED
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
            # M2: staging rows recorded + applied at commit
            staging = (
                await conn.execute(
                    text(
                        "SELECT op, state FROM session_staging "
                        "WHERE session_id=:s ORDER BY created_at"
                    ),
                    {"s": sid},
                )
            ).fetchall()
            # M2: the frozen overlay manifest is attached to the session
            manifest = (
                await conn.execute(
                    text(
                        "SELECT m.entry_count FROM workspace_manifests m "
                        "JOIN sessions s ON s.committed_workspace_manifest_id = m.id "
                        "WHERE s.id=:s"
                    ),
                    {"s": sid},
                )
            ).fetchall()
            # M2: the commit attempt went prepared -> committed in the
            # fenced final transaction (T2.18/T2.19)
            attempt = (
                await conn.execute(
                    text(
                        "SELECT a.status, a.staging_hash IS NOT NULL, "
                        "a.workspace_manifest_id IS NOT NULL "
                        "FROM commit_attempts a JOIN sessions s ON s.commit_attempt_id = a.id "
                        "WHERE s.id=:s"
                    ),
                    {"s": sid},
                )
            ).fetchall()
            # M2: the knowledge revision was bumped in the same transaction
            knowledge_rev = (
                await conn.execute(text("SELECT revision FROM domain_revisions WHERE scope='knowledge'"))
            ).scalar_one()
            # M3: the memory model was applied inside the fenced tx — the
            # claim, its evidence, the assessment and the current head
            claims = (
                await conn.execute(
                    text("SELECT statement, claim_type FROM claims WHERE created_in_session=:s"),
                    {"s": sid},
                )
            ).fetchall()
            evidence = (
                await conn.execute(
                    text("SELECT evidence_kind, relation FROM evidence WHERE created_in_session=:s"),
                    {"s": sid},
                )
            ).fetchall()
            heads = (
                await conn.execute(
                    text(
                        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade, a.confidence "
                        "FROM claim_assessment_heads h "
                        "JOIN claims c ON c.id = h.claim_id AND c.created_in_session=:s "
                        "JOIN claim_assessments a ON a.id = h.current_assessment_id"
                    ),
                    {"s": sid},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert runs == 4  # 3 explorer + 1 curator
    assert actions == 2
    assert audit >= 8
    assert outbox == audit  # every audit event has an outbox twin
    assert question_state == QuestionState.VERIFIED.value

    # the curator's claim + evidence link + question went through
    # session_staging and were applied at the commit boundary
    assert len(staging) == 3
    assert {row[0] for row in staging} == {"claim", "evidence", "question"}
    assert all(row[1] == "applied" for row in staging)

    # M3: the memory model was applied inside the fenced final tx
    assert len(claims) == 1
    assert claims[0][1] == "computed_result"
    assert len(evidence) == 1
    assert evidence[0] == ("computation", "supports")
    assert len(heads) == 1
    state, epistemic, grade, confidence = heads[0]
    assert state == "current"
    assert epistemic == "supported"
    assert grade == "E2"
    assert confidence == 0.55
    # the stub executor's workspace (with notes.md) was frozen into the
    # manifest
    assert len(manifest) == 1
    assert manifest[0][0] >= 1

    # the fenced commit: one attempt, prepared -> committed, bound to the
    # staging hash and the manifest
    assert len(attempt) == 1
    assert attempt[0][0] == "committed"
    assert attempt[0][1] is True  # staging_hash set
    assert attempt[0][2] is True  # workspace manifest bound
    # the knowledge revision moved from the seed 0 to 1
    assert knowledge_rev == 1


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


TOOL_DENIED: JsonDict = {
    "public_rationale": "Попробовать недопустимый инструмент",
    "decision": {
        "kind": "tool",
        "tool": "web.fetch",
        "arguments": {"url": "https://example.com"},
    },
}


@pytest.mark.asyncio
async def test_policy_denies_and_session_continues(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """A tool outside the profile is denied by the Policy Engine: the action
    is never executed, the decision is audited, the model sees the denial,
    and the session continues (T2.5)."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    fake_llm.script(
        [
            {"content": TOOL_DENIED},  # denied by policy
            {"content": TOOL_PYTHON},  # allowed
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
    assert outcome.termination_reason == "goal_reached"
    assert outcome.steps == 3

    engine = create_async_engine(scratch_url)
    try:
        sid = str(outcome.session_id)
        async with engine.connect() as conn:
            # both the denied and the allowed action rows exist
            action_rows = (
                await conn.execute(
                    text("SELECT tool, policy_decision, state, error_code FROM actions WHERE session_id=:s"),
                    {"s": sid},
                )
            ).fetchall()
            actions = {r[0]: (r[1], r[2], r[3]) for r in action_rows}
            policy_evaluated = (
                await conn.execute(
                    text(
                        "SELECT payload FROM audit_events "
                        "WHERE session_id=:s AND type='policy_evaluated' ORDER BY sequence"
                    ),
                    {"s": sid},
                )
            ).fetchall()
    finally:
        await engine.dispose()

    assert set(actions) == {"web.fetch", "python.execute"}
    d_decision, d_state, d_error = actions["web.fetch"]
    a_decision, a_state, _a_error = actions["python.execute"]
    assert d_decision in ("deny", "require_operator")
    assert d_state == "failed"
    assert d_error == f"policy:{d_decision}"
    assert a_decision == "allow"
    assert a_state == "completed"

    # every tool decision produced a PolicyEvaluated audit row
    assert len(policy_evaluated) == 2
    assert policy_evaluated[0][0]["decision"] in ("deny", "require_operator")
    assert policy_evaluated[1][0]["decision"] == "allow"
    assert policy_evaluated[0][0]["profile_version"] == "sealed-v1"


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


async def _run_fake_session(
    scratch_url: str, fake: FakeLLM, tmp_path: Path, question_id: uuid.UUID, script: list[dict]
) -> Any:
    fake_llm_script = script
    orch, gateway, engine = _make_orchestrator(scratch_url, fake, tmp_path / f"ws-{len(fake_llm_script)}")
    try:
        fake.script(list(script))
        return await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_curator_dependency_committed_bumps_graph_revision(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T4.1: the curator declares an evidential dependency on an existing
    claim; the fenced commit writes the edge AND bumps the
    dependency_graph revision (fence = base revision from prepare)."""
    scratch_url, _engine = migrated_db

    # session 1: a plain session that commits claim A
    q1 = await _seed_question(scratch_url)
    outcome1 = await _run_fake_session(
        scratch_url, fake_llm, tmp_path, q1, [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}]
    )
    assert outcome1.final_state is SessionState.SUCCEEDED

    row = await _scalar(
        scratch_url, "SELECT id, statement FROM claims ORDER BY observed_at LIMIT 1"
    )
    assert row is not None
    a_id = row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))

    # session 2: the curator proposes claim B with an evidential
    # dependency on A (the model would see A in the [c:<uuid>] lines)
    q2 = await _seed_question(scratch_url, text="Проверьте 42*1 ещё раз")
    curator_dep: dict[str, Any] = {
        "summary": "Зависимость от A",
        "claims": [
            {
                "statement": "42*1 равно 42",
                "claim_type": "computed_result",
                "scope": {"expr": "42*1"},
                "dependencies": [{"claim_id": str(a_id), "kind": "evidential"}],
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }
    outcome2 = await _run_fake_session(
        scratch_url,
        fake_llm,
        tmp_path,
        q2,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": curator_dep}],
    )
    assert outcome2.final_state is SessionState.SUCCEEDED

    # the edge is committed, direction: from(B) depends on to(A)
    edge = await _scalar(
        scratch_url,
        "SELECT from_claim_id, to_claim_id, kind FROM claim_dependencies",
    )
    assert edge is not None
    b_id = edge[0] if isinstance(edge[0], uuid.UUID) else uuid.UUID(str(edge[0]))
    assert edge[1] == a_id
    assert b_id != a_id
    assert edge[2] == "evidential"

    # the graph revision moved 0 -> 1 (it was bumped in the fenced commit)
    rev = await _scalar(
        scratch_url, "SELECT revision FROM domain_revisions WHERE scope='dependency_graph'"
    )
    assert int(rev[0]) == 1

    # the commit audit carries the new graph revision
    audit = await _scalar(
        scratch_url,
        "SELECT payload FROM audit_events "
        "WHERE type='commit_attempt_committed' AND session_id=:s",
        {"s": str(outcome2.session_id)},
    )
    assert audit is not None
    assert int(audit[0].get("dependency_graph_revision", -1)) == 1


@pytest.mark.asyncio
async def test_curator_cycle_dependency_rejected_graph_unchanged(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """T4.1: a dependency that would close an evidential cycle is
    rejected at the commit boundary; the claim still commits and the
    dependency_graph revision is NOT bumped (no evidential edge changed)."""
    scratch_url, _engine = migrated_db

    # session 1: two claims A and B, no dependencies
    q1 = await _seed_question(scratch_url)
    curator_two: dict[str, Any] = {
        "summary": "Два утверждения",
        "claims": [
            {"statement": "A: база", "claim_type": "external_fact", "scope": {"obj": "a"}},
            {"statement": "B: из A", "claim_type": "computed_result", "scope": {"expr": "b"}},
        ],
        "evidence_links": [],
        "new_questions": [],
    }
    outcome1 = await _run_fake_session(
        scratch_url, fake_llm, tmp_path, q1, [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": curator_two}]
    )
    assert outcome1.final_state is SessionState.SUCCEEDED

    async def _claim_id(stmt: str) -> uuid.UUID:
        row = await _scalar(scratch_url, "SELECT id FROM claims WHERE statement=:s", {"s": stmt})
        assert row is not None
        return row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))

    a_id = await _claim_id("A: база")
    b_id = await _claim_id("B: из A")

    # session 2: REUSED A with dependency A -> B (legal: B is current, no
    # path from B back to A) → the edge commits, graph revision 0 -> 1
    q2 = await _seed_question(scratch_url, text="Свяжите A и B")
    curator_a_dep: dict[str, Any] = {
        "summary": "A зависит от B",
        "claims": [
            {
                "statement": "A: база",
                "claim_type": "external_fact",
                "scope": {"obj": "a"},
                "dependencies": [{"claim_id": str(b_id), "kind": "evidential"}],
            }
        ],
        "evidence_links": [],
        "new_questions": [],
    }
    outcome2 = await _run_fake_session(
        scratch_url,
        fake_llm,
        tmp_path,
        q2,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": curator_a_dep}],
    )
    assert outcome2.final_state is SessionState.SUCCEEDED
    rev = await _scalar(
        scratch_url, "SELECT revision FROM domain_revisions WHERE scope='dependency_graph'"
    )
    assert int(rev[0]) == 1

    # session 3: REUSED B with dependency B -> A: closes the cycle
    # A -> B -> A. The edge must be rejected (audit), the claim still
    # commits, and the graph revision stays at 1.
    q3 = await _seed_question(scratch_url, text="Свяжите B и A обратно")
    curator_b_dep: dict[str, Any] = {
        "summary": "B зависит от A (цикл!)",
        "claims": [
            {
                "statement": "B: из A",
                "claim_type": "computed_result",
                "scope": {"expr": "b"},
                "dependencies": [{"claim_id": str(a_id), "kind": "evidential"}],
            }
        ],
        "evidence_links": [],
        "new_questions": [],
    }
    outcome3 = await _run_fake_session(
        scratch_url,
        fake_llm,
        tmp_path,
        q3,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": curator_b_dep}],
    )
    assert outcome3.final_state is SessionState.SUCCEEDED

    edges = await _scalar(scratch_url, "SELECT count(*) FROM claim_dependencies")
    assert int(edges[0]) == 1  # the cyclic edge was not written

    rev = await _scalar(
        scratch_url, "SELECT revision FROM domain_revisions WHERE scope='dependency_graph'"
    )
    assert int(rev[0]) == 1  # unchanged

    rejected = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events "
        "WHERE type='dependency_edge_rejected' AND session_id=:s",
        {"s": str(outcome3.session_id)},
    )
    assert int(rejected[0]) == 1
