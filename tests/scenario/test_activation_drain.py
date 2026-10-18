"""Scenario (DB): the T7.26 drain protocol (ADR-0013, §8.7.2, EVAL-4d).

The defect (EVAL-4d, run ca5933c4, code 375f759): the quiesce check
("no active sessions") was BEFORE the activating-pointer publish — in
the SAME transaction (``acquire_activation``). So a serial session
series (the eval-run driver starts the next session immediately after
the previous one) never had a zero-session moment: the activation
could never publish the pointer (the check failed first), and the
driver never saw "an activation is in flight" — 401 watchdog attempts,
each "active sessions present: 1", no mid-run flip.

The fix: the durable activation intent (the DRAIN — the fenced slot;
the candidate stays ``draft``) is published FIRST, WITHOUT the quiesce
check. From that moment the scheduler rejects wakes
(``activation_slot_busy``), the worker defers batches, and a session's
admission registration (which takes the head lock) fails closed with
``ActivationInFlightError`` while the slot holds admission. The
activation then WAITS, bounded by ``drain_wait_seconds``, for the
window between sessions (zero active sessions + zero live admission
records — the T7.20 barrier, expired records swept — + zero
unresolved attempts). On timeout the intent is cancelled (slot
cleared, the candidate stays ``draft`` — a drain cancel is NOT a
terminal state) and a clear, retryable error is raised.

The T7.20 invariant is preserved and tightened: the admission
registration and the flip both hold the runtime-head lock, so an
admission is either counted at the flip (the flip is blocked) or
rejected at registration — a live session can never slip past the
flip. A crashed drain (``draft`` + expired lease) does not hold
admission forever: the slot checks are lease-aware for ``draft``
(the analogy of the T7.20 sweep of expired admission records).
"""

from __future__ import annotations

import asyncio
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

import packages.memory.activation as act
from apps.orchestrator.scheduler import REASON_ACTIVATION_SLOT
from packages.domain.services.audit import AuditService
from packages.memory.activation import ActivationInFlightError, activation_slot_busy
from packages.memory.session_admission import register_session_admission
from tests.scenario.test_online_activation import (
    _audit,
    _bootstrap,
    _candidates,
    _factory,
    _pointer,
    _rules_payload,
    _run_online,
    _scalar,
    _seed_current_claims,
    _slot,
    _state_of,
)
from tests.scenario.test_wake_scheduler import _sched

pytestmark = [pytest.mark.scenario]

SNAP = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


async def _admission_count(engine: AsyncEngine) -> int:
    row = await _scalar(engine, "SELECT count(*) FROM session_admissions WHERE lease_expires_at > now()")
    assert row is not None
    return int(row[0])


async def _seed_live_session(
    engine: AsyncEngine, sid: uuid.UUID, snapshot: uuid.UUID, state: str = "exploring"
) -> None:
    """A committed admission record + a VISIBLE session row (the
    orchestrator's pre-phase-1 short tx + a session past the
    visibility gap) — a running session the drain must wait for."""
    factory = await _factory(engine)
    async with factory() as db, db.begin():
        await register_session_admission(
            db,
            sid,
            node_owner="node-test",
            config_snapshot_id=snapshot,
            phase_deadline_seconds=600,
        )
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id, lease_owner, "
                "lease_expires_at, started_at) "
                "VALUES (:id, :s, :c, 'node-test', "
                "clock_timestamp() + interval '10 minutes', now())"
            ),
            {"id": sid, "s": state, "c": snapshot},
        )


async def _terminate_session(engine: AsyncEngine, sid: uuid.UUID, state: str = "succeeded") -> None:
    """One terminal transaction — the trigger releases the admission
    record in the SAME transaction (T7.20)."""
    factory = await _factory(engine)
    async with factory() as db, db.begin():
        await db.execute(
            text("UPDATE sessions SET state = :s, finished_at = now() WHERE id = :id"),
            {"s": state, "id": sid},
        )


