"""Scenario (DB): writer admission (§5.9.1, §14.1, T4.4).

Covers: the knowledge_write_gate CAS protocol (NOWAIT acquire, conflict,
expired-lease takeover, release), the session commit intent (registered
under a live lease, cleared by the terminal paths, refused without a
lease), the worker yielding (gate conflict → jitter deferral, intent at
admission and mid-batch → jobs back to the queue with the attempt
restored), and the scheduler admission gates T_escalate /
T_worker_admission (the queue gets the window between sessions).
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.orchestrator.scheduler import (
    REASON_REASSESSMENT_BACKLOG,
    ReassessmentAdmission,
    ReassessmentAdmissionError,
)
from packages.memory.reassessment import (
    ACTOR,
    run_reassessment_batch,
)
from packages.memory.writer_gate import (
    GATE_PRIORITY_ACTIVATION,
    OWNER_ACTIVATION,
    OWNER_WORKER,
    acquire_writer_gate,
    active_session_intent,
    clear_session_intent,
    register_session_intent,
    release_writer_gate,
    writer_gate_holder,
)
from tests.scenario.test_reassessment import (
    _factory,
    _job_state,
    _run,
    _scalar,
    _seed_claim,
    _seed_evidence,
    _seed_job,
    _set_activating,
)
from tests.scenario.test_wake_scheduler import _sched, _seed_commit_attempt, _seed_session

pytestmark = [pytest.mark.scenario]


def _u(tag: str) -> uuid.UUID:
    return uuid.UUID(int=int(tag, 16))


# ─── gate protocol (pure CAS) ──────────────────────────────────────────────


async def _holder(db) -> Any:
    return await writer_gate_holder(db)


@pytest.mark.asyncio
async def test_gate_acquire_release_takeover(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        async with factory() as db, db.begin():
            assert await _holder(db) is None
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_ACTIVATION, owner_id="test",
                priority=GATE_PRIORITY_ACTIVATION, lease_seconds=600,
            )
            h = await _holder(db)
            assert h is not None
            assert (h.owner_kind, h.priority) == (OWNER_ACTIVATION, 2)
            # re-entrant for the same owner: True
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_ACTIVATION, owner_id="test",
                priority=GATE_PRIORITY_ACTIVATION, lease_seconds=600,
            )
            # a foreign owner conflicts (NOWAIT): False, holder unchanged
            assert not await acquire_writer_gate(
                db, owner_kind=OWNER_WORKER, owner_id=ACTOR, priority=0, lease_seconds=600,
            )
            h = await _holder(db)
            assert h is not None and h.owner_kind == OWNER_ACTIVATION
            # release only by the holder
            await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id=ACTOR)
            assert await _holder(db) is not None
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id="test")
            assert await _holder(db) is None
        # expired lease is taken over (crash window closed)
        async with factory() as db, db.begin():
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_ACTIVATION, owner_id="crashed",
                priority=GATE_PRIORITY_ACTIVATION, lease_seconds=600,
            )
            await db.execute(
                text(
                    "UPDATE knowledge_write_gate SET "
                    "lease_expires_at = now() - interval '1 second'"
                )
            )
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_WORKER, owner_id=ACTOR, priority=0, lease_seconds=600,
            )
            h = await _holder(db)
            assert h is not None and h.owner_kind == OWNER_WORKER
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_gate_check_fields_move_together(migrated_db: tuple[str, AsyncEngine]) -> None:
    """The §14.1 row CHECKs: the holder fields move as one (the CAS
    UPDATE always writes all of them)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    # separate transactions: a CHECK violation aborts the tx
    async with factory() as db, db.begin():
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "UPDATE knowledge_write_gate SET owner_kind = :k "
                    "WHERE scope = 'global'"
                ),
                {"k": "worker"},
            )
    async with factory() as db, db.begin():
        with pytest.raises(IntegrityError):
            await db.execute(
                text(
                    "UPDATE knowledge_write_gate SET lease_expires_at = now() "
                    "WHERE scope = 'global'"
                )
            )
    await engine.dispose()


# ─── session commit intent ─────────────────────────────────────────────────


async def _seed_committing_session(
    engine: AsyncEngine, *, live_lease: bool = True
) -> uuid.UUID:
    factory = await _factory(engine)
    async with factory() as db, db.begin():
        sid = await _seed_session(db, state="committing")
    if live_lease:
        await _scalar(
            engine,
            "UPDATE sessions SET lease_owner = 'test-owner', "
            "lease_expires_at = now() + interval '1 hour' WHERE id = :s",
            {"s": sid},
        )
    return sid


