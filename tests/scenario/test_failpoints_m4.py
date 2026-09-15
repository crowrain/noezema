"""Scenario (DB): M4 failpoints (T4.9, §8.6/§8.7.2/§11.3, §19 stage 3b).

Crash injection at the state boundaries the M4 machinery must survive:
the online activation pointer tuple (flip committed, manifest not
started), a process death between post-publish batches (durable
cursor), a stale activator reviving after a fenced takeover, the next
flip closing a blocked repair backlog, the dependency barrier
crashing after EVERY batch, the group-merge recompute job surviving a
worker crash (expired lease), and the session intent that cannot
starve the worker once its lease is dead.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import packages.memory.activation as act
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService
from packages.memory.cascade import BATCH_SIZE, process_barrier, start_cascade
from packages.memory.reassessment import recover_expired_leases, run_reassessment_batch
from packages.memory.source_graph import apply_source_graph_change
from packages.memory.writer_gate import register_session_intent
from tests.scenario.test_cascade import _big_root, _head_state, _scalar, _seed_claim, _u
from tests.scenario.test_online_activation import (
    _all_rows,
    _audit,
    _candidates,
    _factory,
    _make_blocked,
    _pointer,
    _prompts_payload,
    _rules_payload,
    _run_online,
    _seed_current_claims,
    _slot,
    _state_of,
)
from tests.scenario.test_reassessment import _seed_evidence, _seed_job
from tests.scenario.test_source_graph import (
    _head as _sg_head,
)
from tests.scenario.test_source_graph import (
    _host_source,
    _host_source_evidence,
    _run_worker,
    _seed_claim_pending,
    _source_members,
)
from tests.scenario.test_writer_admission import _seed_committing_session

pytestmark = [pytest.mark.scenario]

_real_run_post_publish = act.run_post_publish


# ─── 1. crash after the flip: the pointer tuple is durable ──────────────────


@pytest.mark.asyncio
async def test_crash_after_flip_recovers_pointer_tuple(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flip (pointer move + previous → superseded) commits in its
    own transaction; a crash before the post-publish manifest starts
    leaves the NEW snapshot effective and the slot held. Resume: no
    re-flip (one publish audit), the same fence, the manifest
    completes, the slot releases."""
    _, engine = migrated_db
    try:
        # change A (prompts only): fast path, completes to active
        res_a = await _run_online(engine, _prompts_payload())
        assert res_a.state == "active"
        cand_a = res_a.candidate_id

        # change B (rules): two affected claims → two pending heads
        await _seed_current_claims(engine, ["b1", "b2"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        async def crash_after_flip(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated crash: process died after the flip tx")

        monkeypatch.setattr(act, "run_post_publish", crash_after_flip)
        with pytest.raises(RuntimeError, match="after the flip tx"):
            res = await _run_online(engine, payload)

        # the pointer tuple: the NEW snapshot is effective, the
        # previous online one is superseded, the slot is still held
        cands = await _candidates(engine)
        assert len(cands) == 2
        cand_b = cands[-1][0]  # the rules change (created_at order)
        assert cand_b != cand_a
        assert await _pointer(engine) == cand_b  # the flip committed
        assert await _state_of(engine, cand_a) == "superseded"
        assert await _state_of(engine, cand_b) == "post_publish"
        slot = await _slot(engine)
        assert slot["activating"] == cand_b
        fence = slot["fence"]
        # exactly one publish for the candidate — a resume must not re-flip
        published = await _audit(engine, "activation_published")
        assert [p for p in published if p.get("candidate_id") == str(cand_b)] and len(
            [p for p in published if p.get("candidate_id") == str(cand_b)]
        ) == 1

        # the process restarts (same owner, live lease): the manifest
        # continues, the fence is unchanged (idempotent resume)
        monkeypatch.setattr(act, "run_post_publish", _real_run_post_publish)
        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert (await _slot(engine))["activating"] is None
        assert (await _slot(engine))["fence"] == fence
        takeovers = await _audit(engine, AuditEventType.ACTIVATION_TAKEOVER.value)
        assert len(takeovers) == 0
        qrows = await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand_b},
        )
        assert len(qrows) == 2
    finally:
        await engine.dispose()


# ─── 2. crash between post-publish batches: the cursor is durable ──────────


@pytest.mark.asyncio
async def test_crash_between_post_publish_batches_resumes_from_cursor(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """The process dies after the first question batch (one batch
    committed, cursor=1 of 2). The restart continues from the durable
    cursor: no duplicate question (UUIDv5 + cursor), the manifest
    completes in the second run."""
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["b3", "b4"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        calls = {"n": 0}

        async def one_batch_then_die(
            db: Any, audit: Any, *, candidate: Any, fence: int, owner: str = act.ACTOR,
            batch_size: int = 64, max_batches: int = 32,
        ) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                # one question, then the process dies (a clean return —
                # the driver sees the open manifest and stops)
                return await _real_run_post_publish(
                    db, audit, candidate=candidate, fence=fence, owner=owner,
                    batch_size=1, max_batches=1,
                )
            return await _real_run_post_publish(
                db, audit, candidate=candidate, fence=fence, owner=owner,
                batch_size=batch_size, max_batches=max_batches,
            )

        monkeypatch.setattr(act, "run_post_publish", one_batch_then_die)
        res1 = await _run_online(engine, payload)
        assert res1.state == "post_publish"  # the manifest is open
        cand = res1.candidate_id
        row = await _scalar(
            engine,
            "SELECT post_publish_cursor FROM config_snapshots WHERE id = :s",
            {"s": cand},
        )
        assert row is not None and int(row[0]) == 1
        assert len(await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )) == 1
        slot = await _slot(engine)
        assert slot["activating"] == cand  # still quiesced
        fence = slot["fence"]

        # the restart: the second batch finishes the manifest
        monkeypatch.setattr(act, "run_post_publish", _real_run_post_publish)
        res2 = await _run_online(engine, payload)
        assert res2.state == "active"
        assert (await _slot(engine))["activating"] is None
        assert (await _slot(engine))["fence"] == fence
        qrows = await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )
        assert len(qrows) == 2  # no duplicates across the crash
    finally:
        await engine.dispose()


# ─── 3. a stale activator is fenced out after a takeover ────────────────────


@pytest.mark.asyncio
async def test_stale_activator_after_takeover_is_fenced_out(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """O1 flips and dies mid-manifest; the lease goes dead; O2 takes
    over (fence + 1) and makes one batch. O1's process revives and
    calls post-publish with its STALE tuple (old fence, old owner):
    the fence predicate refuses it — no question, no error, no state
    change. O2 then finishes the manifest."""
    _, engine = migrated_db
    try:
        O1, O2 = "activator-1", "activator-2"
        await _seed_current_claims(engine, ["b5", "b6"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})

        async def crash_o1(*args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("simulated crash of O1 after the flip")

        monkeypatch.setattr(act, "run_post_publish", crash_o1)
        with pytest.raises(RuntimeError, match="O1 after the flip"):
            await _run_online(engine, payload, owner=O1)
        cands = await _candidates(engine)
        cand = cands[0][0]
        slot = await _slot(engine)
        fence_o1 = slot["fence"]
        assert slot["owner"] == O1

        # O1's machine is down: the lease is dead
        await _scalar(
            engine,
            "UPDATE runtime_config_heads SET activation_lease_owner = 'crashed', "
            "activation_lease_expires_at = now() - interval '1 minute' "
            "WHERE scope = 'global'",
        )

        # O2 takes over (fence + 1) and commits ONE batch
        o2_partial = {"done": False}

        async def o2_one_batch(
            db: Any, audit: Any, *, candidate: Any, fence: int, owner: str = act.ACTOR,
            batch_size: int = 64, max_batches: int = 32,
        ) -> Any:
            if owner == O2 and not o2_partial["done"]:
                o2_partial["done"] = True
                return await _real_run_post_publish(
                    db, audit, candidate=candidate, fence=fence, owner=owner,
                    batch_size=1, max_batches=1,
                )
            return await _real_run_post_publish(
                db, audit, candidate=candidate, fence=fence, owner=owner,
                batch_size=batch_size, max_batches=max_batches,
            )

        monkeypatch.setattr(act, "run_post_publish", o2_one_batch)
        res2 = await _run_online(engine, payload, owner=O2)
        assert res2.state == "post_publish"
        slot = await _slot(engine)
        assert slot["fence"] == fence_o1 + 1  # the takeover bumped it
        assert slot["owner"] == O2
        assert len(await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )) == 1

        # O1 revives with its stale tuple: the fence predicate refuses
        factory = await _factory(engine)
        async with factory() as db:
            cand_orm = await db.get(ORMConfigSnapshot, cand)
            assert cand_orm is not None
            stale = await act.run_post_publish(
                db, AuditService(db), candidate=cand_orm, fence=fence_o1, owner=O1
            )
        assert stale.deferred is True
        assert stale.state == "post_publish"
        assert stale.questions_created == 0
        # nothing moved: the manifest is untouched, O2 still owns the slot
        assert len(await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )) == 1
        slot = await _slot(engine)
        assert slot["owner"] == O2 and slot["fence"] == fence_o1 + 1

        # O2 finishes the manifest
        async with factory() as db:
            cand_orm = await db.get(ORMConfigSnapshot, cand)
            assert cand_orm is not None
            done = await act.run_post_publish(
                db, AuditService(db), candidate=cand_orm, fence=fence_o1 + 1, owner=O2
            )
        assert done.state == "active"
        assert (await _slot(engine))["activating"] is None
        assert len(await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )) == 2
    finally:
        await engine.dispose()