async def _wait_slot(engine: AsyncEngine, *, want_set: bool, timeout: float = 15.0) -> dict[str, Any]:
    """Poll the slot until it is set/cleared (the drain is published /
    the terminal cleanup ran)."""
    deadline = time.monotonic() + timeout
    while True:
        slot = await _slot(engine)
        is_set = slot["activating"] is not None
        if is_set == want_set:
            return slot
        if time.monotonic() >= deadline:
            raise AssertionError(f"slot did not become {'set' if want_set else 'clear'}: {slot}")
        await asyncio.sleep(0.05)


# ─── 1. the main scenario: a serial series, the flip lands between sessions ─


@pytest.mark.asyncio
async def test_drain_flips_between_sessions(migrated_db: tuple[str, AsyncEngine], tmp_path: Path) -> None:
    """A serial series with an activation requested in the middle:
    drain, the in-flight session ends, the flip lands BETWEEN sessions,
    the series continues on the new snapshot, no session is lost."""
    _, engine = migrated_db
    try:
        cids = await _seed_current_claims(engine, ["88", "89"], with_evidence=True)
        bootstrap = await _bootstrap(engine)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        # session A of the series: admitted + running (visible)
        sid_a = uuid.uuid4()
        await _seed_live_session(engine, sid_a, bootstrap, state="exploring")

        # the watchdog requests the activation in the middle of the series
        async def _activate() -> act.ActivationResult:
            factory = await _factory(engine)
            async with factory() as db:
                return await act.run_online_change(
                    db,
                    AuditService(db),
                    requested_payload=payload,
                    drain_wait_seconds=30,
                    drain_poll_seconds=0.2,
                )

        task = asyncio.create_task(_activate())
        try:
            # the drain intent is published (slot set) WHILE session A runs
            slot = await _wait_slot(engine, want_set=True)
            assert slot["activating"] is not None
            # the candidate stays DRAFT: slot set + draft is the drain phase
            assert await _state_of(engine, uuid.UUID(str(slot["activating"]))) == "draft"
            # the scheduler rejects new wakes (the driver must wait):
            # while session A is running the first-failing gate is
            # nonterminal_session (checked before the slot); once A ends
            # the same wake hits activation_slot_busy (the slot is still
            # set until the flip's terminal cleanup). Either way: skip.
            factory = await _factory(engine)
            async with factory() as db:
                decision = await _sched(db, tmp_path).decide(
                    source="wake_now", now=datetime.now(UTC)
                )
            assert decision.action == "skip"
            assert decision.reason in (
                "nonterminal_session",
                REASON_ACTIVATION_SLOT,
            )
            # the shared predicate (scheduler / admission gate / worker)
            # says the slot holds admission: draft + live lease
            assert activation_slot_busy(
                state="draft", lease=slot["lease_expires_at"]
            ) is True

            # session A ends (the terminal tx releases its admission record)
            await _terminate_session(engine, sid_a)

            # the drain sees the window → the flip lands → the run completes
            res = await asyncio.wait_for(task, timeout=30)
        finally:
            if not task.done():
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task

        assert res.state == "active"
        assert await _pointer(engine) == res.candidate_id
        # the slot cleared (terminal cleanup) — admission resumes
        assert (await _slot(engine))["activating"] is None
        # the audit trail: drain published → quiesced (acquired) → published
        drain_pub = await _audit(engine, "activation_drain_published")
        assert len(drain_pub) == 1
        assert uuid.UUID(drain_pub[0]["candidate_id"]) == res.candidate_id
        assert await _audit(engine, "activation_acquired")
        assert await _audit(engine, "activation_published")
        assert await _audit(engine, "activation_drain_cancelled") == []

        # the series continues on the NEW snapshot: session B is admitted
        # (the slot is clear) and runs on the new snapshot
        sid_b = uuid.uuid4()
        await _seed_live_session(engine, sid_b, res.candidate_id, state="exploring")
        await _terminate_session(engine, sid_b)

        # no session is lost: both terminal, no live admissions
        fa = await _factory(engine)
        async with fa() as db:
            rows = (
                await db.execute(
                    text("SELECT id, state FROM sessions WHERE id IN (:a, :b)"),
                    {"a": sid_a, "b": sid_b},
                )
            ).all()
        assert {str(r[0]): r[1] for r in rows} == {
            str(sid_a): "succeeded",
            str(sid_b): "succeeded",
        }
        assert await _admission_count(engine) == 0

        # the T7.20 invariant: no claim is left with a head ONLY on the
        # superseded snapshot — every claim has a head on the active one
        missing = await _scalar(
            engine,
            "SELECT count(*) FROM claims c WHERE NOT EXISTS ("
            "SELECT 1 FROM claim_assessment_heads h "
            "WHERE h.claim_id = c.id AND h.config_snapshot_id = " + SNAP + ")",
        )
        assert missing is not None and int(missing[0]) == 0
        # the seeded claims have shadow heads under the new snapshot
        for cid in cids:
            row = await _scalar(
                engine,
                "SELECT assessment_state FROM claim_assessment_heads "
                "WHERE claim_id = :c AND config_snapshot_id = " + SNAP,
                {"c": cid},
            )
            assert row is not None
    finally:
        await engine.dispose()