@pytest.mark.asyncio
async def test_session_intent_requires_live_lease(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        sid_dead = await _seed_committing_session(engine, live_lease=False)
        sid_live = await _seed_committing_session(engine, live_lease=True)
        async with factory() as db, db.begin():
            # dead lease: refused, no intent written
            assert not await register_session_intent(db, sid_dead)
            assert not await active_session_intent(db)
            # live lease: registered
            assert await register_session_intent(db, sid_live)
            assert await active_session_intent(db)
            # the intent is stamped (rowcount proved the UPDATE hit the
            # row; the value is verified after commit below)
            # idempotent: the intent is not re-stamped
            assert not await register_session_intent(db, sid_live)
        async with factory() as db:
            row = (
                await db.execute(
                    text("SELECT commit_intent_at FROM sessions WHERE id = :s"),
                    {"s": sid_live},
                )
            ).first()
            assert row is not None and row[0] is not None
            # cleared in the terminal transaction (rule 5)
            await clear_session_intent(db, sid_live)
            assert not await active_session_intent(db)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reconciliation_clears_stale_intent(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Rule 5: recovery clears the stale intent only after the
    lease/attempt fencing (no live lease, prepared attempt → aborted)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    from packages.domain.services.audit import AuditService
    from packages.domain.services.reconciler import reconcile_commit

    try:
        sid = await _seed_committing_session(engine, live_lease=True)
        await _scalar(
            engine,
            "UPDATE sessions SET commit_intent_at = now() WHERE id = :s",
            {"s": sid},
        )
        async with factory() as db, db.begin():
            await _seed_commit_attempt(db, sid, status="prepared")
            await db.execute(
                text(
                    "UPDATE sessions SET commit_attempt_id = "
                    "(SELECT id FROM commit_attempts WHERE session_id = :s) "
                    "WHERE id = :s"
                ),
                {"s": sid},
            )
        # the lease dies (crash); the reconciler fences and aborts
        await _scalar(
            engine,
            "UPDATE sessions SET lease_owner = NULL, lease_expires_at = NULL WHERE id = :s",
            {"s": sid},
        )
        async with factory() as db, db.begin():
            result = await reconcile_commit(db, AuditService(db), sid, original_owner="test-owner")
        assert result.outcome == "aborted"
        row = await _scalar(
            engine,
            "SELECT state, commit_intent_at FROM sessions WHERE id = :s",
            {"s": sid},
        )
        assert row[0] == "failed" and row[1] is None
    finally:
        await engine.dispose()


# ─── worker yields ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_worker_defers_on_gate_conflict_with_jitter(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Rule 3: a NOWAIT conflict defers the due jobs with jitter (5..15 s)
    and leases nothing."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        claims = []
        for i in range(3):
            c = _u(f"8{i}")
            await _seed_claim(engine, c, f"гейт {i}")
            await _seed_evidence(engine, c)
            claims.append((c, await _seed_job(engine, c)))
        async with factory() as db, db.begin():
            # activation holds the gate (the exclusive case)
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_ACTIVATION, owner_id="test",
                priority=GATE_PRIORITY_ACTIVATION, lease_seconds=600,
            )
        async with factory() as db:
            out = await run_reassessment_batch(db)
        assert out.deferred and out.processed == 0 and out.completed == 0
        for _c, j in claims:
            st = await _job_state(engine, j)
            assert st["status"] == "queued" and st["attempts"] == 0
            # jitter deferral: 5..15 s in the future
            assert st["next_attempt_at"] is not None
        # once the gate is free, the batch runs normally (after the
        # deferral window — simulate the clock by clearing the deferral)
        for _c, j in claims:
            await _scalar(
                engine,
                "UPDATE reassessment_jobs SET next_attempt_at = NULL WHERE id = :j",
                {"j": j},
            )
        async with factory() as db, db.begin():
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id="test")
        out = await _run(engine)
        assert out.completed == 3 and out.deferred is False
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_yields_to_session_intent_at_admission(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Rule 4 (admission): a live session intent — no batch, no lease,
    jobs untouched."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        c = _u("81")
        await _seed_claim(engine, c, "интент на входе")
        await _seed_evidence(engine, c)
        j = await _seed_job(engine, c)
        sid = await _seed_committing_session(engine)
        async with factory() as db, db.begin():
            assert await register_session_intent(db, sid)
        async with factory() as db:
            out = await run_reassessment_batch(db)
        assert out.deferred and out.processed == 0
        st = await _job_state(engine, j)
        assert st["status"] == "queued" and st["attempts"] == 0
        assert st["next_attempt_at"] is None  # not even jittered
        # the gate is not left held
        async with factory() as db:
            assert await writer_gate_holder(db) is None
        # without the intent the batch runs
        async with factory() as db, db.begin():
            await clear_session_intent(db, sid)
        out = await _run(engine)
        assert out.completed == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_worker_unleases_when_intent_appears_midbatch(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Rule 4 (mid-batch): a session intent that appears during
    validation sends the remaining job back to the queue with the
    attempt restored; the first job already committed."""
    _, engine = migrated_db
    import packages.memory.reassessment as worker_mod

    factory = await _factory(engine)
    c1, c2 = _u("82"), _u("83")
    await _seed_claim(engine, c1, "первый")
    await _seed_evidence(engine, c1)
    await _seed_claim(engine, c2, "второй")
    await _seed_evidence(engine, c2)
    j1 = await _seed_job(engine, c1)
    j2 = await _seed_job(engine, c2)
    sid = await _seed_committing_session(engine)

    real_process = worker_mod._process_one_job

    async def process_then_intent(db: Any, job_id: Any, *, audit: Any) -> str:
        result = await real_process(db, job_id, audit=audit)
        if job_id == j1:
            # the session registers its intent between the two jobs
            # (side connection: the session row is not locked by the
            # job transaction)
            async with factory() as db2, db2.begin():
                await register_session_intent(db2, sid)
        return result

    worker_mod._process_one_job = process_then_intent
    try:
        async with factory() as db:
            out = await run_reassessment_batch(db)
    finally:
        worker_mod._process_one_job = real_process

    assert out.completed == 1
    st1 = await _job_state(engine, j1)
    st2 = await _job_state(engine, j2)
    assert st1["status"] == "completed"
    assert st2["status"] == "queued" and st2["attempts"] == 0 and st2["lease_owner"] is None
    # the gate was released after the batch
    async with factory() as db:
        assert await writer_gate_holder(db) is None
    await engine.dispose()


@pytest.mark.asyncio
async def test_activation_takes_gate_exclusively_before_pointer(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """T4.5 protocol preview: the activation acquisition takes the gate
    NOWAIT; a worker batch running concurrently cannot start (it defers
    with jitter), and after the pointer flip the worker is not runnable
    at all (the activating slot)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        c = _u("84")
        await _seed_claim(engine, c, "активация")
        await _seed_evidence(engine, c)
        j = await _seed_job(engine, c)
        async with factory() as db, db.begin():
            # the activation acquisition: gate first, THEN the pointer
            assert await acquire_writer_gate(
                db, owner_kind=OWNER_ACTIVATION, owner_id="test",
                priority=GATE_PRIORITY_ACTIVATION, lease_seconds=600,
            )
        # the worker defers on the gate conflict (the pointer is still
        # empty — the deferral is the gate, not the slot)
        out = await _run(engine)
        assert out.deferred
        st = await _job_state(engine, j)
        assert st["status"] == "queued" and st["attempts"] == 0
        # the pointer is installed under the gate (T4.5 proper does this
        # in its fenced tx)
        await _set_activating(engine, on=True)
        await _scalar(
            engine,
            "UPDATE reassessment_jobs SET next_attempt_at = NULL WHERE id = :j",
            {"j": j},
        )
        out = await _run(engine)
        assert out.deferred  # now the activating slot also holds
        async with factory() as db, db.begin():
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id="test")
        await _set_activating(engine, on=False)
    finally:
        await engine.dispose()


# ─── scheduler gates (T_escalate / T_worker_admission) ─────────────────────


async def _seed_backlog_job(engine: AsyncEngine, *, age_hours: float) -> uuid.UUID:
    c = _u("85")
    await _seed_claim(engine, c, "бэклог")
    await _seed_evidence(engine, c)
    j = await _seed_job(engine, c)
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET enqueued_at = now() - "
        "make_interval(secs => :s) WHERE id = :j",
        {"j": j, "s": int(age_hours * 3600)},
    )
    return j


