"""Scenario (DB): the reassessment worker (T4.3, §5.9.1, §8.7, §14.1).

Covers: the runnability predicate (effective pointer + empty activating
slot + due + budget), lease/retry with backoff, deterministic block
(head invalid + critical alert), insufficient data → invalid + research
question, head promotion with rules-engine re-evaluation, crash-lease
recovery, and the admission metrics for the T4.4 scheduler gates.
The worker has no LLM and no network by construction (it only reads
claims/evidence and runs the rules engine).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.memory import reassessment as worker_mod
from packages.memory.reassessment import (
    ACTOR,
    _backoff_seconds,
    recover_expired_leases,
    run_reassessment_batch,
    worker_admission_metrics,
)

pytestmark = [pytest.mark.scenario]

SNAP_SUBQUERY = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


def _u(tag: str) -> uuid.UUID:
    """Deterministic test UUIDs (same pattern as T4.1/T4.2 tests)."""
    return uuid.UUID(int=int(tag, 16))


async def _factory(engine: AsyncEngine) -> async_sessionmaker:
    return async_sessionmaker(engine, expire_on_commit=False)


async def _scalar(engine: AsyncEngine, sql: str, params: dict | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


async def _all(engine: AsyncEngine, sql: str, params: dict | None = None) -> list[Any]:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        return (await db.execute(text(sql), params or {})).all()


async def _seed_claim(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    statement: str,
    *,
    claim_type: str = "computed_result",
    state: str = "pending",
    scope: str = '{"x": 1}',
) -> None:
    """One claim (+ an old current assessment with scope when state is
    pending — the invalidated head the worker must re-evaluate) + head."""
    factory = await _factory(engine)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, :t, 'fresh')"
            ),
            {"id": claim_id, "s": statement, "t": claim_type},
        )
        if state == "pending":
            # the cascade left the head pending; the old assessment (with
            # the claim scope) is no longer referenced by the head
            aid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', :sc, 0.5, false)"
                ),
                {"a": aid, "c": claim_id, "sc": scope},
            )
        await db.execute(
            text(
                "INSERT INTO claim_assessment_heads "
                "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                " epistemic_status, prepared_by) "
                f"VALUES (:c, {SNAP_SUBQUERY}, :st, NULL, NULL, 'rules_activation')"
            ),
            {"c": claim_id, "st": state},
        )


async def _seed_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    *,
    relation: str = "supports",
    kind: str = "computation",
) -> None:
    """One computation evidence (the kind allowed for computed_result)
    with its observation artifact."""
    factory = await _factory(engine)
    async with factory() as db, db.begin():
        art = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO artifacts (id, sha256, size, trust_class) "
                "VALUES (:a, :sha, 10, 'session_workspace')"
            ),
            {"a": art, "sha": f"ev-{claim_id}-artifact"},
        )
        await db.execute(
            text(
                "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
                " scope, observation_artifact_id) "
                "VALUES (:id, :c, :r, :k, :h, '{\"x\": 1}', :a)"
            ),
            {"id": uuid.uuid4(), "c": claim_id, "r": relation, "k": kind, "h": f"ev-{claim_id}-h", "a": art},
        )


async def _seed_job(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    *,
    status: str = "queued",
    target: uuid.UUID | None = None,
    priority: int = 0,
) -> uuid.UUID:
    jid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, status, "
        " reason, priority) VALUES (:id, :c, "
        + (f"{SNAP_SUBQUERY} " if target is None else ":t ")
        + ", :st, 'test', :p)",
        {"id": jid, "c": claim_id, "st": status, "p": priority, "t": target},
    )
    return jid


async def _job_state(engine: AsyncEngine, job_id: uuid.UUID) -> dict[str, Any]:
    row = await _scalar(
        engine,
        "SELECT status, attempts, lease_owner, lease_expires_at, next_attempt_at, "
        "error_class, blocked_at, completed_at, last_error FROM reassessment_jobs "
        "WHERE id = :j",
        {"j": job_id},
    )
    assert row is not None
    return {
        "status": row[0],
        "attempts": row[1],
        "lease_owner": row[2],
        "lease_expires_at": row[3],
        "next_attempt_at": row[4],
        "error_class": row[5],
        "blocked_at": row[6],
        "completed_at": row[7],
        "last_error": row[8],
    }


async def _head_state(engine: AsyncEngine, claim_id: uuid.UUID) -> str:
    row = await _scalar(
        engine,
        "SELECT assessment_state FROM claim_assessment_heads "
        f"WHERE claim_id = :c AND config_snapshot_id = {SNAP_SUBQUERY}",
        {"c": claim_id},
    )
    assert row is not None
    return row[0]


async def _set_activating(engine: AsyncEngine, on: bool) -> None:
    """Move the activation slot together (CHECKs: the three fields move
    as one)."""
    if on:
        await _scalar(
            engine,
            "UPDATE runtime_config_heads SET "
            "activating_config_snapshot_id = active_config_snapshot_id, "
            "activation_fence = activation_fence + 1, "
            "activation_lease_owner = 'test-activator', "
            "activation_lease_expires_at = now() + interval '10 minutes' "
            "WHERE scope = 'global'",
        )
    else:
        await _scalar(
            engine,
            "UPDATE runtime_config_heads SET activating_config_snapshot_id = NULL, "
            "activation_lease_owner = NULL, activation_lease_expires_at = NULL "
            "WHERE scope = 'global'",
        )


async def _run(engine: AsyncEngine, **kwargs: Any) -> Any:
    factory = await _factory(engine)
    async with factory() as db:
        return await run_reassessment_batch(db, **kwargs)


# ─── pure ────────────────────────────────────────────────────────────────────


def test_backoff_monotonic_and_capped() -> None:
    lo, hi = _backoff_seconds(1), _backoff_seconds(8)
    assert 30.0 <= lo <= 45.0
    assert 3600.0 <= hi <= 3615.0
    # exponential growth with no overlap between consecutive windows
    for a, b in ((1, 2), (2, 3), (3, 4)):
        assert _backoff_seconds(b) >= _backoff_seconds(a) + 15.0


# ─── happy path: head promotion ─────────────────────────────────────────────


async def test_worker_promotes_head_to_current(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("1")
    await _seed_claim(engine, claim, "2 + 2 = 4, x=1")
    await _seed_evidence(engine, claim)
    job = await _seed_job(engine, claim)

    out = await _run(engine)
    assert out.processed == 1 and out.completed == 1 and out.blocked == 0 and out.retried == 0
    assert not out.deferred

    st = await _job_state(engine, job)
    assert st["status"] == "completed" and st["completed_at"] is not None
    assert st["lease_owner"] is None and st["lease_expires_at"] is None
    assert st["attempts"] == 1

    # head is current again, prepared by the worker, graded by the rules
    # engine (E2/supported: one computation group, scope covered)
    assert await _head_state(engine, claim) == "current"
    head = await _scalar(
        engine,
        "SELECT h.current_assessment_id, h.epistemic_status, h.prepared_by, "
        "a.effective_grade, a.rules_version, a.rules_hash, a.confidence "
        f"FROM claim_assessment_heads h LEFT JOIN claim_assessments a "
        f"ON a.id = h.current_assessment_id WHERE h.claim_id = :c "
        f"AND h.config_snapshot_id = {SNAP_SUBQUERY}",
        {"c": claim},
    )
    assert head is not None
    assert head[0] is not None and head[1] == "supported" and head[2] == "reassessment_worker"
    assert head[3] == "E2" and head[4] == "rules-v1" and head[5] is not None
    assert head[6] == pytest.approx(0.55)

    # the assessment is linked to the evidence (support role)
    links = await _all(
        engine,
        "SELECT ae.evidence_id, ae.role FROM assessment_evidence ae "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = ae.assessment_id "
        f"WHERE h.claim_id = :c AND h.config_snapshot_id = {SNAP_SUBQUERY}",
        {"c": claim},
    )
    assert len(links) == 1 and links[0][1] == "support"

    # the knowledge revision moved (fencing honesty, T4.2 pattern)
    rev = await _scalar(
        engine, "SELECT revision FROM domain_revisions WHERE scope = 'knowledge'"
    )
    assert rev is not None and int(rev[0]) >= 1  # bootstrap revision is 0

    # audit: the completed event with the worker actor
    ev = await _scalar(
        engine,
        "SELECT actor, payload->>'grade' FROM audit_events WHERE type = 'reassessment_job_completed' "
        "ORDER BY id DESC LIMIT 1",
    )
    assert ev is not None and ev[0] == ACTOR and ev[1] == "E2"

    # a second run is a no-op (the job is terminal)
    out2 = await _run(engine)
    assert out2.processed == 0 and out2.deferred


# ─── insufficient data → invalid + question ─────────────────────────────────


async def test_worker_insufficient_data_invalidates_and_questions(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("2")
    await _seed_claim(engine, claim, "нет данных")
    job = await _seed_job(engine, claim)

    out = await _run(engine)
    assert out.processed == 1 and out.completed == 1

    assert await _head_state(engine, claim) == "invalid"
    st = await _job_state(engine, job)
    assert st["status"] == "completed"

    # deterministic UUIDv5 research question (invalid_assessment origin)
    snap = await _scalar(
        engine, "SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global'"
    )
    assert snap is not None
    from packages.domain.config import QUESTION_UUID5_NAMESPACE

    expected = uuid.uuid5(
        uuid.UUID(QUESTION_UUID5_NAMESPACE),
        f"reassessment-insufficient:{snap[0]}:{claim}",
    )
    q = await _scalar(
        engine,
        "SELECT id, origin, state, text FROM questions WHERE origin = 'invalid_assessment'",
    )
    assert q is not None
    assert q[0] == expected and q[1] == "invalid_assessment" and "нет данных" in str(q[3])

    # the critical path is audited
    ev = await _scalar(
        engine,
        "SELECT payload->>'outcome' FROM audit_events "
        "WHERE type = 'reassessment_job_completed' LIMIT 1",
    )
    assert ev is not None and ev[0] == "insufficient_data"


# ─── runnability predicate ───────────────────────────────────────────────────


async def test_worker_defers_while_activating_slot_busy(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("3")
    await _seed_claim(engine, claim, "активация занята")
    await _seed_evidence(engine, claim)
    job = await _seed_job(engine, claim)

    await _set_activating(engine, on=True)
    out = await _run(engine)
    assert out.deferred and out.processed == 0
    st = await _job_state(engine, job)
    assert st["status"] == "queued" and st["attempts"] == 0 and st["lease_owner"] is None

    # empty slot again → the job becomes runnable
    await _set_activating(engine, on=False)
    out = await _run(engine)
    assert out.completed == 1
    assert await _head_state(engine, claim) == "current"


async def test_worker_ignores_job_for_other_snapshot(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("4")
    await _seed_claim(engine, claim, "чужой снапшот")
    await _seed_evidence(engine, claim)
    # a job for a NON-effective snapshot (the bootstrap snapshot's parent
    # does not exist — use a fresh config_snapshots row)
    other = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO config_snapshots (id, payload_sha256, sha256, activation_mode, "
        " activation_state, model, embeddings, prompts, policy, curiosity, token_budgets, "
        " session_limits, activation_limits, claim_type_rules) "
        "VALUES (:id, 'p', 's', 'offline', 'superseded', '{}', '{}', '{}', '{}', '{}', "
        "'{}', '{}', '{}', '{}')",
        {"id": other},
    )
    await _seed_job(engine, claim, target=other)
    # and a job for the effective snapshot
    job = await _seed_job(engine, claim)

    out = await _run(engine)
    assert out.processed == 1 and out.completed == 1
    other_row = (
        await _all(engine, "SELECT id FROM reassessment_jobs WHERE id <> :j", {"j": job})
    )[0][0]
    st_other = await _job_state(engine, other_row)
    assert st_other["status"] == "queued"
    st = await _job_state(engine, job)
    assert st["status"] == "completed"


async def test_worker_not_due_until_backoff_elapsed(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("5")
    await _seed_claim(engine, claim, "бэк-офф")
    job = await _seed_job(engine, claim)
    # simulate a previous failed attempt: the job is in retry, due later
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET status = 'retry', attempts = 2, "
        "next_attempt_at = now() + interval '1 hour' WHERE id = :j",
        {"j": job},
    )
    out = await _run(engine)
    assert out.deferred and out.processed == 0
    st = await _job_state(engine, job)
    assert st["status"] == "retry" and st["attempts"] == 2

    # due again → processed
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET next_attempt_at = now() - interval '1 second' WHERE id = :j",
        {"j": job},
    )
    await _seed_evidence(engine, claim)
    out = await _run(engine)
    assert out.completed == 1
    st = await _job_state(engine, job)
    assert st["status"] == "completed" and st["attempts"] == 3


# ─── error classes ───────────────────────────────────────────────────────────


async def test_worker_retry_on_transient_error_then_succeeds(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("6")
    await _seed_claim(engine, claim, "временный сбой")
    await _seed_evidence(engine, claim)
    job = await _seed_job(engine, claim)

    real_evaluate = worker_mod.evaluate
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_evaluate(*args, **kwargs)

    worker_mod.evaluate = flaky
    try:
        out = await _run(engine)
    finally:
        worker_mod.evaluate = real_evaluate

    assert out.processed == 1 and out.retried == 1
    st = await _job_state(engine, job)
    assert st["status"] == "retry"
    assert st["attempts"] == 1 and st["error_class"] == "RuntimeError"
    assert st["next_attempt_at"] is not None and st["lease_owner"] is None
    assert st["last_error"] == "boom"
    # head is still pending (no partial write)
    assert await _head_state(engine, claim) == "pending"

    # the job is NOT runnable before the backoff elapses
    out = await _run(engine)
    assert out.deferred and out.processed == 0

    # due again → succeeds
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET next_attempt_at = now() - interval '1 second' WHERE id = :j",
        {"j": job},
    )
    out = await _run(engine)
    assert out.completed == 1
    st = await _job_state(engine, job)
    assert st["status"] == "completed" and st["attempts"] == 2
    assert await _head_state(engine, claim) == "current"

    # retry is audited
    ev = await _scalar(
        engine,
        "SELECT payload->>'error_class' FROM audit_events "
        "WHERE type = 'reassessment_job_retry' LIMIT 1",
    )
    assert ev is not None and ev[0] == "RuntimeError"


async def test_worker_blocked_on_permanent_error(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("7")
    # a support evidence of a kind the rule does not allow: the rules
    # engine raises RuleValidationError (deterministic, §14.3)
    await _seed_claim(engine, claim, "недопустимый вид evidence")
    await _seed_evidence(engine, claim, kind="formal_check")
    job = await _seed_job(engine, claim)

    out = await _run(engine)
    assert out.processed == 1 and out.blocked == 1

    st = await _job_state(engine, job)
    assert st["status"] == "blocked"
    assert st["error_class"] == "rule_validation"
    assert st["blocked_at"] is not None
    assert st["lease_owner"] is None and st["lease_expires_at"] is None

    # the claim stays invalid, a question was created, a critical alert
    # was raised
    assert await _head_state(engine, claim) == "invalid"
    assert (
        await _scalar(engine, "SELECT count(*) FROM questions WHERE origin = 'invalid_assessment'")
    )[0] == 1
    alert = await _scalar(
        engine,
        "SELECT payload->>'alert_class' FROM audit_events WHERE type = 'alert_raised' LIMIT 1",
    )
    assert alert is not None and alert[0] == "reassessment_job_blocked"

    # a blocked job is not runnable: further runs are no-ops
    out = await _run(engine)
    assert out.deferred and out.processed == 0
    st = await _job_state(engine, job)
    assert st["status"] == "blocked"


# ─── crash recovery ──────────────────────────────────────────────────────────


async def test_expired_lease_is_recovered_and_completed(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("8")
    await _seed_claim(engine, claim, "крах между tx")
    await _seed_evidence(engine, claim)
    job = await _seed_job(engine, claim)

    # simulate a crash: the lease tx committed, the job tx never ran
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET status = 'leased', lease_owner = :o, "
        "lease_expires_at = now() - interval '1 second', attempts = 1 WHERE id = :j",
        {"o": ACTOR, "j": job},
    )

    # before recovery: the job is not runnable (status leased)
    out = await _run(engine)
    assert out.deferred and out.processed == 0

    factory = await _factory(engine)
    async with factory() as db:
        n = await recover_expired_leases(db)
    assert n == 1
    st = await _job_state(engine, job)
    assert st["status"] == "queued" and st["attempts"] == 0  # attempt restored
    assert st["lease_owner"] is None and st["lease_expires_at"] is None

    # the worker picks it up
    out = await _run(engine)
    assert out.completed == 1
    st = await _job_state(engine, job)
    assert st["status"] == "completed" and st["attempts"] == 1


async def test_unexpired_lease_is_not_stolen(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claim = _u("9")
    await _seed_claim(engine, claim, "активная аренда")
    await _seed_job(engine, claim)
    job = (await _all(engine, "SELECT id FROM reassessment_jobs"))[0][0]
    await _scalar(
        engine,
        "UPDATE reassessment_jobs SET status = 'leased', lease_owner = :o, "
        "lease_expires_at = now() + interval '10 minutes' WHERE id = :j",
        {"o": ACTOR, "j": job},
    )
    factory = await _factory(engine)
    async with factory() as db:
        n = await recover_expired_leases(db)
    assert n == 0
    st = await _job_state(engine, job)
    assert st["status"] == "leased"


# ─── admission metrics (T4.4 gates) ─────────────────────────────────────────


async def test_admission_metrics_track_runnable_jobs(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    factory = await _factory(engine)

    async with factory() as db:
        m = await worker_admission_metrics(db)
    assert m["runnable_count"] == 0 and m["oldest_runnable_age_seconds"] is None

    claim = _u("a")
    await _seed_claim(engine, claim, "метрики")
    await _seed_evidence(engine, claim)
    await _seed_job(engine, claim)

    async with factory() as db:
        m = await worker_admission_metrics(db)
    assert m["runnable_count"] == 1
    assert isinstance(m["oldest_runnable_age_seconds"], float)

    # activating slot busy → not runnable (the predicate is exact)
    await _set_activating(engine, on=True)
    async with factory() as db:
        m = await worker_admission_metrics(db)
    assert m["runnable_count"] == 0
    await _set_activating(engine, on=False)

    # after completion → empty again
    await _run(engine)
    async with factory() as db:
        m = await worker_admission_metrics(db)
    assert m["runnable_count"] == 0


# ─── batch semantics ─────────────────────────────────────────────────────────


async def test_batch_is_bounded_and_fifo(migrated_db: tuple[str, AsyncEngine]) -> None:
    _, engine = migrated_db
    claims = []
    for i in range(4):
        c = _u(f"c{i}")
        await _seed_claim(engine, c, f"батч {i}")
        await _seed_evidence(engine, c)
        jid = await _seed_job(engine, c, priority=0 if i != 3 else 1)
        claims.append((c, jid))

    out = await _run(engine, batch_size=2)
    assert out.processed == 2 and out.completed == 2

    # priority wins: the priority-1 job is in the first batch
    states = {c: (await _job_state(engine, j))["status"] for c, j in claims}
    assert states[claims[3][0]] == "completed"
    assert sum(1 for v in states.values() if v == "queued") == 2

    out = await _run(engine, batch_size=32)
    assert out.completed == 2
    final_states = []
    for _, j in claims:
        final_states.append((await _job_state(engine, j))["status"])
    assert all(st == "completed" for st in final_states)


async def test_midbatch_admission_loss_rolls_back_remaining_jobs(migrated_db: tuple[str, AsyncEngine]) -> None:
    """The admission re-check inside the per-job transaction: a config
    change between the lease tx and the job tx sends the job back to the
    queue WITHOUT consuming the attempt (§5.9.1: the session/activation
    always wins). The change is made on a side connection after the
    first job's tx started — the job rows are not locked between
    transactions, so this is deadlock-free (unlike mutating the runtime
    head, which the first job's tx holds)."""
    _, engine = migrated_db
    c1, c2 = _u("b1"), _u("b2")
    await _seed_claim(engine, c1, "первый в батче")
    await _seed_evidence(engine, c1)
    await _seed_claim(engine, c2, "второй в батче")
    await _seed_evidence(engine, c2)
    j1 = await _seed_job(engine, c1)
    j2 = await _seed_job(engine, c2)
    other_snap = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO config_snapshots (id, payload_sha256, sha256, activation_mode, "
        " activation_state, model, embeddings, prompts, policy, curiosity, token_budgets, "
        " session_limits, activation_limits, claim_type_rules) "
        "VALUES (:id, 'p', 's', 'offline', 'superseded', '{}', '{}', '{}', '{}', '{}', "
        "'{}', '{}', '{}', '{}')",
        {"id": other_snap},
    )

    real_process = worker_mod._process_one_job

    async def process_then_retarget(db: Any, job_id: Any, *, audit: Any) -> str:
        result = await real_process(db, job_id, audit=audit)
        if job_id == j1:
            # between the two job transactions: a config change moves the
            # second job's target away from the effective snapshot
            side = async_sessionmaker(engine, expire_on_commit=False)
            async with side() as db2, db2.begin():
                await db2.execute(
                    text(
                        "UPDATE reassessment_jobs "
                        "SET target_config_snapshot_id = :t WHERE id = :j"
                    ),
                    {"t": other_snap, "j": j2},
                )
        return result

    worker_mod._process_one_job = process_then_retarget
    try:
        out = await _run(engine)
    finally:
        worker_mod._process_one_job = real_process

    # the first job completed inside its tx; the second failed the
    # admission re-check and went back to the queue, attempt restored
    assert out.completed == 1
    st1 = await _job_state(engine, j1)
    st2 = await _job_state(engine, j2)
    assert st1["status"] == "completed"
    assert st2["status"] == "queued" and st2["attempts"] == 0 and st2["lease_owner"] is None
