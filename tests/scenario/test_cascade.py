"""Scenario (DB): cascade invalidation over the evidential DAG (T4.2, §8.6).

Covers: pure closure walk, inline cascade (root + closure + jobs +
UUIDv5 questions + knowledge bump), idempotent replay, the barrier
lifecycle (large closure: active → batches → closing → resolved),
crash-resume from the durable cursor, graph-change → new generation,
blocked on manifest tamper, and the retrieval ancestor check.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.models.enums import BarrierStatus
from packages.memory.cascade import (
    BATCH_SIZE,
    BarrierBlockedError,
    CascadeError,
    compute_reverse_closure,
    process_barrier,
    protected_claim_ids,
    start_cascade,
)

pytestmark = [pytest.mark.scenario]

SNAP_SUBQUERY = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
BOOTSTRAP_SNAP = SNAP_SUBQUERY


def _u(tag: str) -> uuid.UUID:
    """Deterministic test UUIDs (same pattern as T4.1 tests)."""
    return uuid.UUID(int=int(tag, 16))


async def _seed_claim(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    statement: str,
    *,
    state: str = "current",
) -> None:
    """One claim + assessment + effective head (test plumbing: the
    cascade protocol only needs head rows, not real evidence)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, 'computed_result', 'fresh')"
            ),
            {"id": claim_id, "s": statement},
        )
        if state == "current":
            aid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', '{}', 0.5, true)"
                ),
                {"a": aid, "c": claim_id},
            )
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {SNAP_SUBQUERY}, 'current', :a, 'supported', 'rules_activation')"
                ),
                {"c": claim_id, "a": aid},
            )
        else:
            await db.execute(
                text(
                    "INSERT INTO claim_assessment_heads "
                    "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                    " epistemic_status, prepared_by) "
                    f"VALUES (:c, {SNAP_SUBQUERY}, :st, NULL, NULL, 'rules_activation')"
                ),
                {"c": claim_id, "st": state},
            )


async def _seed_edge(engine: AsyncEngine, frm: uuid.UUID, to: uuid.UUID, kind: str = "evidential") -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claim_dependencies (id, from_claim_id, to_claim_id, kind) "
                "VALUES (:i, :f, :t, :k)"
            ),
            {"i": uuid.uuid4(), "f": frm, "t": to, "k": kind},
        )