async def _patch_admission(engine: AsyncEngine, patch: dict) -> None:
    await _scalar(
        engine,
        "UPDATE config_snapshots SET reassessment_admission = "
        "reassessment_admission || CAST(:p AS jsonb)",
        {"p": json.dumps(patch, sort_keys=True)},
    )


@pytest.mark.asyncio
async def test_wake_skipped_on_dependency_critical_backlog(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """The oldest runnable job is older than T_escalate (default 7200 s)
    and therefore dependency-critical; its age (10 h) exceeds
    T_worker_admission (7200 s) — the wake is SKIPPED, never queued."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        await _seed_backlog_job(engine, age_hours=10)
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=datetime.now(UTC))
        assert decision.action == "skip"
        assert decision.reason == REASON_REASSESSMENT_BACKLOG
        async with factory() as db:
            audit = (
                await db.execute(
                    text("SELECT payload FROM audit_events WHERE type = 'wake_skipped'")
                )
            ).mappings().all()
        assert len(audit) == 1
        assert audit[0]["payload"]["reason"] == REASON_REASSESSMENT_BACKLOG
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wake_admitted_when_queue_is_fresh(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """A fresh runnable job (younger than T_escalate) is not
    dependency-critical — the wake proceeds."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        c = _u("86")
        await _seed_claim(engine, c, "свежий")
        await _seed_evidence(engine, c)
        await _seed_job(engine, c)
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=datetime.now(UTC))
        assert decision.action == "wake"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_escalated_but_below_admission_threshold_wakes(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """A job escalated past T_escalate (3600 s, patched) but younger than
    T_worker_admission (72000 s, patched) does NOT block the wake:
    the thresholds are independent (the escalation marks the job
    dependency-critical; the admission threshold stops the wake)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        await _patch_admission(
            engine,
            {"t_escalate_seconds": 3600, "t_worker_admission_seconds": 72000},
        )
        await _seed_backlog_job(engine, age_hours=2)  # 7200 s: escalated, not over
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=datetime.now(UTC))
        assert decision.action == "wake"
        # the same job once over the admission threshold blocks
        await _patch_admission(engine, {"t_worker_admission_seconds": 3600})
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=datetime.now(UTC))
        assert decision.action == "skip"
        assert decision.reason == REASON_REASSESSMENT_BACKLOG
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_blocked_job_does_not_block_wake(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """A blocked job (any age) is not runnable and never blocks the wake
    (§5.9.1: blocked не участвует в admission)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        j = await _seed_backlog_job(engine, age_hours=48)
        await _scalar(
            engine,
            "UPDATE reassessment_jobs SET status = 'blocked', blocked_at = now(), "
            "lease_owner = NULL, lease_expires_at = NULL WHERE id = :j",
            {"j": j},
        )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=datetime.now(UTC))
        assert decision.action == "wake"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_reassessment_admission_validation() -> None:
    """The section is validated fail-closed (like wake_schedule)."""
    good = ReassessmentAdmission.from_payload(
        {"t_escalate_seconds": 7200, "t_worker_admission_seconds": 7200, "queue_slo_seconds": 1}
    )
    assert good.t_escalate_seconds == 7200
    for bad in (
        None,
        "x",
        {"t_escalate_seconds": 0, "t_worker_admission_seconds": 1, "queue_slo_seconds": 1},
        {"t_escalate_seconds": 1, "queue_slo_seconds": 1},
        {"t_escalate_seconds": True, "t_worker_admission_seconds": 1, "queue_slo_seconds": 1},
        {"t_escalate_seconds": 1.5, "t_worker_admission_seconds": 1, "queue_slo_seconds": 1},
    ):
        with pytest.raises(ReassessmentAdmissionError):
            ReassessmentAdmission.from_payload(bad)


@pytest.mark.asyncio
async def test_scheduler_status_reports_queue_metrics(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """The depth/age metrics + the SLO flag (operator view, §5.9.1)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        async with factory() as db:
            status = await _sched(db, tmp_path).status()
        q = status["reassessment_queue"]
        assert q["oldest_runnable_age_seconds"] is None and q["slo_breached"] is False
        assert q["queue_slo_seconds"] == 172800
        await _seed_backlog_job(engine, age_hours=48)
        async with factory() as db:
            status = await _sched(db, tmp_path).status()
        q = status["reassessment_queue"]
        assert q["oldest_runnable_age_seconds"] is not None
        assert q["oldest_runnable_age_seconds"] > 47 * 3600
        assert q["slo_breached"] is True  # 48 h > 48 h SLO
    finally:
        await engine.dispose()
