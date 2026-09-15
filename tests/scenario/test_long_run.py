"""Scenario: long-run behavior across many sessions (T5.6, stage 4).

The system is a long-running local-first thinker: knowledge
accumulates session after session, the question queue is worked
FIFO, repetition protection acts on the accumulated history, and
counterevidence in a later session disputes an earlier claim. These
tests run real orchestrator sessions against one scratch database
and assert on the durable state in between.
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, QuestionOrigin, QuestionState, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

COMPLETE: dict[str, Any] = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}


def _tool_python(expr: str) -> dict[str, Any]:
    return {
        "public_rationale": "Проверить вычисление",
        "expected_information": f"Результат {expr}",
        "decision": {
            "kind": "tool",
            "tool": "python.execute",
            "arguments": {"code": f"print({expr})"},
        },
    }


def _curator_claim(statement: str, scope: dict[str, Any], claim_type: str = "computed_result") -> dict[str, Any]:
    return {
        "summary": "Одно утверждение",
        "claims": [
            {"statement": statement, "claim_type": claim_type, "scope": scope},
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }


def _curator_counter(statement: str, scope: dict[str, Any], claim_type: str = "computed_result") -> dict[str, Any]:
    """The same claim, re-proposed, with the new evidence COUNTERING it
    (claim dedup makes the host attach the counter to the existing
    claim — the only staging channel for knowledge changes)."""
    return {
        "summary": "Контрпример к утверждению",
        "claims": [
            {"statement": statement, "claim_type": claim_type, "scope": scope},
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "counters"}],
        "new_questions": [],
    }


async def _seed_question(
    scratch_url: str, text_: str, state: str = "candidate", priority: int = 0
) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db,
                ORMQuestion(text=text_, origin=QuestionOrigin.SEEDED.value, priority=priority),
            )
            if state != "candidate":
                q.state = state
            return q.id
    finally:
        await engine.dispose()


async def _bootstrap_snapshot_id(scratch_url: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'")
                )
            ).first()
            assert row is not None
            return row[0]
    finally:
        await engine.dispose()


async def _seed_no_progress_session(
    scratch_url: str, question_id: uuid.UUID, bootstrap_snapshot_id: uuid.UUID, when: datetime
) -> None:
    """A finished session on the question with NO new claims (the
    trusted-host test seed for the no-progress counter)."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            session = ORMSession(
                id=uuid.uuid4(),
                state=SessionState.SUCCEEDED.value,
                question_id=question_id,
                config_snapshot_id=bootstrap_snapshot_id,
                started_at=when,
                finished_at=when + timedelta(minutes=5),
                termination_reason="goal_reached",
                created_at=when,
            )
            db.add(session)
    finally:
        await engine.dispose()


async def _enable_repetition(engine: Any, **overrides: Any) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["repetition"])
    section["enabled"] = True
    section.update(overrides)
    payload["repetition"] = section
    result = await _run_online(engine, payload)
    assert result.state == "active"


