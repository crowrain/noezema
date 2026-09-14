"""Scenario (DB): online activation of a config snapshot (T4.5, §8.7.2).

Covers: the fenced lease (acquire / idempotent resume / takeover with a
fence bump), the quiesce preconditions (writer gate wait, active
sessions), the bounded idempotent shadow-head prepare (fast path for
unchanged claim types, pending + durable job for affected ones), the
separate verification + immutable seal, the atomic flip (pointer move,
previous → superseded, knowledge bump), the post-publish manifest
(deterministic UUIDv5 questions, cursor batches, transient backoff,
retry exhaustion → post_publish_blocked + alert), the trusted repair
runner (repair CAS, superseded close), the scheduler's
T_repair_admission gate, and the sealed-interval DB trigger.
"""

from __future__ import annotations

import copy
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

import packages.memory.activation as act
from packages.domain.config import BOOTSTRAP_PAYLOAD, QUESTION_UUID5_NAMESPACE
from packages.domain.db.uow import transaction
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService
from packages.memory.reassessment import run_reassessment_batch
from packages.memory.writer_gate import (
    GATE_PRIORITY_WORKER,
    OWNER_WORKER,
    acquire_writer_gate,
    release_writer_gate,
)
from tests.scenario.test_cascade import _seed_claim
from tests.scenario.test_reassessment import _factory, _scalar, _seed_evidence
from tests.scenario.test_wake_scheduler import NOW, _sched, _seed_session

pytestmark = [pytest.mark.scenario]

SNAP = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


def _rules_payload(**rule_patches: dict) -> dict[str, Any]:
    p = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    for ctype, patch in rule_patches.items():
        p["claim_type_rules"][ctype].update(patch)
    return p


def _prompts_payload() -> dict[str, Any]:
    p = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    p["prompts"] = dict(p["prompts"])
    p["prompts"]["session_opening"] = "изменённый системный промпт (тест)"
    return p


async def _run_online(
    engine: AsyncEngine, payload: dict[str, Any], **kwargs: Any
) -> act.ActivationResult:
    factory = await _factory(engine)
    async with factory() as db:
        return await act.run_online_change(db, AuditService(db), requested_payload=payload, **kwargs)


async def _candidates(engine: AsyncEngine) -> list[tuple[uuid.UUID, str, str | None]]:
    rows = (
        await _all_rows(
            engine,
            "SELECT id, activation_state, base_snapshot_id FROM config_snapshots "
            "WHERE activation_mode = 'online' ORDER BY created_at",
        )
    )
    return [(r[0], r[1], r[2]) for r in rows]


async def _all_rows(engine: AsyncEngine, sql: str, params: dict | None = None) -> list[Any]:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        return (await db.execute(text(sql), params or {})).all()


async def _slot(engine: AsyncEngine) -> dict[str, Any]:
    row = (
        await _scalar(
            engine,
            "SELECT activating_config_snapshot_id, activation_fence, activation_lease_owner, "
            "activation_lease_expires_at FROM runtime_config_heads WHERE scope = 'global'",
        )
    )
    assert row is not None
    return {
        "activating": row[0],
        "fence": row[1],
        "owner": row[2],
        "lease_expires_at": row[3],
    }


async def _head(
    engine: AsyncEngine, claim_id: uuid.UUID, snap: str = SNAP
) -> dict[str, Any] | None:
    row = (
        await _scalar(
            engine,
            "SELECT assessment_state, current_assessment_id, epistemic_status, prepared_by "
            "FROM claim_assessment_heads WHERE claim_id = :c AND config_snapshot_id = " + snap,
            {"c": claim_id},
        )
    )
    if row is None:
        return None
    return {
        "state": row[0],
        "assessment_id": row[1],
        "status": row[2],
        "prepared_by": row[3],
    }


async def _jobs_for(engine: AsyncEngine, snapshot_id: uuid.UUID) -> list[Any]:
    return await _all_rows(
        engine,
        "SELECT claim_id, status, reason FROM reassessment_jobs "
        "WHERE target_config_snapshot_id = :s ORDER BY claim_id",
        {"s": snapshot_id},
    )


async def _audit(engine: AsyncEngine, etype: str) -> list[dict[str, Any]]:
    rows = (
        await _all_rows(engine, "SELECT payload FROM audit_events WHERE type = :t", {"t": etype})
    )
    return [dict(r[0]) for r in rows]


async def _pointer(engine: AsyncEngine) -> uuid.UUID:
    row = await _scalar(engine, "SELECT active_config_snapshot_id FROM runtime_config_heads")
    assert row is not None
    return row[0]