# ─── 2. drain timeout: the intent is cancelled, the series continues ───────


@pytest.mark.asyncio
async def test_drain_timeout_cancels_intent(migrated_db: tuple[str, AsyncEngine]) -> None:
    """A session that does not end within the drain budget: the intent
    is cancelled (slot cleared, the candidate stays DRAFT — not a
    terminal state), the pointer is untouched, the error is clear and
    retryable; after the session ends the same run succeeds (the series
    had continued on the old snapshot)."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["8a"], with_evidence=True)
        bootstrap = await _bootstrap(engine)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        sid = uuid.uuid4()
        await _seed_live_session(engine, sid, bootstrap, state="exploring")

        with pytest.raises(act.ActivationError, match="active sessions present"):
            await _run_online(
                engine, payload, drain_wait_seconds=1, drain_poll_seconds=0.1
            )

        # the intent was cancelled: the slot is clear, the candidate is
        # still DRAFT (a drain cancel is not a terminal state), the
        # pointer is untouched, the admission record is still live
        assert (await _slot(engine))["activating"] is None
        assert await _pointer(engine) == bootstrap
        cands = await _candidates(engine)
        assert len(cands) == 1 and cands[0][1] == "draft"
        assert await _admission_count(engine) == 1

        # the audit trail: published + cancelled (timeout), never acquired
        drain_pub = await _audit(engine, "activation_drain_published")
        assert len(drain_pub) == 1
        drain_cancel = await _audit(engine, "activation_drain_cancelled")
        assert len(drain_cancel) == 1
        assert drain_cancel[0]["reason"].startswith("drain_wait_timeout")
        assert await _audit(engine, "activation_acquired") == []
        assert await _audit(engine, "activation_published") == []

        # the session ends — the SAME run (the same candidate row) now
        # finds the window and flips (the series had continued on the
        # old snapshot meanwhile)
        await _terminate_session(engine, sid)
        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert await _pointer(engine) == res.candidate_id
    finally:
        await engine.dispose()


# ─── 3. a crashed drain does not hold admission forever ────────────────────


@pytest.mark.asyncio
async def test_crashed_drain_does_not_hold_admission(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """The drain publisher crashes after publishing the intent (slot
    set + candidate draft). While its lease is live, admission stays
    closed; once the lease expires (no renewal — the publisher is
    dead) the slot NO LONGER holds admission (the analogy of the T7.20
    sweep of expired admission records) — a wake is granted; the next
    run takes over (fence bump) and completes."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["8b"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        factory = await _factory(engine)

        # publish the drain (then "crash": the process dies mid-wait)
        async with factory() as db:
            audit = AuditService(db)
            head_row = (
                await db.execute(text("SELECT active_config_snapshot_id FROM runtime_config_heads"))
            ).scalar_one()
            from packages.domain.models.config import ORMConfigSnapshot

            base = await db.get(ORMConfigSnapshot, head_row)
            assert base is not None
            async with act.transaction(db):
                cand, _ = await act.upsert_online_candidate(
                    db, audit, base_snapshot=base, requested_payload=payload
                )
            fence = await act.publish_activation_drain(
                db, audit, candidate=cand, owner=act.ACTOR
            )

        # live lease: the scheduler still blocks the wake
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(
                source="wake_now", now=datetime.now(UTC)
            )
        assert decision.action == "skip"
        assert decision.reason == REASON_ACTIVATION_SLOT

        # the crash leaves no renewal: the drain lease expires
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE runtime_config_heads "
                    "SET activation_lease_expires_at = now() - interval '1 hour' "
                    "WHERE scope = 'global'"
                )
            )
        # the intent no longer holds admission (draft + expired lease)
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(
                source="wake_now", now=datetime.now(UTC)
            )
        assert decision.action == "wake"

        # the next run (the watchdog re-runs) takes over — fence bump —
        # and completes (no session is in flight)
        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert res.candidate_id == cand.id
        assert (await _slot(engine))["fence"] == fence + 1
        assert await _pointer(engine) == res.candidate_id
        # the takeover was recorded
        assert await _audit(engine, "activation_takeover")
    finally:
        await engine.dispose()