async def _scalar(engine: AsyncEngine, sql: str, params: dict | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


async def _all(engine: AsyncEngine, sql: str, params: dict | None = None) -> list[Any]:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        return (await db.execute(text(sql), params or {})).all()


async def _head_state(engine: AsyncEngine, claim_id: uuid.UUID) -> str:
    row = await _scalar(
        engine,
        "SELECT assessment_state FROM claim_assessment_heads "
        f"WHERE claim_id = :c AND config_snapshot_id = {SNAP_SUBQUERY}",
        {"c": claim_id},
    )
    assert row is not None
    return row[0]


# ─── pure closure walk ───────────────────────────────────────────────────────


def test_closure_walk_shapes() -> None:
    a, b, c, d, e = (_u("a"), _u("b"), _u("c"), _u("d"), _u("e"))
    # empty graph
    r = compute_reverse_closure([], a, 7)
    assert r.claim_ids == () and r.ranks == {} and r.count == 0

    # chain a <- b <- c (b depends on a, c depends on b): closure of a = (b, c)
    r = compute_reverse_closure([(b, a), (c, b)], a, 0)
    assert r.claim_ids == (b, c)
    assert r.ranks == {str(b): 1, str(c): 2}

    # diamond: c depends on both a and b; b depends on a — c is depth 1
    # (its SHORTEST path from a wins; the DAG has no cycles)
    r = compute_reverse_closure([(b, a), (c, a), (c, b)], a, 1)
    assert r.claim_ids == (b, c)
    assert r.ranks == {str(b): 1, str(c): 1}

    # deterministic ordering: (rank, claim id)
    x, y = _u("1"), _u("2")
    r = compute_reverse_closure([(y, a), (x, a)], a, 0)
    assert r.claim_ids == (x, y)

    # self-edges and research edges never enter the evidential closure
    r = compute_reverse_closure([(a, a), (d, e)], a, 0)
    assert r.claim_ids == ()

    # batch windows respect the durable cursor
    r = compute_reverse_closure([(b, a), (c, b)], a, 0)
    assert r.batch(0, 1) == (b,)
    assert r.batch(1, 1) == (c,)
    assert r.batch(2, 1) == ()


# ─── inline cascade ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inline_cascade_invalidates_downstream(migrated_db: Any) -> None:
    _url, engine = migrated_db
    root, d1, d2 = _u("1"), _u("2"), _u("3")
    await _seed_claim(engine, root, "root: база")
    await _seed_claim(engine, d1, "d1: зависит от root")
    await _seed_claim(engine, d2, "d2: зависит от d1")
    await _seed_edge(engine, d1, root)
    await _seed_edge(engine, d2, d1)

    k_before = int((await _scalar(engine, "SELECT revision FROM domain_revisions WHERE scope='knowledge'"))[0])
    g_before = int((await _scalar(engine, "SELECT revision FROM domain_revisions WHERE scope='dependency_graph'"))[0])

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")

    assert result.mode == "inline"
    assert result.closure_count == 2
    assert result.invalidated == 3  # root + d1 + d2
    assert result.jobs_created == 3
    assert result.barrier_id is None
    assert result.graph_revision == g_before
    assert result.knowledge_revision == k_before + 1

    # heads: root and the whole closure are pending (not current)
    for cid in (root, d1, d2):
        assert await _head_state(engine, cid) == "pending"

    # durable jobs + deterministic UUIDv5 questions
    jobs = await _scalar(
        engine, "SELECT count(*) FROM reassessment_jobs WHERE status='queued'"
    )
    assert int(jobs[0]) == 3
    snap = (await _scalar(engine, BOOTSTRAP_SNAP))[0]
    qid = uuid.uuid5(uuid.UUID("c0e3d3b6-dd7b-557d-a4d8-6e41049f8468"), f"cascade-invalidation:{snap}:{root}")
    q = await _scalar(engine, "SELECT origin FROM questions WHERE id = :q", {"q": qid})
    assert q is not None and q[0] == "invalid_assessment"

    # the graph itself is untouched
    g_after = int((await _scalar(engine, "SELECT revision FROM domain_revisions WHERE scope='dependency_graph'"))[0])
    assert g_after == g_before

    # audit
    audit = await _scalar(engine, "SELECT payload FROM audit_events WHERE type='cascade_started'")
    assert audit is not None
    assert audit[0]["root_claim_id"] == str(root)
    assert audit[0]["mode"] == "inline"
    assert audit[0]["knowledge_revision"] == k_before + 1


@pytest.mark.asyncio
async def test_cascade_replay_is_idempotent(migrated_db: Any) -> None:
    _url, engine = migrated_db
    root, d1 = _u("1"), _u("2")
    await _seed_claim(engine, root, "root: база")
    await _seed_claim(engine, d1, "d1: зависит от root")
    await _seed_edge(engine, d1, root)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        r1 = await start_cascade(db, root_claim_id=root, reason="root invalid")
    k_after = int((await _scalar(engine, "SELECT revision FROM domain_revisions WHERE scope='knowledge'"))[0])
    assert r1.invalidated == 2

    # replay (crash before the caller saw the result): nothing new
    async with factory() as db:
        r2 = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert r2.invalidated == 0
    assert r2.jobs_created == 0
    assert r2.knowledge_revision is None  # knowledge did not change
    k_replay = int((await _scalar(engine, "SELECT revision FROM domain_revisions WHERE scope='knowledge'"))[0])
    assert k_replay == k_after  # no bump on a no-op replay

    # exactly one job per claim (the unique active-job constraint holds)
    for cid in (root, d1):
        n = await _scalar(
            engine,
            "SELECT count(*) FROM reassessment_jobs WHERE claim_id=:c AND status IN ('queued','leased','retry')",
            {"c": cid},
        )
        assert int(n[0]) == 1
    # questions: still exactly 2
    q = await _scalar(engine, "SELECT count(*) FROM questions WHERE origin='invalid_assessment'")
    assert int(q[0]) == 2
    # one manifest, deduped by content
    m = await _scalar(engine, "SELECT count(*) FROM closure_manifests")
    assert int(m[0]) == 1


# ─── barrier lifecycle ───────────────────────────────────────────────────────


async def _big_root(engine: AsyncEngine, n: int) -> uuid.UUID:
    """Root + n direct dependents (n > BATCH_SIZE → barrier mode)."""
    root = _u("1")
    await _seed_claim(engine, root, "root: база")
    for i in range(2, n + 2):
        d = _u(hex(i).lstrip("0x"))
        await _seed_claim(engine, d, f"d{i}")
        await _seed_edge(engine, d, root)
    return root


@pytest.mark.asyncio
async def test_barrier_lifecycle_large_closure(migrated_db: Any) -> None:
    _url, engine = migrated_db
    n = BATCH_SIZE + 2  # 34 members → 2 batches
    root = await _big_root(engine, n)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert result.mode == "barrier"
    assert result.barrier_id is not None
    assert result.closure_count == n
    assert result.invalidated == 1 + BATCH_SIZE  # root + first batch

    barrier = await _scalar(
        engine,
        "SELECT status, next_offset, member_count, generation FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": result.barrier_id},
    )
    assert barrier is not None
    assert barrier[0] == "active"
    assert barrier[1] == BATCH_SIZE
    assert barrier[2] == n

    # step 1: apply the remaining 2 (crash-resume: a FRESH session, as
    # after a process restart — the durable cursor drives the resume)
    async with factory() as db:
        status = await process_barrier(db, result.barrier_id)
    assert status is BarrierStatus.RESOLVED

    # everything in the closure is pending now
    for i in range(2, n + 2):
        assert await _head_state(engine, _u(hex(i).lstrip("0x"))) == "pending"

    barrier = await _scalar(
        engine,
        "SELECT status, next_offset, generation, resolved_at IS NOT NULL "
        "FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": result.barrier_id},
    )
    assert barrier[0] == "resolved"
    assert barrier[1] == n
    assert barrier[2] == 1
    assert barrier[3] is True

    # jobs: root + n closure members
    jobs = await _scalar(engine, "SELECT count(*) FROM reassessment_jobs WHERE status='queued'")
    assert int(jobs[0]) == 1 + n

    # resolved barrier no longer protects its closure
    async with factory() as db:
        protected = await protected_claim_ids(db)
    assert protected == set()

    # audit trail
    batches = await _all(engine, "SELECT payload FROM audit_events WHERE type='barrier_batch_applied'")
    assert len(batches) == 2  # start batch + final batch
    resolved = await _scalar(engine, "SELECT 1 FROM audit_events WHERE type='barrier_resolved'")
    assert resolved is not None

    # resolved: process again → idempotent no-op
    async with factory() as db:
        again = await process_barrier(db, result.barrier_id)
    assert again is BarrierStatus.RESOLVED