async def _bootstrap(engine: AsyncEngine) -> uuid.UUID:
    row = await _scalar(
        engine, "SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'"
    )
    assert row is not None
    return row[0]


async def _state_of(engine: AsyncEngine, snap_id: uuid.UUID) -> str:
    row = await _scalar(
        engine, "SELECT activation_state FROM config_snapshots WHERE id = :s", {"s": snap_id}
    )
    assert row is not None
    return row[0]


async def _seed_current_claims(
    engine: AsyncEngine, tags: list[str], *, with_evidence: bool = False
) -> list[uuid.UUID]:
    cids: list[uuid.UUID] = []
    for tag in tags:
        cid = uuid.UUID(int=int(tag, 16))
        cids.append(cid)
        await _seed_claim(engine, cid, f"утверждение {tag}")
        # test_cascade seeds assessed_scope='{}' — the rules engine then
        # fails requires_scope; carry the same scope the evidence has
        await _scalar(
            engine,
            "UPDATE claim_assessments SET assessed_scope = '{\"x\": 1}' "
            "WHERE id = (SELECT id FROM claim_assessments WHERE claim_id = :c "
            "ORDER BY created_at DESC, id DESC LIMIT 1)",
            {"c": cid},
        )
        if with_evidence:
            await _seed_evidence(engine, cid)
    return cids


# ─── 1. happy path: fast path + affected ────────────────────────────────────