async def _scalar(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _all(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


async def _run_session(
    fake: FakeLLM, scratch_url: str, workspace: Path, script: list[dict[str, Any]]
) -> Any:
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
    try:
        fake.script(script)
        return await orch.run_session(None)
    finally:
        await gateway.close()
        await engine.dispose()


async def _claim_head(scratch_url: str, claim_id: uuid.UUID) -> Any:
    # (assessment_state, epistemic_status, effective_grade) — the grade
    # lives on the assessment row the head points to (§3.7 lifecycle)
    return await _scalar(
        scratch_url,
        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade "
        "FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": str(claim_id)},
    )


# ── test 1: knowledge accumulates across sessions ───────────────────────


async def test_multi_session_knowledge_accumulates(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Three real sessions on three questions: each claim survives the
    later sessions, heads stay current, the queue is worked FIFO, and a
    session's new question joins the queue for a future run."""
    scratch_url, _engine = migrated_db
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    q1 = await _seed_question(scratch_url, "Сколько будет 6*7?")
    q2 = await _seed_question(scratch_url, "Сколько будет 6*8?")
    q3 = await _seed_question(scratch_url, "Сколько будет 6*9?")

    out1 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("6*7")},
            {"content": COMPLETE},
            {
                "content": {
                    "summary": "Одно утверждение",
                    "claims": [
                        {
                            "statement": "6*7 равно 42",
                            "claim_type": "computed_result",
                            "scope": {"expr": "6*7"},
                        }
                    ],
                    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
                    "new_questions": [{"text": "Почему 42?", "origin": "previous_result"}],
                }
            },
        ],
    )
    out2 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("6*8")},
            {"content": COMPLETE},
            {"content": _curator_claim("6*8 равно 48", {"expr": "6*8"})},
        ],
    )
    out3 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("6*9")},
            {"content": COMPLETE},
            {"content": _curator_claim("6*9 равно 54", {"expr": "6*9"})},
        ],
    )

    # FIFO: the sessions worked the queue in the seeded order
    assert out1.question_id == q1 and out2.question_id == q2 and out3.question_id == q3
    for out in (out1, out2, out3):
        assert out.final_state is SessionState.SUCCEEDED

    # three claims, each with a single CURRENT supported head
    rows = await _all(
        scratch_url,
        "SELECT c.id, c.statement, h.assessment_state, h.epistemic_status, a.effective_grade "
        "FROM claims c JOIN claim_assessment_heads h ON h.claim_id = c.id "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "ORDER BY c.statement",
    )
    assert [r[1] for r in rows] == ["6*7 равно 42", "6*8 равно 48", "6*9 равно 54"]
    for row in rows:
        assert row[2] == "current"
        assert row[3] == "supported"
        assert row[4] == "E2"
    first_claim_id = rows[0][0]

    # exactly one assessment per claim (no duplicates across sessions)
    counts = await _all(
        scratch_url,
        "SELECT claim_id, count(*) FROM claim_assessments GROUP BY claim_id",
    )
    assert sorted(c[1] for c in counts) == [1, 1, 1]

    # the first session's claim head survived the later sessions
    head = await _claim_head(scratch_url, first_claim_id)
    assert head[0] == "current" and head[1] == "supported"

    # the questions are terminal (answered) and the new question is queued
    for qid in (q1, q2, q3):
        state = await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :q", {"q": str(qid)})
        assert state[0] == QuestionState.VERIFIED.value
    new_q = await _scalar(
        scratch_url,
        "SELECT id, state, origin FROM questions WHERE text = 'Почему 42?'",
    )
    assert new_q is not None
    assert new_q[1] == QuestionState.CANDIDATE.value
    assert new_q[2] == "previous_result"

    # the queue is now: the new question (a later session will take it)
    out4 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("7*6")},
            {"content": COMPLETE},
            {"content": _curator_claim("7*6 равно 42", {"expr": "7*6"})},
        ],
    )
    assert out4.question_id == new_q[0]
    assert out4.final_state is SessionState.SUCCEEDED


# ── test 2: repetition protection acts on the accumulated history ──────