@pytest.mark.asyncio
async def test_barrier_graph_change_publishes_new_generation(migrated_db: Any) -> None:
    _url, engine = migrated_db
    n = BATCH_SIZE + 1  # 33 members → 2 batches
    root = await _big_root(engine, n)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert result.mode == "barrier"

    # a concurrent session commits a NEW evidential edge (claim X depends
    # on the first closure member) and bumps the graph revision — exactly
    # what a T4.1-style commit does
    x = _u("ff")
    d2 = _u("2")
    await _seed_claim(engine, x, "x: новый descendant")
    await _seed_edge(engine, x, d2)
    await _scalar(engine, "UPDATE domain_revisions SET revision = revision + 1 WHERE scope='dependency_graph'")

    # step: the processor sees the moved graph revision → new generation
    async with factory() as db:
        status = await process_barrier(db, result.barrier_id)
    assert status is BarrierStatus.ACTIVE  # batch 0..32 of gen 2 applied

    barrier = await _scalar(
        engine,
        "SELECT status, next_offset, generation, member_count "
        "FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": result.barrier_id},
    )
    assert barrier[2] == 2
    assert barrier[3] == n + 1  # the fresh closure includes x
    assert barrier[1] == BATCH_SIZE

    # already processed claims are safely skipped (idempotent batches):
    # gen 2 re-invalidates nothing for the first 32 (they are pending)
    batches = await _all(engine, "SELECT payload FROM audit_events WHERE type='barrier_batch_applied'")
    gen2_first = batches[-1][0]
    assert gen2_first["invalidated"] == 0

    # next step: the tail batch includes x → closing → resolved
    async with factory() as db:
        status = await process_barrier(db, result.barrier_id)
    assert status is BarrierStatus.RESOLVED
    assert await _head_state(engine, x) == "pending"

    gen = await _scalar(engine, "SELECT count(*) FROM audit_events WHERE type='barrier_generation_published'")
    assert int(gen[0]) == 1