@pytest.mark.asyncio
async def test_online_happy_path_mixed_fast_and_affected(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _, engine = migrated_db
    try:
        cids = await _seed_current_claims(engine, ["91", "92", "93"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        res = await _run_online(engine, payload)
        assert res.published and res.state == "active"
        assert res.pending_heads == 3  # all claims are computed_result
        assert res.questions_created == 3

        cand = res.candidate_id
        assert await _pointer(engine) == cand
        assert await _state_of(engine, cand) == "active"
        # the bootstrap is immutable (the CHECK keeps it 'active' forever)
        assert await _state_of(engine, await _bootstrap(engine)) == "active"

        slot = await _slot(engine)
        assert slot["activating"] is None and slot["owner"] is None

        for cid in cids:
            h = await _head(engine, cid, f"'{cand}'")
            assert h is not None and h["state"] == "pending"
            assert h["assessment_id"] is None and h["status"] is None
        jobs = await _jobs_for(engine, cand)
        assert len(jobs) == 3 and all(j[1] == "queued" and j[2] == "activation" for j in jobs)

        # deterministic post-publish questions (UUIDv5)
        qrows = await _all_rows(
            engine,
            "SELECT id, origin, origin_config_snapshot_id FROM questions "
            "WHERE origin_config_snapshot_id = :c ORDER BY id",
            {"c": cand},
        )
        assert len(qrows) == 3
        ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
        qids = {qrow[0] for qrow in qrows}
        for cid in cids:
            expected = uuid.uuid5(ns, f"activation-pending:{cand}:{cid}")
            assert expected in qids  # deterministic UUIDv5 per pending head
        assert all(qrow[1] == "previous_result" for qrow in qrows)

        events = {
            e.value
            for e in (
                AuditEventType.ACTIVATION_ACQUIRED,
                AuditEventType.ACTIVATION_PUBLISHED,
                AuditEventType.ACTIVATION_POST_PUBLISH_COMPLETED,
                AuditEventType.ACTIVATION_CLEANED_UP,
            )
            if await _audit(engine, e.value)
        }
        assert events == {
            AuditEventType.ACTIVATION_ACQUIRED.value,
            AuditEventType.ACTIVATION_PUBLISHED.value,
            AuditEventType.ACTIVATION_POST_PUBLISH_COMPLETED.value,
            AuditEventType.ACTIVATION_CLEANED_UP.value,
        }

        # the worker completes the activation jobs (target = active pointer)
        factory = await _factory(engine)
        async with factory() as db:
            outcome = await run_reassessment_batch(db, batch_size=4)
        assert outcome.completed == 3
        for cid in cids:
            h = await _head(engine, cid)
            assert h is not None and h["state"] == "current"
            assert h["assessment_id"] is not None and h["status"] == "supported"
    finally:
        await engine.dispose()


# ─── 2. all fast path (prompts-only change) ─────────────────────────────────


@pytest.mark.asyncio
async def test_online_all_fast_path_no_jobs_no_questions(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _, engine = migrated_db
    try:
        cids = await _seed_current_claims(engine, ["94", "95"])
        res = await _run_online(engine, _prompts_payload())
        assert res.published and res.state == "active"
        cand = res.candidate_id
        assert res.pending_heads == 0 and res.questions_created == 0
        assert await _pointer(engine) == cand

        for cid in cids:
            old = await _head(engine, cid, f"'{await _bootstrap(engine)}'")
            new = await _head(engine, cid, f"'{cand}'")
            assert new is not None and old is not None
            # the fast path carries the OLD valid assessment reference
            assert new["state"] == "current"
            assert new["assessment_id"] == old["assessment_id"]
            assert new["status"] == old["status"]
            assert new["prepared_by"] == "rules_activation"
        assert await _jobs_for(engine, cand) == []
        n_questions = await _scalar(
            engine,
            "SELECT count(*) FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )
        assert n_questions is not None and n_questions[0] == 0
    finally:
        await engine.dispose()


# ─── 3. pre-publish failure → failed + slot cleared ─────────────────────────


@pytest.mark.asyncio
async def test_online_pre_publish_failure_marks_failed(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["96"])

        async def broken_seal(*args: Any, **kwargs: Any) -> str:
            raise act.ActivationError("seal mismatch (test)")

        monkeypatch.setattr(act, "verify_and_seal_online", broken_seal)
        with pytest.raises(act.ActivationError, match="seal mismatch"):
            await _run_online(engine, _rules_payload(computed_result={"min_grade_for_supported": "E3"}))

        cands = await _candidates(engine)
        assert len(cands) == 1
        assert cands[0][1] == "failed"
        slot = await _slot(engine)
        assert slot["activating"] is None  # terminal cleanup cleared the slot
        # the pointer never moved
        assert await _pointer(engine) == await _bootstrap(engine)
        assert await _state_of(engine, await _bootstrap(engine)) == "active"
        cleaned = await _audit(engine, AuditEventType.ACTIVATION_CLEANED_UP.value)
        assert len(cleaned) == 1 and cleaned[0]["outcome"] == "failed"
    finally:
        await engine.dispose()


# ─── 4. crash mid-prepare → idempotent resume ───────────────────────────────


@pytest.mark.asyncio
async def test_online_crash_mid_prepare_resumes(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["97", "98"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        real_seal = act.verify_and_seal_online
        calls = {"n": 0}

        async def flaky_seal(db: Any, **kwargs: Any) -> str:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated crash after prepare")
            return await real_seal(db, **kwargs)

        monkeypatch.setattr(act, "verify_and_seal_online", flaky_seal)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await _run_online(engine, payload)

        # the slot is still held (no crash cleanup for a non-deterministic
        # failure) and the candidate stays preparing_heads
        cands = await _candidates(engine)
        assert cands[0][1] == "preparing_heads"
        slot = await _slot(engine)
        assert slot["activating"] == cands[0][0]
        fence_after_crash = slot["fence"]

        monkeypatch.setattr(act, "verify_and_seal_online", real_seal)
        res = await _run_online(engine, payload)
        assert res.state == "active" and res.published
        slot = await _slot(engine)
        assert slot["activating"] is None
        # same owner, live lease → idempotent resume, the fence is unchanged
        assert slot["fence"] == fence_after_crash
        assert len(await _audit(engine, AuditEventType.ACTIVATION_TAKEOVER.value)) == 0
    finally:
        await engine.dispose()


# ─── 5. takeover bumps the fence ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_online_takeover_bumps_fence(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["99"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        real_publish = act.publish_online

        async def crash_flip(*args: Any, **kwargs: Any) -> int:
            raise RuntimeError("simulated crash at the flip")

        monkeypatch.setattr(act, "publish_online", crash_flip)
        with pytest.raises(RuntimeError, match="simulated crash"):
            await _run_online(engine, payload)

        cands = await _candidates(engine)
        cand = cands[0][0]
        assert cands[0][1] == "ready"  # the seal committed before the crash
        slot = await _slot(engine)
        fence_before = slot["fence"]

        # the owner crashed: a foreign dead lease on the same candidate
        await _scalar(
            engine,
            "UPDATE runtime_config_heads SET activation_lease_owner = 'crashed', "
            "activation_lease_expires_at = now() - interval '1 minute' "
            "WHERE scope = 'global'",
        )
        monkeypatch.setattr(act, "publish_online", real_publish)
        res = await _run_online(engine, payload)
        assert res.state == "active"
        slot = await _slot(engine)
        assert slot["fence"] == fence_before + 1  # the takeover bumped it
        assert slot["activating"] is None
        takeovers = await _audit(engine, AuditEventType.ACTIVATION_TAKEOVER.value)
        assert len(takeovers) == 1 and takeovers[0]["fence"] == fence_before + 1
        assert takeovers[0]["candidate_id"] == str(cand)
    finally:
        await engine.dispose()


# ─── 6. gate wait times out (foreign live holder) ───────────────────────────


@pytest.mark.asyncio
async def test_online_gate_wait_times_out(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["9a"])
        factory = await _factory(engine)
        async with factory() as db, db.begin():
            got = await acquire_writer_gate(
                db,
                owner_kind=OWNER_WORKER,
                owner_id="worker-1",
                priority=GATE_PRIORITY_WORKER,
                lease_seconds=60,
            )
        assert got

        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        with pytest.raises(act.ActivationError, match="writer gate"):
            await _run_online(engine, payload, gate_wait_seconds=1)

        # the candidate was created (draft) but the slot was never taken
        cands = await _candidates(engine)
        assert len(cands) == 1 and cands[0][1] == "draft"
        assert (await _slot(engine))["activating"] is None

        async with factory() as db, db.begin():
            await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id="worker-1")
        res = await _run_online(engine, payload)
        assert res.state == "active"  # the second run proceeds normally
    finally:
        await engine.dispose()


# ─── 7. an active session blocks the acquire ────────────────────────────────


@pytest.mark.asyncio
async def test_online_active_session_blocks_acquire(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["81"])
        factory = await _factory(engine)
        async with factory() as db, db.begin():
            await _seed_session(db, state="exploring")

        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        with pytest.raises(act.ActivationError, match="active sessions"):
            await _run_online(engine, payload)
        assert (await _slot(engine))["activating"] is None

        async with factory() as db, db.begin():
            await db.execute(text("DELETE FROM sessions WHERE state = 'exploring'"))
        res = await _run_online(engine, payload)
        assert res.state == "active"
    finally:
        await engine.dispose()


# ─── 8. post-publish transient failure → backoff, then success ──────────────


@pytest.mark.asyncio
async def test_post_publish_transient_backoff_then_success(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        await _seed_current_claims(engine, ["82", "83"], with_evidence=True)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        real_q = act._question_for_pending_head
        calls = {"n": 0}

        async def flaky_q(db: Any, **kwargs: Any) -> int:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("simulated transient question failure")
            return await real_q(db, **kwargs)

        monkeypatch.setattr(act, "_question_for_pending_head", flaky_q)
        with pytest.raises(RuntimeError, match="simulated transient"):
            await _run_online(engine, payload)

        cands = await _candidates(engine)
        cand = cands[0][0]
        assert cands[0][1] == "post_publish"  # the flip committed
        assert await _pointer(engine) == cand
        row = await _scalar(
            engine,
            "SELECT post_publish_attempts, post_publish_next_attempt_at, "
            "post_publish_last_error FROM config_snapshots WHERE id = :s",
            {"s": cand},
        )
        assert row is not None
        assert row[0] == 1  # attempts+1
        assert row[2] is not None and "RuntimeError" in str(row[2])
        # the backoff window is in the future
        due = await _scalar(
            engine,
            "SELECT (post_publish_next_attempt_at > now()) FROM config_snapshots WHERE id = :s",
            {"s": cand},
        )
        assert due is not None and due[0] is True
        # the slot is still held (the worker stays quiesced)
        assert (await _slot(engine))["activating"] == cand

        # fast-forward the backoff and resume
        monkeypatch.setattr(act, "_question_for_pending_head", real_q)
        await _scalar(
            engine,
            "UPDATE config_snapshots SET post_publish_next_attempt_at = now() - interval '1 second' "
            "WHERE id = :s",
            {"s": cand},
        )
        res = await _run_online(engine, payload)
        assert res.state == "active"
        assert await _state_of(engine, cand) == "active"
        assert (await _slot(engine))["activating"] is None
        qrows = await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )
        assert len(qrows) == 2
    finally:
        await engine.dispose()


# ─── 9. retry exhaustion → blocked + alert, then the repair runner ─────────


async def _make_blocked(
    engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch, tag: str
) -> uuid.UUID:
    """Drive an online change to post_publish_blocked (budget = 1)."""
    cids = await _seed_current_claims(engine, [tag], with_evidence=True)
    payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
    payload["activation_limits"]["online_activation_max_attempts"] = 1
    real_q = act._question_for_pending_head

    async def broken_q(db: Any, **kwargs: Any) -> int:
        raise RuntimeError("simulated permanent-ish question failure")

    monkeypatch.setattr(act, "_question_for_pending_head", broken_q)
    res = await _run_online(engine, payload)
    monkeypatch.setattr(act, "_question_for_pending_head", real_q)
    assert res.state == "post_publish_blocked"
    assert cids  # one affected claim
    return res.candidate_id


@pytest.mark.asyncio
async def test_post_publish_exhaustion_blocks_then_repair_completes(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        cand = await _make_blocked(engine, monkeypatch, "84")
        assert await _state_of(engine, cand) == "post_publish_blocked"
        # terminal cleanup: the slot cleared, the pointer is on the candidate
        slot = await _slot(engine)
        assert slot["activating"] is None
        assert await _pointer(engine) == cand
        blocked = await _audit(engine, AuditEventType.ACTIVATION_POST_PUBLISH_BLOCKED.value)
        assert len(blocked) == 1
        alerts = await _audit(engine, AuditEventType.ALERT_RAISED.value)
        assert any(a.get("alert_class") == "post_publish_blocked" for a in alerts)
        cleaned = await _audit(engine, AuditEventType.ACTIVATION_CLEANED_UP.value)
        assert any(c["outcome"] == "post_publish_blocked" for c in cleaned)

        # the trusted repair runner (repair CAS: active = candidate,
        # activating IS NULL, state post_publish_blocked, due)
        factory = await _factory(engine)
        async with factory() as db:
            outcome = await act.run_activation_repair(
                db, AuditService(db), candidate=cand, batch_size=64
            )
        assert outcome.state == "active"
        assert await _state_of(engine, cand) == "active"
        rows = await _all_rows(
            engine,
            "SELECT id FROM questions WHERE origin_config_snapshot_id = :c",
            {"c": cand},
        )
        assert len(rows) == 1
        batches = await _audit(engine, AuditEventType.ACTIVATION_POST_PUBLISH_BATCH.value)
        assert any(b.get("phase") == "repair" for b in batches)
        completed = await _audit(engine, AuditEventType.ACTIVATION_POST_PUBLISH_COMPLETED.value)
        assert len(completed) == 1
    finally:
        await engine.dispose()


# ─── 10. repair closes a superseded backlog, never reactivates ─────────────


@pytest.mark.asyncio
async def test_repair_closes_superseded_backlog(
    migrated_db: tuple[str, AsyncEngine], monkeypatch: pytest.MonkeyPatch
) -> None:
    _, engine = migrated_db
    try:
        old_cand = await _make_blocked(engine, monkeypatch, "85")

        # a NEWER flip owns the pointer now (simulated): a different
        # candidate becomes active
        factory = await _factory(engine)
        new_payload = _prompts_payload()
        async with factory() as db, db.begin():
            audit = AuditService(db)
            head_row = (
                await db.execute(
                    text("SELECT active_config_snapshot_id FROM runtime_config_heads")
                )
            ).scalar_one()
            base = await db.get(ORMConfigSnapshot, head_row)
            assert base is not None
            new_cand, already = await act.upsert_online_candidate(
                db, audit, base_snapshot=base, requested_payload=new_payload
            )
            assert not already
            new_cand.activation_state = "active"
            # the pointer moves WITHOUT closing the old candidate — the
            # repair runner must close the blocked backlog itself
            await db.execute(
                text("UPDATE runtime_config_heads SET active_config_snapshot_id = :n"),
                {"n": new_cand.id},
            )

        # the repair runner sees the pointer moved → closes the old
        # backlog as superseded and never returns it to active
        async with factory() as db:
            outcome = await act.run_activation_repair(
                db, AuditService(db), candidate=old_cand, batch_size=64
            )
        assert outcome.superseded and outcome.state == "superseded"
        assert await _state_of(engine, old_cand) == "superseded"
        assert await _pointer(engine) == new_cand.id
        sup = await _audit(engine, AuditEventType.ACTIVATION_SUPERSEDED.value)
        assert len(sup) == 1 and sup[0]["candidate_id"] == str(old_cand)
    finally:
        await engine.dispose()


# ─── 11. the scheduler's T_repair_admission gate ────────────────────────────


@pytest.mark.asyncio
async def test_scheduler_skips_wake_on_old_repair_backlog(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    from datetime import UTC, datetime

    _, engine = migrated_db
    try:
        factory = await _factory(engine)
        # a blocked candidate that owns the pointer (terminal cleanup done)
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        async with factory() as db, db.begin():
            audit = AuditService(db)
            head_row = (
                await db.execute(
                    text("SELECT active_config_snapshot_id FROM runtime_config_heads")
                )
            ).scalar_one()
            from packages.domain.models.config import ORMConfigSnapshot

            base = await db.get(ORMConfigSnapshot, head_row)
            assert base is not None
            cand, already = await act.upsert_online_candidate(
                db, audit, base_snapshot=base, requested_payload=payload
            )
            assert not already
            cand.activation_state = "post_publish_blocked"
            cand.post_publish_started_at = datetime.now(UTC)
            cand.post_publish_next_attempt_at = datetime.now(UTC)
            # the bootstrap stays 'active' (immutable CHECK) — the pointer
            # alone marks the effective snapshot
            await db.execute(
                text("UPDATE runtime_config_heads SET active_config_snapshot_id = :n"),
                {"n": cand.id},
            )

        # an OLD backlog (well past T_repair_admission = 7200 s)
        await _scalar(
            engine,
            "UPDATE config_snapshots SET post_publish_started_at = now() - interval '100 hours' "
            "WHERE id = :s",
            {"s": cand.id},
        )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "repair_backlog"
        skipped = await _audit(engine, "wake_skipped")
        assert any(s.get("reason") == "repair_backlog" for s in skipped)

        # a FRESH backlog does not block the wake (the repair runner is a
        # separate lane; the session lane waits only for an old backlog)
        await _scalar(
            engine,
            "UPDATE config_snapshots SET post_publish_started_at = now() - interval '1 hour' "
            "WHERE id = :s",
            {"s": cand.id},
        )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=NOW)
        assert decision.action == "wake"
    finally:
        await engine.dispose()


# ─── 12. the sealed-interval trigger ────────────────────────────────────────


@pytest.mark.asyncio
async def test_sealed_interval_freezes_shadow_heads(
    migrated_db: tuple[str, AsyncEngine]
) -> None:
    _, engine = migrated_db
    try:
        cids = await _seed_current_claims(engine, ["86", "87"])
        payload = _rules_payload(computed_result={"min_grade_for_supported": "E3"})
        factory = await _factory(engine)

        # drive the candidate to the sealed state (ready) step by step
        async with factory() as db:
            audit = AuditService(db)
            async with transaction(db):
                head_row = (
                    await db.execute(
                        text("SELECT active_config_snapshot_id FROM runtime_config_heads")
                    )
                ).scalar_one()
                from packages.domain.models.config import ORMConfigSnapshot

                base = await db.get(ORMConfigSnapshot, head_row)
                assert base is not None
                cand, _ = await act.upsert_online_candidate(
                    db, audit, base_snapshot=base, requested_payload=payload
                )
            fence = await act.acquire_activation(db, audit, candidate=cand)
            await act.freeze_cohort_online(db, candidate=cand, fence=fence, owner=act.ACTOR)
            await act.prepare_heads_online(db, candidate=cand, fence=fence, owner=act.ACTOR)
            digest = await act.verify_and_seal_online(
                db, candidate=cand, fence=fence, owner=act.ACTOR
            )
            assert digest
        assert await _state_of(engine, cand.id) == "ready"

        # while sealed (ready), the shadow heads are immutable (DB trigger)
        with pytest.raises(DBAPIError) as excinfo:
            await _scalar(
                engine,
                "UPDATE claim_assessment_heads SET prepared_by = 'hacker' "
                "WHERE config_snapshot_id = :c",
                {"c": cand.id},
            )
        assert getattr(excinfo.value.orig, "pgcode", None) == "45000"

        # the rebuild path: a conditional ready → preparing_heads return
        # is allowed by the trigger, and afterwards the heads are writable
        await _scalar(
            engine,
            "UPDATE config_snapshots SET activation_state = 'preparing_heads', "
            "activation_verified_at = NULL, activation_heads_sha256 = NULL, "
            "activation_verified_head_count = NULL "
            "WHERE id = :c AND activation_state = 'ready'",
            {"c": cand.id},
        )
        await _scalar(
            engine,
            "UPDATE claim_assessment_heads SET prepared_by = 'rules_activation' "
            "WHERE config_snapshot_id = :c",
            {"c": cand.id},
        )
        assert await _state_of(engine, cand.id) == "preparing_heads"
        for cid in cids:
            h = await _head(engine, cid, f"'{cand.id}'")
            assert h is not None
    finally:
        await engine.dispose()
