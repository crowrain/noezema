"""T7.34 (ADR-0018): the reverify of an existing claim through the
CURATOR boundary (the full session loop, fake LLM).

Two properties:

1. FAIL-CLOSED at the curator boundary: a reverify reference the host
   cannot resolve (unknown id — not in the context pack, and not in the
   corpus at all) rejects the WHOLE proposal before any staging op is
   recorded — a clear audit reason (``reverify_unresolved``), no claim,
   no staging, the session ends in a normal terminal state, the next
   session is admitted;
2. the happy path end-to-end: the anchor session creates the claim,
   the follow-up session proposes the reverify by the host-issued id
   (the id the model read from the ``[c:<uuid>]`` pack line) — the
   commit applies it to the EXISTING claim (no new claim row), the
   reverify record (a fresh session-bound assessment) exists, the
   ``claim_reverified`` audit event is written in the same transaction.

The commit-boundary variants (headless target, ambiguous prefix,
evidence dedup, grade by rules) live in
tests/unit/test_memory_service.py; the pure resolver in
tests/unit/test_claim_reference.py; the gate-5 path (EVAL-4d form) in
tests/scenario/test_evaluation_gates.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text

from tests.conftest import FakeLLM
from tests.scenario.test_commit_boundary import (
    _complete,
    _curator,
    _make_orchestrator,
    _run_next_session,
    _seed_question,
    _session_state,
    _tool,
    _unresolved_attempts,
)

pytestmark = [pytest.mark.scenario]


async def _anchor_claim_id(scratch_url: str, fake: FakeLLM, workspace: Path) -> uuid.UUID:
    """Run session 1 (the anchor): one computed_result claim (E2)."""
    qid = await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake.script(
        [
            _tool("python.execute", {"code": "print(6*7)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake, workspace)
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()
    assert outcome.final_state.value == "succeeded"
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    eng = create_async_engine(scratch_url)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with factory() as db:
            row = (
                (await db.execute(text("SELECT id FROM claims"))).first()
            )
        assert row is not None
        cid = row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    finally:
        await eng.dispose()
    return cid


@pytest.mark.asyncio
async def test_reverify_unknown_reference_rejects_whole_proposal(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """A reverify reference the host cannot resolve (the id exists in no
    corpus) rejects the WHOLE curator proposal — fail-closed: zero
    staging ops, no claim, a clear audit reason (not a silent skip), a
    normal terminal state, and the next session is admitted."""
    scratch_url, _engine = migrated_db
    await _anchor_claim_id(scratch_url, fake_llm, tmp_path / "ws1")

    unknown = uuid.uuid4()
    qid = await _seed_question(scratch_url, "Сколько будет 6*7? Проверь ещё раз.")
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
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws2")
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded"
    assert outcome.claims_proposed == 0

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    eng = create_async_engine(scratch_url)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with factory() as db:
            # no staging op was recorded: the proposal was bounced BEFORE
            # any staging write (the curator boundary, not the commit)
            staging = (
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM session_staging "
                            "WHERE session_id = :s"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .scalar_one()
            )
            assert staging == 0
            # the claim count is unchanged (1 — the anchor's)
            claims = (
                (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
            )
            assert claims == 1
            # the rejection is explainable from the audit: a clear reason
            rejected = (
                (
                    await db.execute(
                        text(
                            "SELECT payload->>'curator_reject_kind', public_summary "
                            "FROM audit_events "
                            "WHERE session_id = :s "
                            "AND payload ? 'curator_rejected' "
                            "AND payload->>'curator_reject_kind' = 'reverify_unresolved'"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .all()
            )
            assert len(rejected) == 1
            assert rejected[0][0] == "reverify_unresolved"
            assert "not visible" in rejected[0][1]
        state, _reason = await _session_state(scratch_url, outcome.session_id)
    finally:
        await eng.dispose()
    assert state == "succeeded"

    assert await _unresolved_attempts(scratch_url) == 0
    # the next session is admitted and runs to a terminal state
    next_state = await _run_next_session(scratch_url, fake_llm, tmp_path / "ws3")
    assert next_state.value == "succeeded"


@pytest.mark.asyncio
async def test_reverify_by_id_end_to_end(
    migrated_db: Any, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """The happy path: the follow-up's curator references the anchor's
    claim by the host-issued id (as read from the [c:<uuid>] pack line).
    The commit applies the reverify to the EXISTING claim — no new claim
    row, the new evidence is attached to the anchor, the reverify record
    (a fresh session-bound assessment) exists, and the
    ``claim_reverified`` audit event names the claim and the session."""
    scratch_url, _engine = migrated_db
    anchor_id = await _anchor_claim_id(scratch_url, fake_llm, tmp_path / "ws1")

    qid = await _seed_question(scratch_url, "Сколько будет 6*7? Проверь ещё раз.")
    fake_llm.script(
        [
            _tool("python.execute", {"code": "print(7*6)"}),
            _complete(),
            _curator(
                [
                    {
                        "statement": "6*7 равно 42 (перепроверено)",
                        "claim_type": "computed_result",
                        "scope": {"expr": "6*7"},
                        "existing_claim_id": str(anchor_id),
                    }
                ],
                [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
            ),
        ]
    )
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws2")
    try:
        outcome = await orch.run_session(qid)
    finally:
        await gateway.close()
        await engine.dispose()

    assert outcome.final_state.value == "succeeded"

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    eng = create_async_engine(scratch_url)
    factory = async_sessionmaker(eng, expire_on_commit=False)
    try:
        async with factory() as db:
            # still exactly ONE claim — the reverify did not create one
            n_claims = (
                (await db.execute(text("SELECT count(*) FROM claims"))).scalar_one()
            )
            assert n_claims == 1
            claim_id = (
                (await db.execute(text("SELECT id FROM claims"))).scalar_one()
            )
            cid = (
                claim_id if isinstance(claim_id, uuid.UUID) else uuid.UUID(str(claim_id))
            )
            assert cid == anchor_id
            # the new evidence is attached to the EXISTING claim (2 rows:
            # the anchor's computation + the reverify's computation —
            # different identity, both kept)
            n_evidence = (
                (
                    await db.execute(
                        text(
                            "SELECT count(*) FROM evidence WHERE claim_id = :c"
                        ),
                        {"c": str(anchor_id)},
                    )
                )
                .scalar_one()
            )
            assert n_evidence == 2
            # the reverify record: a second assessment bound to the
            # follow-up session (the verification moment)
            assessments = (
                (
                    await db.execute(
                        text(
                            "SELECT created_in_session FROM claim_assessments "
                            "WHERE claim_id = :c ORDER BY created_at"
                        ),
                        {"c": str(anchor_id)},
                    )
                )
                .scalars()
                .all()
            )
            assert len(assessments) == 2
            assert str(assessments[1]) == str(outcome.session_id)
            # the direct audit event (the session_id column is the
            # follow-up session): the claim, and the reference the model
            # sent, resolved to the same id
            rev = (
                (
                    await db.execute(
                        text(
                            "SELECT payload->>'claim_id', payload->>'reference', "
                            "payload->>'resolved' FROM audit_events "
                            "WHERE type = 'claim_reverified' "
                            "AND session_id = :s"
                        ),
                        {"s": str(outcome.session_id)},
                    )
                )
                .first()
            )
            assert rev is not None
            assert rev[0] == str(anchor_id).lower()
            assert rev[1] == str(anchor_id)
            assert rev[2] == str(anchor_id).lower()
        state, _reason = await _session_state(scratch_url, outcome.session_id)
    finally:
        await eng.dispose()
    assert state == "succeeded"
    assert await _unresolved_attempts(scratch_url) == 0