async def test_repetition_cycle_breaks_in_long_run(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """History (a verified question with two no-progress sessions) + a
    rephrased candidate = a §9 cycle: the host picks the deterministic
    strategy, injects the note into the explorer's context, and the
    session still runs — and this time makes progress (a claim), so
    the next rephrase of the same content is not yet a cycle."""
    scratch_url, engine = migrated_db
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    await _enable_repetition(engine)

    original = "Какова масса Луны в килограммах?"
    q_old = await _seed_question(scratch_url, original, state=QuestionState.VERIFIED.value)
    snap = await _bootstrap_snapshot_id(scratch_url)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    await _seed_no_progress_session(scratch_url, q_old, snap, base)
    await _seed_no_progress_session(scratch_url, q_old, snap, base + timedelta(hours=1))
    q_rephrased = await _seed_question(
        scratch_url, "Какова масса Луны в килограммах, скажите точно?"
    )

    before = len(fake_llm.requests())
    out = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("7342e19")},
            {"content": COMPLETE},
            {"content": _curator_claim("Масса Луны составляет примерно 7.342e22 кг", {"body": "moon"})},
        ],
    )
    assert out.final_state is SessionState.SUCCEEDED
    assert out.question_id == q_rephrased

    # the cycle was detected on the accumulated history
    audit = await _scalar(
        scratch_url,
        "SELECT payload->>'strategy', payload->>'no_progress_sessions', "
        "payload->>'fingerprint', payload->>'similar_question_id' "
        "FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.REPEAT_CYCLE_DETECTED.value, "s": str(out.session_id)},
    )
    assert audit is not None
    assert audit[0] == "compare_previous_session"
    assert audit[1] == "2"
    assert audit[2] == "repeat-jaccard-v1"
    assert str(audit[3]) == str(q_old)

    # the note reached the explorer's actual request (host data, not
    # model text)
    new_requests = fake_llm.requests()[before:]
    assert any("Стратегия против цикла" in r["last_user"] for r in new_requests)

    # the session made progress: a claim with a current supported head
    claim = await _scalar(
        scratch_url,
        "SELECT id FROM claims WHERE created_in_session = :s",
        {"s": str(out.session_id)},
    )
    assert claim is not None
    head = await _claim_head(scratch_url, claim[0])
    assert head[0] == "current" and head[1] == "supported"
    state = await _scalar(
        scratch_url, "SELECT state FROM questions WHERE id = :q", {"q": str(q_rephrased)}
    )
    assert state[0] == QuestionState.VERIFIED.value


# ── test 3: counterevidence in a later session disputes the claim ─────


async def test_later_counterevidence_disputes_claim(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Session 1 supports a claim (E2, supported). Session 2 finds
    counterevidence for the SAME claim (re-proposed → deduped → the
    counter attaches to the existing claim): the rules engine — the
    only grade producer — re-assesses it as disputed, capped at E1.
    The grade never comes from the model or the curator."""
    scratch_url, _engine = migrated_db
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    await _seed_question(scratch_url, "Чему равно значение параметра X?")
    await _seed_question(scratch_url, "Перепроверьте значение параметра X.")

    out1 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("42")},
            {"content": COMPLETE},
            {"content": _curator_claim("Значение параметра X равно 42", {"param": "X"})},
        ],
    )
    assert out1.final_state is SessionState.SUCCEEDED
    claim = await _scalar(
        scratch_url,
        "SELECT id FROM claims WHERE created_in_session = :s",
        {"s": str(out1.session_id)},
    )
    assert claim is not None
    head = await _claim_head(scratch_url, claim[0])
    assert head[0] == "current" and head[1] == "supported" and head[2] == "E2"

    # the later session: the same claim, the new evidence counters it
    out2 = await _run_session(
        fake_llm,
        scratch_url,
        workspace,
        [
            {"content": _tool_python("43")},
            {"content": COMPLETE},
            {"content": _curator_counter("Значение параметра X равно 42", {"param": "X"})},
        ],
    )
    assert out2.final_state is SessionState.SUCCEEDED

    # dedup: exactly ONE claim in the corpus
    total = await _scalar(scratch_url, "SELECT count(*) FROM claims")
    assert total[0] == 1

    # the counter evidence was attached to the existing claim
    counters = await _scalar(
        scratch_url,
        "SELECT count(*) FROM evidence WHERE claim_id = :c AND relation = 'counters'",
        {"c": str(claim[0])},
    )
    assert counters[0] == 1

    # the rules engine re-assessed: disputed, capped at E1 (§3.7)
    head = await _claim_head(scratch_url, claim[0])
    assert head[0] == "current" and head[1] == "disputed" and head[2] == "E1"
    # the re-assessment audit of the SECOND session carries the
    # rules-engine reasons (audit sequence is per-session, so scope
    # the query to that session)
    reasons = await _scalar(
        scratch_url,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = :t AND session_id = :s AND payload->>'claim_id' = :c",
        {
            "t": AuditEventType.CLAIM_ASSESSED.value,
            "s": str(out2.session_id),
            "c": str(claim[0]),
        },
    )
    assert reasons is not None
    assert "counterevidence_unresolved" in str(reasons[0])
