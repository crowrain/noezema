"""Scenario (DB): M7 web — diagnostics (T7.1, §22.1).

Covers: the aggregated diagnostics view (sessions, commit attempts,
barriers, jobs, activation slot, writer gate, revisions), the
reconciliation view (committing/reconciling_commit sessions + attempts +
checkpoints, unresolved flag), the reassessment job views (blocked with
error class, retry with next-attempt), and the invalidation barrier view
(closure progress + the immutable manifest).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.api import create_app

pytestmark = [pytest.mark.scenario]

BOOT = "(SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap')"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_app(scratch_url: str):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(engine=engine, factory=factory)
    return app, engine


async def _scalar(engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


async def _seed_session(
    engine: AsyncEngine, state: str, *, attempt_status: str | None = None
) -> uuid.UUID:
    sid = uuid.uuid4()
    await _scalar(
        engine,
        f"INSERT INTO sessions (id, state, config_snapshot_id) VALUES (:id, :s, {BOOT})",
        {"id": sid, "s": state},
    )
    if attempt_status is not None:
        await _scalar(
            engine,
            "INSERT INTO commit_attempts (id, session_id, status, staging_hash, "
            " base_knowledge_revision, base_dependency_graph_revision) "
            "VALUES (:id, :s, :st, :h, 3, 2)",
            {"id": uuid.uuid4(), "s": sid, "st": attempt_status, "h": "f" * 64},
        )
    return sid


async def _seed_checkpoint(engine: AsyncEngine, session_id: uuid.UUID) -> None:
    await _scalar(
        engine,
        "INSERT INTO checkpoints (id, session_id, knowledge_revision, "
        " dependency_graph_revision) VALUES (:id, :s, 3, 2)",
        {"id": uuid.uuid4(), "s": session_id},
    )


async def _seed_claim(engine: AsyncEngine, claim_id: uuid.UUID, statement: str) -> None:
    await _scalar(
        engine,
        "INSERT INTO claims (id, statement, claim_type, freshness_status) "
        "VALUES (:id, :s, 'computed_result', 'fresh')",
        {"id": claim_id, "s": statement},
    )


@pytest.mark.asyncio
async def test_diagnostics_summary_empty(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, _fixture_engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics")
            assert r.status_code == 200
            d = r.json()
            assert d["sessions"] == {}
            assert d["commit_attempts"] == {}
            assert d["unresolved_attempts"] == 0
            assert d["open_barriers"] == {}
            assert d["reassessment_jobs"] == {}
            assert d["blocked_jobs"] == []
            # the activation slot is empty on a fresh DB
            assert d["activation"]["activating_snapshot_id"] is None
            assert d["activation"]["fence"] == 0
            # the writer gate row exists but is free
            assert d["writer_gate"]["owner_kind"] is None
            assert d["writer_gate"]["owner_id"] is None
            assert d["revisions"]["knowledge"] >= 0
            assert d["revisions"]["dependency_graph"] >= 0
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_reconciliation_view(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, _fixture_engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        # a session frozen in reconciling_commit with a prepared attempt
        # (the unresolved state: it blocks wake and GC)
        sid = await _seed_session(engine, "reconciling_commit", attempt_status="prepared")
        await _seed_checkpoint(engine, sid)
        # a terminal session with an already-committed attempt: NOT
        # unresolved
        await _seed_session(engine, "succeeded", attempt_status="committed")
        # an exploring session with no attempt: not in the view at all
        await _seed_session(engine, "exploring")

        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics/reconciliation")
            assert r.status_code == 200
            d = r.json()
            assert d["unresolved"] is True
            assert len(d["entries"]) == 1
            e = d["entries"][0]
            assert e["session_id"] == str(sid)
            assert e["session_state"] == "reconciling_commit"
            assert e["attempt"] is not None
            assert e["attempt"]["status"] == "prepared"
            assert e["attempt"]["staging_hash"] == "f" * 64
            assert e["attempt"]["base_knowledge_revision"] == 3
            assert e["attempt"]["base_dependency_graph_revision"] == 2
            assert e["checkpoint"]["knowledge_revision"] == 3
            assert e["checkpoint"]["workspace_manifest_id"] is None

            # the summary carries the unresolved count
            r = await client.get("/api/v1/diagnostics")
            s = r.json()
            assert s["unresolved_attempts"] == 1
            assert s["commit_attempts"]["prepared"] == 1
            assert s["commit_attempts"]["committed"] == 1
            assert s["sessions"]["reconciling_commit"] == 1

        # after the attempt resolves the view is clean
        await _scalar(
            engine,
            "UPDATE commit_attempts SET status = 'committed', finished_at = now() "
            "WHERE session_id = :s",
            {"s": sid},
        )
        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics/reconciliation")
            d = r.json()
            assert d["unresolved"] is False
            assert len(d["entries"]) == 1  # the committed attempt still shows
            assert d["entries"][0]["attempt"]["status"] == "committed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_jobs_view(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, _fixture_engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        c_block = uuid.uuid5(uuid.NAMESPACE_URL, "diag-job-block")
        c_retry = uuid.uuid5(uuid.NAMESPACE_URL, "diag-job-retry")
        c_queued = uuid.uuid5(uuid.NAMESPACE_URL, "diag-job-queued")
        await _seed_claim(engine, c_block, "утверждение с blocked джобой")
        await _seed_claim(engine, c_retry, "утверждение с retry джобой")
        await _seed_claim(engine, c_queued, "утверждение с queued джобой")

        j_blocked = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, "
            " status, reason, priority, attempts, max_attempts, error_class, "
            " next_attempt_at, blocked_at) "
            f"VALUES (:id, :c, {BOOT}, 'blocked', 'cascade', 5, 78, 78, "
            " 'transient_db_error', now() + interval '1 hour', now())",
            {"id": j_blocked, "c": c_block},
        )
        j_retry = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, "
            " status, reason, priority, attempts, next_attempt_at) "
            f"VALUES (:id, :c, {BOOT}, 'retry', 'cascade', 1, 3, now() + interval '10 minutes')",
            {"id": j_retry, "c": c_retry},
        )
        await _scalar(
            engine,
            "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, "
            " status, reason) "
            f"VALUES (:id, :c, {BOOT}, 'queued', 'activation')",
            {"id": uuid.uuid4(), "c": c_queued},
        )

        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics/jobs")
            assert r.status_code == 200
            jobs = r.json()["jobs"]
            # priority DESC first: the blocked job (priority 5)
            assert jobs[0]["id"] == str(j_blocked)
            assert jobs[0]["status"] == "blocked"
            assert jobs[0]["error_class"] == "transient_db_error"
            assert jobs[0]["attempts"] == 78
            assert jobs[0]["max_attempts"] == 78
            assert jobs[0]["blocked_at"] is not None
            assert jobs[0]["claim_statement"] == "утверждение с blocked джобой"

            # the retry view: next-attempt scheduling is visible
            r = await client.get("/api/v1/diagnostics/jobs", params={"status": "retry"})
            retry = r.json()["jobs"]
            assert len(retry) == 1
            assert retry[0]["id"] == str(j_retry)
            assert retry[0]["next_attempt_at"] is not None
            assert retry[0]["attempts"] == 3

            # an unknown status is rejected
            r = await client.get("/api/v1/diagnostics/jobs", params={"status": "nope"})
            assert r.status_code == 422

            # the summary carries the per-status counts + the blocked list
            r = await client.get("/api/v1/diagnostics")
            s = r.json()
            assert s["reassessment_jobs"]["blocked"] == 1
            assert s["reassessment_jobs"]["retry"] == 1
            assert s["reassessment_jobs"]["queued"] == 1
            assert s["blocked_jobs"][0]["id"] == str(j_blocked)
            assert s["blocked_jobs"][0]["error_class"] == "transient_db_error"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_barriers_view(migrated_db: tuple[str, AsyncEngine]) -> None:
    scratch_url, _fixture_engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        root = uuid.uuid5(uuid.NAMESPACE_URL, "diag-barrier-root")
        await _seed_claim(engine, root, "корень инвалидации")

        # a closure manifest (content-addressed)
        cm_sha = "b" * 64
        cm = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO closure_manifests (id, root_claim_id, graph_revision, "
            " claim_ids, ranks, count, sha256) "
            "VALUES (:id, :r, 7, '[\"x\", \"y\"]', '{\"x\": 0, \"y\": 1}', 2, :s)",
            {"id": cm, "r": root, "s": cm_sha},
        )

        # an active barrier with partial closure progress
        b_active = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO dependency_invalidation_barriers (id, root_claim_id, "
            " graph_revision, generation, status, closure_manifest_id, "
            " member_count, next_offset) "
            "VALUES (:id, :r, 7, 1, 'active', :cm, 5, 2)",
            {"id": b_active, "r": root, "cm": cm},
        )
        # a blocked barrier: the CHECK requires a sealed manifest for any
        # status other than discovering — blocked happens after sealing
        cm2 = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO closure_manifests (id, root_claim_id, graph_revision, "
            " claim_ids, ranks, count, sha256) "
            "VALUES (:id, :r, 8, '[\"z\"]', '{\"z\": 0}', 1, :s)",
            {"id": cm2, "r": root, "s": "c" * 64},
        )
        b_blocked = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO dependency_invalidation_barriers (id, root_claim_id, "
            " graph_revision, generation, status, closure_manifest_id, "
            " member_count, next_offset, last_error) "
            "VALUES (:id, :r, 8, 2, 'blocked', :cm, 3, 1, 'lock timeout')",
            {"id": b_blocked, "r": root, "cm": cm2},
        )
        # a resolved barrier: hidden by default (resolved also carries a
        # sealed manifest — the CHECK ties the manifest to the status)
        cm3 = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO closure_manifests (id, root_claim_id, graph_revision, "
            " claim_ids, ranks, count, sha256) "
            "VALUES (:id, :r, 9, '[\"w\"]', '{\"w\": 0}', 1, :s)",
            {"id": cm3, "r": root, "s": "d" * 64},
        )
        b_resolved = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO dependency_invalidation_barriers (id, root_claim_id, "
            " graph_revision, generation, status, closure_manifest_id, "
            " member_count, next_offset, resolved_at) "
            "VALUES (:id, :r, 9, 3, 'resolved', :cm, 2, 2, now())",
            {"id": b_resolved, "r": root, "cm": cm3},
        )

        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics/barriers")
            assert r.status_code == 200
            barriers = r.json()["barriers"]
            assert len(barriers) == 2  # resolved is hidden
            by_id = {b["id"]: b for b in barriers}
            assert by_id[str(b_active)]["status"] == "active"
            assert by_id[str(b_active)]["closure_progress"] == "2/5"
            assert by_id[str(b_active)]["member_count"] == 5
            assert by_id[str(b_active)]["next_offset"] == 2
            assert by_id[str(b_active)]["closure_manifest"]["sha256"] == cm_sha
            assert by_id[str(b_active)]["closure_manifest"]["count"] == 2
            assert by_id[str(b_active)]["root_statement"] == "корень инвалидации"
            assert by_id[str(b_blocked)]["status"] == "blocked"
            assert by_id[str(b_blocked)]["last_error"] == "lock timeout"
            assert by_id[str(b_blocked)]["closure_manifest"]["sha256"] == "c" * 64
            assert by_id[str(b_blocked)]["closure_progress"] == "1/3"

            # include_resolved adds the resolved barrier
            r = await client.get(
                "/api/v1/diagnostics/barriers", params={"include_resolved": True}
            )
            ids = {b["id"]: b for b in r.json()["barriers"]}
            assert ids[str(b_resolved)]["status"] == "resolved"
            assert ids[str(b_resolved)]["resolved_at"] is not None

            # the summary aggregates the open barriers
            r = await client.get("/api/v1/diagnostics")
            s = r.json()
            assert s["open_barriers"]["active"]["n"] == 1
            assert s["open_barriers"]["active"]["members"] == 5
            assert s["open_barriers"]["active"]["closed"] == 2
            assert s["open_barriers"]["blocked"]["n"] == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_diagnostics_activation_slot_and_gate(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    scratch_url, _fixture_engine = migrated_db
    app, engine = await _make_app(scratch_url)
    try:
        candidate = uuid.uuid4()
        await _scalar(
            engine,
            "INSERT INTO config_snapshots (id, base_snapshot_id, payload_sha256, sha256, "
            " activation_mode, activation_state, model, embeddings, prompts, policy, "
            " curiosity, token_budgets, session_limits, activation_limits, claim_type_rules) "
            f"VALUES (:id, {BOOT}, :ps, :s, 'online', 'publishing', "
            "'{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}', '{}')",
            {"id": candidate, "ps": "e" * 64, "s": "f" * 64},
        )
        # occupy the activation slot + the writer gate
        await _scalar(
            engine,
            "UPDATE runtime_config_heads SET activating_config_snapshot_id = :c, "
            " activation_fence = 3, activation_lease_owner = 'diag-owner', "
            " activation_lease_expires_at = now() + interval '10 minutes', "
            " updated_at = now() WHERE scope = 'global'",
            {"c": candidate},
        )
        await _scalar(
            engine,
            "UPDATE knowledge_write_gate SET owner_kind = 'session', owner_id = 'sess-1', "
            " priority = 10, acquired_at = now(), "
            " lease_expires_at = now() + interval '5 minutes' WHERE scope = 'global'",
            {},
        )

        async with _client(app) as client:
            r = await client.get("/api/v1/diagnostics")
            d = r.json()
            assert d["activation"]["activating_snapshot_id"] == str(candidate)
            assert d["activation"]["fence"] == 3
            assert d["activation"]["lease_owner"] == "diag-owner"
            assert d["activation"]["lease_expires_at"] is not None
            assert d["writer_gate"]["owner_kind"] == "session"
            assert d["writer_gate"]["owner_id"] == "sess-1"
            assert d["writer_gate"]["priority"] == 10
    finally:
        await engine.dispose()