# ─── 4. the session admission gate (orchestrator phase 0) ──────────────────


@pytest.mark.asyncio
async def test_session_admission_rejected_while_drain(
    migrated_db: tuple[str, AsyncEngine], fake_llm: Any, tmp_path: Path
) -> None:
    """The orchestrator's phase 0 takes the head lock and rejects the
    admission while the slot holds admission (``ActivationInFlightError``
    — the session did NOT start: no session row, no admission record);
    after the slot clears, the same wake starts a session normally (the
    series continues)."""
    scratch_url, engine = migrated_db
    from tests.scenario.test_orchestrator import (
        COMPLETE,
        CURATOR_OK,
        TOOL_PYTHON,
        TOOL_WRITE,
        _make_orchestrator,
        _seed_question,
    )

    try:
        await _seed_current_claims(engine, ["8c"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        question_id = await _seed_question(scratch_url)
        factory = await _factory(engine)

        async with factory() as db:
            audit = AuditService(db)
            head_row = (
                await db.execute(text("SELECT active_config_snapshot_id FROM runtime_config_heads"))
            ).scalar_one()
            from packages.domain.models.config import ORMConfigSnapshot

            base = await db.get(ORMConfigSnapshot, head_row)
            assert base is not None
            async with act.transaction(db):
                cand, _ = await act.upsert_online_candidate(
                    db, audit, base_snapshot=base, requested_payload=payload
                )
            await act.publish_activation_drain(db, audit, candidate=cand, owner=act.ACTOR)

        orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
        try:
            # phase 0 is rejected under the head lock — no session starts
            with pytest.raises(ActivationInFlightError):
                await orch.run_session(question_id)
            # nothing was created: no session row, no admission record
            assert await _scalar(engine, "SELECT count(*) FROM sessions") is not None
            row = await _scalar(engine, "SELECT count(*) FROM sessions")
            assert row is not None and int(row[0]) == 0
            assert await _admission_count(engine) == 0
        finally:
            await gateway.close()
            await orch_engine.dispose()

        # the slot clears (the drain ended / was cancelled) — the same
        # wake now starts a session normally
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "UPDATE runtime_config_heads "
                    "SET activating_config_snapshot_id = NULL, "
                    "activation_lease_owner = NULL, activation_lease_expires_at = NULL "
                    "WHERE scope = 'global'"
                )
            )
        fake_llm.script(
            [
                {"content": TOOL_PYTHON},
                {"content": TOOL_WRITE},
                {"content": COMPLETE},
                {"content": CURATOR_OK},
            ]
        )
        orch2, gateway2, orch_engine2 = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws2")
        try:
            outcome = await orch2.run_session(question_id)
        finally:
            await gateway2.close()
            await orch_engine2.dispose()
        assert outcome.final_state.value == "succeeded"
        assert await _admission_count(engine) == 0  # the trigger released it
    finally:
        await engine.dispose()