@pytest.mark.asyncio
async def test_barrier_blocked_on_manifest_tamper_keeps_protection(migrated_db: Any) -> None:
    _url, engine = migrated_db
    n = BATCH_SIZE + 1
    root = await _big_root(engine, n)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert result.mode == "barrier"

    # operator-tamper the immutable manifest (hash mismatch)
    await _scalar(
        engine,
        "UPDATE closure_manifests SET ranks = jsonb_set(ranks, '{2}', '99'::jsonb) "
        "WHERE root_claim_id = :r",
        {"r": root},
    )

    async with factory() as db:
        status = await process_barrier(db, result.barrier_id)
    assert status is BarrierStatus.BLOCKED
    barrier = await _scalar(
        engine,
        "SELECT status, last_error FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": result.barrier_id},
    )
    assert barrier[0] == "blocked"
    assert "manifest_hash_mismatch" in barrier[1]

    # blocked keeps ancestor protection: the closure claims are still
    # "not current" for retrieval
    async with factory() as db:
        protected = await protected_claim_ids(db)
    assert len(protected) == n

    # and a blocked barrier never auto-resolves: the processor refuses
    async with factory() as db:
        with pytest.raises(BarrierBlockedError):
            await process_barrier(db, result.barrier_id)


@pytest.mark.asyncio
async def test_retrieval_ancestor_check(migrated_db: Any) -> None:
    """While a barrier is open, a current claim inside its closure must
    not be served as current by retrieval (§8.6)."""
    from packages.cognition.retrieval import retrieve

    _url, engine = migrated_db
    root, d1 = _u("1"), _u("2")
    await _seed_claim(engine, root, "root: база")
    await _seed_claim(engine, d1, "d1: зависит от root про сорок два")
    await _seed_edge(engine, d1, root)

    # baseline: d1 is current and retrievable (only d1 matches the query)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "сорок", snapshot_id=(await db.execute(text(BOOTSTRAP_SNAP))).scalar_one())
    assert any(c.claim_id == d1 for c in res.current)

    # open a barrier on `root` (closure = d1 + 32 more dependents > 32)
    for i in range(3, BATCH_SIZE + 3):
        d = _u(hex(i).lstrip("0x"))
        await _seed_claim(engine, d, f"d{i} про число {i}")
        await _seed_edge(engine, d, root)

    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert result.mode == "barrier"

    # d1 is inside the open barrier's closure → not current anymore
    async with factory() as db:
        res = await retrieve(db, "сорок", snapshot_id=(await db.execute(text(BOOTSTRAP_SNAP))).scalar_one())
    assert not any(c.claim_id == d1 for c in res.current)

    # resolve the barrier (d1's head is already pending after the first
    # batch, so the final scan passes) → protection released
    async with factory() as db:
        status = await process_barrier(db, result.barrier_id)
    assert status is BarrierStatus.RESOLVED
    async with factory() as db:
        res = await retrieve(db, "сорок", snapshot_id=(await db.execute(text(BOOTSTRAP_SNAP))).scalar_one())
    # d1 is pending now (invalidated by the first batch) — it is NOT in
    # current regardless; the protection set itself is empty
    async with factory() as db:
        assert await protected_claim_ids(db) == set()


@pytest.mark.asyncio
async def test_start_cascade_fails_on_moved_graph(migrated_db: Any) -> None:
    _url, engine = migrated_db
    root, d1 = _u("1"), _u("2")
    await _seed_claim(engine, root, "root: база")
    await _seed_claim(engine, d1, "d1")
    await _seed_edge(engine, d1, root)

    # simulate a concurrent edge commit between the closure read (step 1)
    # and the locked verification (step 2): the second read of the graph
    # revision returns a moved value
    import packages.memory.cascade as cascade_mod

    real = cascade_mod._graph_revision
    calls = {"n": 0}

    async def moved(db: Any) -> int:
        calls["n"] += 1
        v = await real(db)
        return v + 1 if calls["n"] >= 2 else v

    cascade_mod._graph_revision = moved
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        async with factory() as db:
            with pytest.raises(CascadeError, match="graph revision moved"):
                await start_cascade(db, root_claim_id=root, reason="x")
    finally:
        cascade_mod._graph_revision = real

    # nothing was applied: the head is still current, no manifest
    assert await _head_state(engine, root) == "current"
    m = await _scalar(engine, "SELECT count(*) FROM closure_manifests")
    assert int(m[0]) == 0



