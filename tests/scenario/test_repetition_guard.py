"""Scenario: repetition protection (§9, T5.4, stage 4).

- a candidate that rephrases an already-investigated question and has
  enough consecutive no-progress sessions on it is a cycle: the host
  picks a strategy from the closed §9 list (deterministic rotation),
  records ``repeat_cycle_detected`` in the audit and either skips the
  question (defer_question → the question is deferred + the next
  candidate is selected) or injects a host-generated strategy note;
- the guard is config-driven: disabled by bootstrap (the MVP FIFO
  selection is unchanged), enabled by an online config change;
- the detector is deterministic (Jaccard over the host word set, no
  LLM call).
"""

from __future__ import annotations

import copy
import uuid
from datetime import UTC, datetime, timedelta
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
TOOL_PYTHON: dict[str, Any] = {
    "public_rationale": "Проверить вычисление",
    "expected_information": "Результат 6*7",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
}
CURATOR_OK: dict[str, Any] = {
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

# same content, different wording — the token overlap (4 of 6 words)
# is above the default rephrase_threshold 0.6 (Jaccard, §5.3.1 family)
ORIGINAL = "Какова масса Луны в килограммах?"
REPHRASE = "Какова масса Луны в килограммах, скажите точно?"
OTHER = "Сколько будет 6*7?"


async def _enable_repetition(engine: Any, **overrides: Any) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    section = copy.deepcopy(BOOTSTRAP_PAYLOAD["repetition"])
    section["enabled"] = True
    section.update(overrides)
    payload["repetition"] = section
    result = await _run_online(engine, payload)
    assert result.state == "active"


async def _seed_question(
    scratch_url: str, text_: str, state: str = "candidate", priority: int = 0
) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            q = await QuestionRepository.create(
                db,
                ORMQuestion(
                    text=text_, origin=QuestionOrigin.SEEDED.value, priority=priority
                ),
            )
            if state != "candidate":
                q.state = state
            return q.id
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


async def _scalar(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).first()
    finally:
        await engine.dispose()


async def _run_session(
    fake: FakeLLM, script: list[dict[str, Any]], tmp_path: Any, scratch_url: str
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
        executor=StubToolExecutor(tmp_path / "ws"),
    )
    try:
        fake.script(script)
        return await orch.run_session(None)
    finally:
        await gateway.close()
        await engine.dispose()


async def test_disabled_by_default_mvp_selection_unchanged(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Bootstrap (enabled=false): a rephrased question is selected as
    usual, no cycle audit, no deferral."""
    scratch_url, _engine = migrated_db
    snapshot_id = await _bootstrap_snapshot_id(scratch_url)
    original_id = await _seed_question(scratch_url, ORIGINAL, state="verified")
    for i in range(3):
        await _seed_no_progress_session(
            scratch_url, original_id, snapshot_id, datetime.now(UTC) - timedelta(hours=10 - i)
        )
    rephrase_id = await _seed_question(scratch_url, REPHRASE)

    outcome = await _run_session(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == rephrase_id
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t",
        {"t": AuditEventType.REPEAT_CYCLE_DETECTED.value},
    )
    assert row[0] == 0


async def test_cycle_note_strategy_injects_context_and_audits(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """no_progress = limit (2) → compare_previous_session (the first
    strategy): the session proceeds with the rephrased question and
    the audit carries the full deterministic explanation."""
    scratch_url, engine = migrated_db
    await _enable_repetition(engine)
    snapshot_id = await _bootstrap_snapshot_id(scratch_url)
    original_id = await _seed_question(scratch_url, ORIGINAL, state="verified")
    now = datetime.now(UTC)
    for i in range(2):
        await _seed_no_progress_session(scratch_url, original_id, snapshot_id, now - timedelta(hours=3 - i))
    rephrase_id = await _seed_question(scratch_url, REPHRASE)

    outcome = await _run_session(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == rephrase_id
    row = await _scalar(
        scratch_url,
        "SELECT payload->>'strategy', payload->>'similar_question_id', "
        "payload->>'no_progress_sessions', payload->>'fingerprint' "
        "FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.REPEAT_CYCLE_DETECTED.value, "s": str(outcome.session_id)},
    )
    assert row is not None
    assert row[0] == "compare_previous_session"
    assert row[1] == str(original_id)
    assert int(row[2]) == 2
    assert row[3] == "repeat-jaccard-v1"
    # the question was not deferred
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(rephrase_id)}))[0]
    assert state == "verified"


async def test_defer_strategy_skips_to_next_candidate(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """no_progress = limit+4 (6) → defer_question: the rephrased
    question is deferred, the session continues with the next
    candidate; the deferral is audited."""
    scratch_url, engine = migrated_db
    await _enable_repetition(engine)
    snapshot_id = await _bootstrap_snapshot_id(scratch_url)
    original_id = await _seed_question(scratch_url, ORIGINAL, state="verified")
    now = datetime.now(UTC)
    for i in range(6):
        await _seed_no_progress_session(scratch_url, original_id, snapshot_id, now - timedelta(hours=8 - i))
    rephrase_id = await _seed_question(scratch_url, REPHRASE)
    other_id = await _seed_question(scratch_url, OTHER, priority=0)

    outcome = await _run_session(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == other_id  # the rephrase was skipped
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(rephrase_id)}))[0]
    assert state == QuestionState.DEFERRED.value
    row = await _scalar(
        scratch_url,
        "SELECT payload->>'strategy' FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.REPEAT_CYCLE_DETECTED.value, "s": str(outcome.session_id)},
    )
    assert row is not None and row[0] == "defer_question"
    deferred = await _scalar(
        scratch_url,
        "SELECT payload->>'question_id' FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.QUESTION_DEFERRED.value, "s": str(outcome.session_id)},
    )
    assert deferred is not None and deferred[0] == str(rephrase_id)


async def test_no_progress_claim_resets_counter(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """A session with a new claim resets the no-progress count: the
    same rephrase after a productive session is NOT a cycle."""
    scratch_url, engine = migrated_db
    await _enable_repetition(engine)
    snapshot_id = await _bootstrap_snapshot_id(scratch_url)
    original_id = await _seed_question(scratch_url, ORIGINAL, state="verified")
    now = datetime.now(UTC)
    # two no-progress sessions, then ONE productive session (a claim
    # created in it resets the count to 0 → below the limit)
    for i in range(2):
        await _seed_no_progress_session(scratch_url, original_id, snapshot_id, now - timedelta(hours=6 - i))
    productive = ORMSession(
        id=uuid.uuid4(),
        state=SessionState.SUCCEEDED.value,
        question_id=original_id,
        config_snapshot_id=snapshot_id,
        started_at=now - timedelta(hours=1),
        finished_at=now,
        termination_reason="goal_reached",
    )
    engine2 = create_async_engine(scratch_url)
    factory2 = async_sessionmaker(engine2, expire_on_commit=False)
    try:
        async with factory2() as db, transaction(db):
            from packages.domain.models.memory import ORMClaim

            db.add(productive)
            await db.flush()
            claim = ORMClaim(
                id=uuid.uuid4(),
                statement="6*7 равно 42",
                claim_type="computed_result",
                freshness_status="unknown",
                created_in_session=productive.id,
            )
            db.add(claim)
    finally:
        await engine2.dispose()
    rephrase_id = await _seed_question(scratch_url, REPHRASE)

    outcome = await _run_session(
        fake_llm,
        [{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}],
        tmp_path,
        scratch_url,
    )
    assert outcome.final_state is SessionState.SUCCEEDED
    assert outcome.question_id == rephrase_id
    row = await _scalar(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND session_id = :s",
        {"t": AuditEventType.REPEAT_CYCLE_DETECTED.value, "s": str(outcome.session_id)},
    )
    assert row[0] == 0
    state = (await _scalar(scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(rephrase_id)}))[0]
    assert state == "verified"


async def test_all_candidates_deferred_is_no_question(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Any
) -> None:
    """Only the rephrased candidate exists and the strategy is a skip
    one: the session ends with no_question (nothing else to select)."""
    scratch_url, engine = migrated_db
    await _enable_repetition(engine)
    snapshot_id = await _bootstrap_snapshot_id(scratch_url)
    original_id = await _seed_question(scratch_url, ORIGINAL, state="verified")
    now = datetime.now(UTC)
    for i in range(6):
        await _seed_no_progress_session(scratch_url, original_id, snapshot_id, now - timedelta(hours=8 - i))
    await _seed_question(scratch_url, REPHRASE)

    outcome = await _run_session(fake_llm, [], tmp_path, scratch_url)
    assert outcome.final_state is SessionState.FAILED
    assert outcome.termination_reason == "no_question"
