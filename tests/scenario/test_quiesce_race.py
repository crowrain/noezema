"""Scenario (DB): the EVAL-3d quiesce race and its barrier (T7.20,
§8.7.2, ADR-0009, docs/eval/EVAL-3-freeze.md §10.5).

The race: a session's phase-1 transaction is long-lived — the ``sessions``
row (and its lease) is uncommitted until the session reaches COMMITTING,
so the activation's "no active sessions" check at the flip moment misses
an in-flight session. In EVAL-3d (run faa3cded, session 6f45deea) the
flip committed while the session's phase-1 tx was open; the session then
committed a claim (8bbbb06a) with a head only on the superseded
snapshot — it silently dropped out of current knowledge and the gates.

The barrier (migration 0022): a committed ``session_admissions`` record
written before the phase-1 transaction, released by a DB trigger on every
terminal session state (or swept when its lease expires for a crashed
session). The activation's quiesce check counts live records as active
sessions. The backstop: a commit whose pinned snapshot is superseded at
the fenced boundary carries every claim it created over to the active
snapshot (pending head + durable reassessment job — the cohort
mechanism), so the invariant "no claim with a head only on a superseded
snapshot" holds under NO interleaving.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

import packages.memory.activation as act
from packages.domain.models.commit import ORMCommitAttempt
from packages.domain.models.enums import SessionState
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.domain.services.commit import finalize as commit_finalize
from packages.domain.services.commit import prepare as commit_prepare
from packages.domain.services.reserve import HostReserveService, ReserveLimits
from packages.domain.services.staging import StagingService
from packages.memory.session_admission import register_session_admission
from tests.scenario.test_online_activation import (
    _audit,
    _bootstrap,
    _factory,
    _head,
    _jobs_for,
    _pointer,
    _rules_payload,
    _run_online,
    _scalar,
    _seed_current_claims,
    _slot,
)

pytestmark = [pytest.mark.scenario]

SNAP = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


async def _admission_count(engine: AsyncEngine) -> int:
    row = await _scalar(engine, "SELECT count(*) FROM session_admissions")
    assert row is not None
    return int(row[0])


async def _seed_committing_session(
    engine: AsyncEngine, sid: uuid.UUID, snapshot: uuid.UUID
) -> None:
    """A committed session row in COMMITTING with a live lease — the state
    a real session reaches when its phase-1 transaction commits."""
    await _scalar(
        engine,
        "INSERT INTO sessions (id, state, config_snapshot_id, lease_owner, "
        "lease_expires_at, started_at) "
        "VALUES (:id, 'committing', :c, 'node-test', clock_timestamp() + interval '10 minutes', now())",
        {"id": sid, "c": snapshot},
    )


async def _old_code_claim(
    engine: AsyncEngine, claim_id: uuid.UUID, superseded: uuid.UUID
) -> None:
    """What the OLD code's session commit produced (EVAL-3d, claim
    8bbbb06a): a claim with a head only on the superseded snapshot."""
    aid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO claims (id, statement, claim_type, freshness_status) "
        "VALUES (:id, 'старый-код claim (гонка quiesce)', 'computed_result', 'fresh')",
        {"id": claim_id},
    )
    await _scalar(
        engine,
        "INSERT INTO claim_assessments "
        "(id, claim_id, effective_grade, epistemic_status, rules_version, "
        " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
        "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', '{}', 0.5, true)",
        {"a": aid, "c": claim_id},
    )
    await _scalar(
        engine,
        "INSERT INTO claim_assessment_heads "
        "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
        " epistemic_status, prepared_by) "
        "VALUES (:c, :s, 'current', :a, 'supported', 'session')",
        {"c": claim_id, "s": superseded, "a": aid},
    )


# ─── 1. the primary barrier: an in-flight session blocks the flip ─────────


@pytest.mark.asyncio
async def test_inflight_session_admission_blocks_activation(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Deterministic repro of the EVAL-3d interleaving: the session is
    admitted (record committed) and its phase-1 tx is open (session row
    uncommitted) when the flip runs. Old code: the flip would pass the
    quiesce check (the row is invisible) and leave the later commit with
    a superseded-only head. New code: the live admission record blocks
    the drain (T7.26: the activation waits for the window, hits the
    bounded drain timeout, cancels the intent and fails — the flip does
    NOT happen while the session is live); after the terminal state the
    trigger releases the record and the activation proceeds unchanged.
    ``drain_wait_seconds`` is pinned small so the bounded wait is a
    fraction of a second (the meaning — the barrier blocks the flip —
    is unchanged)."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["81"])
        bootstrap = await _bootstrap(engine)
        factory = await _factory(engine)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        sid = uuid.uuid4()
        # the orchestrator's pre-phase-1 short tx (committed — visible)
        async with factory() as db, db.begin():
            await register_session_admission(
                db,
                sid,
                node_owner="node-test",
                config_snapshot_id=bootstrap,
                phase_deadline_seconds=600,
            )
        assert await _admission_count(engine) == 1

        # phase-1 tx: the session row is UNCOMMITTED (the visibility gap)
        async with factory() as t1:
            await t1.begin()
            await t1.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id) "
                    "VALUES (:id, 'exploring', :c)"
                ),
                {"id": sid, "c": bootstrap},
            )
            # the flip must be blocked while the row is invisible (the
            # live admission record holds the drain; bounded wait)
            with pytest.raises(act.ActivationError, match="active sessions present"):
                await _run_online(
                    engine, payload, drain_wait_seconds=1, drain_poll_seconds=0.1
                )
            assert (await _slot(engine))["activating"] is None
            assert await _pointer(engine) == bootstrap

            # the phase-1 tx commits — the row is visible now, and the
            # barrier must STILL hold (the existing check sees 'committing')
            await t1.execute(
                text(
                    "UPDATE sessions SET state = 'committing', "
                    "lease_owner = 'node-test', "
                    "lease_expires_at = clock_timestamp() + interval '10 minutes' "
                    "WHERE id = :id"
                ),
                {"id": sid},
            )
            await t1.commit()

        with pytest.raises(act.ActivationError, match="active sessions present"):
            await _run_online(engine, payload, drain_wait_seconds=1, drain_poll_seconds=0.1)
        assert await _admission_count(engine) == 1  # the record is still live

        # the session ends: the terminal state (one tx) releases the
        # admission record via the DB trigger — the activation proceeds
        async with factory() as db, db.begin():
            await db.execute(
                text("UPDATE sessions SET state = 'succeeded', finished_at = now() WHERE id = :id"),
                {"id": sid},
            )
        assert await _admission_count(engine) == 0  # trigger released it

        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert await _pointer(engine) != bootstrap
    finally:
        await engine.dispose()


# ─── 2. a crashed session's expired record is swept, not a wall ──────────


@pytest.mark.asyncio
async def test_expired_admission_is_swept_by_activation(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """A crashed session (no session row at all — the phase-1 tx rolled
    back) leaves an expired admission record. The activation sweeps it at
    the quiesce check instead of blocking forever (the record's lease is
    dead ⇒ the session cannot commit knowledge — its own shorter lease is
    dead too)."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["82"])
        bootstrap = await _bootstrap(engine)

        sid = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO session_admissions "
            "(session_id, node_owner, config_snapshot_id, lease_expires_at) "
            "VALUES (:id, 'node-dead', :c, now() - interval '1 hour')",
            {"id": sid, "c": bootstrap},
        )

        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert await _admission_count(engine) == 0  # swept

        events = await _audit(engine, "activation_acquired")
        assert events and events[-1]["swept_admissions"] == 1
    finally:
        await engine.dispose()