# ─── 4. the next flip closes the blocked repair backlog ─────────────────────


@pytest.mark.asyncio
async def test_next_flip_closes_blocked_backlog(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post_publish_blocked candidate owns the pointer (the terminal
    cleanup cleared the slot). A NEW online change flips on top of it:
    the flip marks the blocked previous as superseded — the repair
    backlog is closed by the flip itself, the repair runner finds
    nothing, the new pointer is untouched by repair."""
    _, engine = migrated_db
    try:
        blocked = await _make_blocked(engine, monkeypatch, "9c")
        assert await _state_of(engine, blocked) == "post_publish_blocked"
        assert await _pointer(engine) == blocked
        assert (await _slot(engine))["activating"] is None

        # the new change (prompts only — no affected claims)
        res = await _run_online(engine, _prompts_payload())
        assert res.state == "active"
        new_cand = res.candidate_id
        assert new_cand != blocked
        # the flip superseded the blocked previous
        assert await _state_of(engine, blocked) == "superseded"
        assert await _pointer(engine) == new_cand
        assert (await _slot(engine))["activating"] is None

        # the repair lane has nothing left to do
        factory = await _factory(engine)
        async with factory() as db:
            backlog = await act.find_repair_backlog(db)
        assert backlog is None
    finally:
        await engine.dispose()


# ─── 5. the barrier crashes after EVERY batch and still resolves ────────────


@pytest.mark.asyncio
async def test_barrier_crash_after_every_batch(migrated_db: Any) -> None:
    """Three batches; the process dies after each of them (a fresh
    session resumes from the durable cursor every time). The final
    closure scan runs only on the last resume — the barrier resolves
    exactly once, every head in the closure is pending, every job is
    durable."""
    _url, engine = migrated_db
    n = 2 * BATCH_SIZE + 1  # 65 members → 3 batches
    root = await _big_root(engine, n)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db:
        result = await start_cascade(db, root_claim_id=root, reason="root invalid")
    assert result.mode == "barrier" and result.barrier_id is not None
    bid = result.barrier_id

    # crash 1: after the first batch (offset 32) — resume
    row = await _scalar(
        engine,
        "SELECT status, next_offset FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": bid},
    )
    assert row is not None and row[0] == "active" and int(row[1]) == BATCH_SIZE
    async with factory() as db:
        status = await process_barrier(db, bid)
    assert status.value == "active"  # batch 2 applied, one batch remains
    row = await _scalar(
        engine,
        "SELECT status, next_offset FROM dependency_invalidation_barriers WHERE id=:i",
        {"i": bid},
    )
    assert row is not None and int(row[1]) == 2 * BATCH_SIZE

    # crash 2: after the second batch — resume, the last batch + the
    # final closure scan + resolved land in one transaction
    async with factory() as db:
        status = await process_barrier(db, bid)
    assert status.value == "resolved"

    # every closure member is pending; the jobs are durable
    for i in range(2, n + 2):
        assert await _head_state(engine, _u(hex(i).lstrip("0x"))) == "pending"
    jobs = await _scalar(engine, "SELECT count(*) FROM reassessment_jobs WHERE status='queued'")
    assert int(jobs[0]) == 1 + n

    # exactly three batch audits (start + two resumes) and one resolved
    batches = await _scalar(engine, "SELECT count(*) FROM audit_events WHERE type='barrier_batch_applied'")
    assert int(batches[0]) == 3
    resolved = await _scalar(engine, "SELECT count(*) FROM audit_events WHERE type='barrier_resolved'")
    assert int(resolved[0]) == 1


# ─── 6. the group-merge recompute survives a worker crash ───────────────────


@pytest.mark.asyncio
async def test_group_merge_recompute_survives_worker_crash(migrated_db: Any) -> None:
    """The merge correction cascades (head pending + durable job). The
    worker dies while holding the job lease (crash between the lease
    tx and the job tx): the lease expires, recovery re-queues the job,
    the next batch completes the merge recompute — the grade drops
    supported → hypothesis on the FRESH snapshot."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт, источники которого слили", "external_fact")
    s1 = await _host_source(engine, uri="https://crash-a.net/story")
    s2 = await _host_source(engine, uri="https://crash-b.org/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    await _seed_job(engine, claim_id)

    # phase A: two independent sources → supported E3
    await _run_worker(engine)
    head = await _sg_head(engine, claim_id)
    assert head[1] == "supported" and head[3] == "E3"

    # phase B: the merge correction + the cascade (one tx)
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO source_graph_corrections "
                "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
                "VALUES (:id, :a, 'merge', :f, :t, 'rules-v1', true)"
            ),
            {"id": uuid.uuid4(), "a": "operator-1", "f": s1, "t": s2},
        )
        result = await apply_source_graph_change(
            db, AuditService(db), source_ids=frozenset({s1, s2}), actor="operator-1"
        )
    assert result.invalidated == 1 and result.jobs_created == 1

    # the worker leases the job and CRASHES: the lease row stays with
    # a dead owner; time passes (the lease expires)
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET status = 'leased', lease_owner = 'crashed-worker', "
        "lease_expires_at = now() - interval '1 minute' "
        "WHERE claim_id = :c AND reason = 'source_graph_change'",
        {"c": claim_id},
    )
    # nothing is re-queued while the lease is considered live… the
    # scheduler's recovery pass (before every batch) returns it
    async with factory() as db:
        recovered = await recover_expired_leases(db)
    assert recovered == 1

    # the next batch completes the merge recompute
    await _run_worker(engine)
    head = await _sg_head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "hypothesis"  # one group after the merge
    members = await _source_members(engine, claim_id)
    assert len(members) == 2
    assert len({m[0] for m in members}) == 1  # the merge took effect
    job = await _scalar(
        engine,
        "SELECT status FROM reassessment_jobs WHERE claim_id = :c "
        "AND reason = 'source_graph_change'",
        {"c": claim_id},
    )
    assert job is not None and job[0] == "completed"