# ─── 3. the backstop: commit after the flip carries claims forward ──────


@pytest.mark.asyncio
async def test_commit_after_flip_carries_claims_to_active_snapshot(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """The residual interleaving the admission barrier cannot cover
    deterministically (record lost/expired while the session can still
    commit) — the old code's exact EVAL-3d state, then the new code's
    commit: the claim created under the superseded snapshot is carried
    over to the active one (pending head + durable job). The invariant —
    no claim with a head only on a superseded snapshot — holds either
    way it is reached."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["83"])
        bootstrap = await _bootstrap(engine)
        factory = await _factory(engine)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        sid = uuid.uuid4()
        # T1: the in-flight phase-1 tx. NO admission record — the
        # visibility state of the old code (record lost or expired).
        async with factory() as t1:
            await t1.begin()
            await t1.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, lease_owner, "
                    "lease_expires_at, started_at) "
                    "VALUES (:id, 'exploring', :c, 'node-test', "
                    "clock_timestamp() + interval '10 minutes', now())"
                ),
                {"id": sid, "c": bootstrap},
            )
            # the flip passes (the session is invisible) — EVAL-3d moment
            res = await _run_online(engine, payload)
            assert res.state == "active"
            active_ptr = res.candidate_id
            assert await _pointer(engine) == active_ptr

            # the phase-1 tx commits: the session reaches COMMITTING
            await t1.execute(
                text(
                    "UPDATE sessions SET state = 'committing' WHERE id = :id"
                ),
                {"id": sid},
            )
            await t1.commit()

        # phase 2: the durable prepared attempt (reads the post-flip
        # knowledge revision — the revision fence passes, which is why
        # the old code committed the superseded-only claim)
        async with factory() as db, db.begin():
            session = await db.get(ORMSession, sid)
            assert session is not None
            attempt = await commit_prepare(db, AuditService(db), session, None, "node-test")
        attempt_id = attempt.id

        # the OLD code's result for a sibling commit: a claim with a head
        # only on the superseded snapshot (claim 8bbbb06a, verbatim)
        old_cid = uuid.UUID(int=0x8BBB06A)
        await _old_code_claim(engine, old_cid, bootstrap)
        heads_old = await _scalar(
            engine,
            "SELECT string_agg(config_snapshot_id::text, ',') "
            "FROM claim_assessment_heads WHERE claim_id = :c",
            {"c": old_cid},
        )
        assert heads_old is not None and str(heads_old[0]) == str(bootstrap)

        # phase 3: the fenced final transaction (the NEW code). The fake
        # memory apply creates one claim + a current head under the
        # session's (superseded) snapshot — what apply_claim_staging does
        # in production.
        async def fake_apply_memory(
            db: AsyncSession, audit: AuditService, session: ORMSession
        ) -> dict[str, Any]:
            cid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claims (id, statement, claim_type, freshness_status, "
                    "created_in_session) "
                    "VALUES (:id, 'claim из сессии, зафиксировавшей flip', "
                    "'computed_result', 'fresh', :sid)"
                ),
                {"id": cid, "sid": session.id},
            )
            aid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', '{}', 0.5, true)"
                ),
                {"a": aid, "c": cid},
            )
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, "
                    "current_assessment_id, epistemic_status, prepared_by) "
                    "VALUES (:c, :s, 'current', :a, 'supported', 'session')"
                ),
                {"c": cid, "s": session.config_snapshot_id, "a": aid},
            )
            return {
                "claims_created": 1,
                "claims_reused": 0,
                "evidence_added": 0,
                "evidence_deduped": 0,
                "assessments": 1,
                "dependencies_added": 0,
                "dependencies_evidential_added": 0,
                "dependencies_rejected": [],
                "problems": [],
            }

        async with factory() as db, db.begin():
            session = await db.get(ORMSession, sid)
            attempt_row = await db.get(ORMCommitAttempt, attempt_id)
            assert session is not None and attempt_row is not None
            staging = StagingService(
                HostReserveService(ReserveLimits(10, 10, 10, 5))
            )
            result = await commit_finalize(
                db,
                AuditService(db),
                session,
                attempt_row,
                "node-test",
                staging,
                terminal=SessionState.SUCCEEDED,
                steps=1,
                evidence_count=0,
                claims=1,
                questions_created=0,
                termination_reason=None,
                apply_memory=fake_apply_memory,
            )
        assert result.outcome == "committed"

        # the session's own snapshot keeps the memory-apply head...
        # (find the carried claim via the audit event)
        drift = await _audit(engine, "commit_snapshot_drift")
        assert len(drift) == 1
        assert uuid.UUID(drift[0]["session_snapshot"]) == uuid.UUID(str(bootstrap))
        assert uuid.UUID(drift[0]["active_snapshot"]) == uuid.UUID(str(active_ptr))
        assert len(drift[0]["carried_claims"]) == 1
        cid = uuid.UUID(drift[0]["carried_claims"][0])

        head_old = await _head(engine, cid, snap=f"'{bootstrap}'")
        assert head_old is not None and head_old["state"] == "current"
        # ...and the active snapshot got the cohort-style carry-over:
        # PENDING head (NULL/NULL — no current knowledge until the worker
        # reassesses) + the durable job
        head_new = await _head(engine, cid, snap=f"'{active_ptr}'")
        assert head_new is not None
        assert head_new["state"] == "pending"
        assert head_new["assessment_id"] is None
        assert head_new["status"] is None
        assert head_new["prepared_by"] == "commit_carryover"
        jobs = await _jobs_for(engine, active_ptr)
        assert [
            (str(j[0]), j[1], j[2]) for j in jobs if str(j[0]) == str(cid)
        ] == [(str(cid), "queued", "commit_carryover")]

        # the invariant: NO claim created by the NEW code's commit has a
        # head only on a superseded snapshot (the old-code claim above is
        # the documented exception — it is what the old code left behind)
        orphaned = await _scalar(
            engine,
            "SELECT count(*) FROM claims c WHERE c.id <> :excluded AND "
            "EXISTS (SELECT 1 FROM claim_assessment_heads h "
            "       WHERE h.claim_id = c.id AND h.config_snapshot_id = :old) "
            "AND NOT EXISTS (SELECT 1 FROM claim_assessment_heads h2 "
            "       WHERE h2.claim_id = c.id AND h2.config_snapshot_id = " + SNAP + ")",
            {"old": bootstrap, "excluded": old_cid},
        )
        assert orphaned is not None and int(orphaned[0]) == 0
        # the terminal state committed (and would have released any live
        # admission record via the trigger)
        state = await _scalar(
            engine, "SELECT state FROM sessions WHERE id = :id", {"id": sid}
        )
        assert state is not None and state[0] == "succeeded"
    finally:
        await engine.dispose()


# ─── 4. the trigger: every terminal state releases the record ────────────


@pytest.mark.asyncio
async def test_terminal_state_releases_admission_trigger(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """The release is a DB trigger on ``sessions`` (migration 0022), so
    no code path can miss it: every terminal state deletes the record in
    the same transaction; a non-terminal transition does not."""
    _, engine = migrated_db
    try:
        bootstrap = await _bootstrap(engine)
        for terminal in ("succeeded", "succeeded_partial", "failed", "cancelled"):
            sid = uuid.uuid4()
            async with (await _factory(engine))() as db, db.begin():
                await register_session_admission(
                    db,
                    sid,
                    node_owner="node-test",
                    config_snapshot_id=bootstrap,
                    phase_deadline_seconds=600,
                )
                await db.execute(
                    text(
                        "INSERT INTO sessions (id, state, config_snapshot_id) "
                        "VALUES (:id, 'committing', :c)"
                    ),
                    {"id": sid, "c": bootstrap},
                )
            row = await _scalar(
                engine,
                "SELECT 1 FROM session_admissions WHERE session_id = :id",
                {"id": sid},
            )
            assert row is not None
            await _scalar(
                engine,
                "UPDATE sessions SET state = :st, finished_at = now() WHERE id = :id",
                {"st": terminal, "id": sid},
            )
            row = await _scalar(
                engine,
                "SELECT 1 FROM session_admissions WHERE session_id = :id",
                {"id": sid},
            )
            assert row is None, f"admission record survived terminal state {terminal}"

        # a non-terminal transition keeps the record (the session is still
        # able to commit knowledge)
        sid = uuid.uuid4()
        async with (await _factory(engine))() as db, db.begin():
            await register_session_admission(
                db,
                sid,
                node_owner="node-test",
                config_snapshot_id=bootstrap,
                phase_deadline_seconds=600,
            )
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id) "
                    "VALUES (:id, 'exploring', :c)"
                ),
                {"id": sid, "c": bootstrap},
            )
        await _scalar(
            engine, "UPDATE sessions SET state = 'committing' WHERE id = :id", {"id": sid}
        )
        row = await _scalar(
            engine, "SELECT 1 FROM session_admissions WHERE session_id = :id", {"id": sid}
        )
        assert row is not None
    finally:
        await engine.dispose()


# ─── 5. regression: an activation without the race is unchanged ──────────


@pytest.mark.asyncio
async def test_normal_activation_without_race_unchanged(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """No sessions, no admission records: the quiesce check (sweep +
    count included) must not change the activation's behavior at all."""
    _, engine = migrated_db
    try:
        cids = await _seed_current_claims(engine, ["84"], with_evidence=True)
        assert await _admission_count(engine) == 0

        res = await _run_online(
            engine, _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        )
        assert res.state == "active"
        assert await _pointer(engine) == res.candidate_id
        # the seeded claim got its shadow head under the new snapshot
        head = await _head(engine, cids[0], snap=f"'{res.candidate_id}'")
        assert head is not None
        assert await _admission_count(engine) == 0
        # the sweep ran (0 rows) and was recorded
        events = await _audit(engine, "activation_acquired")
        assert events and events[-1]["swept_admissions"] == 0
    finally:
        await engine.dispose()