# ─── 7. a dead session intent cannot starve the worker ──────────────────────


@pytest.mark.asyncio
async def test_worker_not_starved_after_intent_lease_expiry(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    """Rule 4 bounds both ways: the worker yields to a LIVE session
    intent, but a dead lease stops being a declaration — the same
    batch completes right after the intent's lease expires (the
    session lane cannot hold the worker's window indefinitely)."""
    _, engine = migrated_db
    factory = await _factory(engine)
    try:
        claim = uuid.uuid4()
        await _seed_claim(engine, claim, "засевший интент")
        await _seed_evidence(engine, claim)
        jid = await _seed_job(engine, claim)
        sid = await _seed_committing_session(engine)
        async with factory() as db, db.begin():
            assert await register_session_intent(db, sid)

        # live intent: the worker defers, the job is untouched
        async with factory() as db:
            out = await run_reassessment_batch(db)
        assert out.deferred and out.processed == 0
        row = await _scalar(
            engine,
            "SELECT status, attempts FROM reassessment_jobs WHERE id = :j",
            {"j": jid},
        )
        assert row is not None and row[0] == "queued" and int(row[1]) == 0

        # the session's lease dies (crashed mid-commit): the intent is
        # no longer live — the worker takes the window
        await _scalar(
            engine,
            "UPDATE sessions SET lease_expires_at = now() - interval '1 minute' "
            "WHERE id = :s",
            {"s": sid},
        )
        async with factory() as db:
            out = await run_reassessment_batch(db)
        assert out.completed == 1 and out.deferred is False
        row = await _scalar(
            engine,
            "SELECT status FROM reassessment_jobs WHERE id = :j",
            {"j": jid},
        )
        assert row is not None and row[0] == "completed"
    finally:
        await engine.dispose()
