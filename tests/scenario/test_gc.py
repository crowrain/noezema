"""Scenario (DB): T7.3 GC — the full §15.3 root set (§20.12).

«GC не удаляет root-reachable object» — every root class of §15.3 is
seeded and must survive an ``apply=True`` sweep; only retention-expired
root-free rows are deleted. Also: «При ``reconciling_commit`` GC
соответствующей сессии запрещён» (the session's own rows are skipped),
pinned objects are never candidates, and the dry-run deletes nothing.
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.db.uow import transaction
from packages.gc.service import run_gc

pytestmark = [pytest.mark.scenario]


async def _scalar(
    engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None
) -> Any:
    """Execute and return the single column of the first row (or None)."""
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        row = res.first() if res.returns_rows else None
        return row[0] if row is not None else None


async def _ins_async(
    engine: AsyncEngine, sql: str, params: dict[str, Any]
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(text(sql), params)


async def _seed_artifact_row(
    engine: AsyncEngine, *, tag: str, days_ago: int = 0
) -> uuid.UUID:
    """One artifact registry row (no live references unless wired)."""
    art_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class, created_at) "
        "VALUES (:id, :s, :n, 'text/plain', 'test', 'untrusted', "
        "CASE WHEN :d > 0 THEN now() - make_interval(days => :d) ELSE now() END)",
        {"id": art_id, "s": f"{tag}{'0' * 55}", "n": 10, "d": days_ago},
    )
    return art_id


async def _seed_session(
    engine: AsyncEngine, *, state: str, snapshot_id: uuid.UUID
) -> uuid.UUID:
    sid = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO sessions (id, state, config_snapshot_id) "
        "VALUES (:id, :st, :cs)",
        {"id": sid, "st": state, "cs": snapshot_id},
    )
    return sid


async def _bootstrap_snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    row = await _scalar(engine, "SELECT id FROM config_snapshots LIMIT 1")
    assert row is not None
    return row


async def _seed_workspace_manifest(
    engine: AsyncEngine, *, session_id: uuid.UUID | None, days_ago: int
) -> uuid.UUID:
    wm_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO workspace_manifests "
        "(id, session_id, root_sha256, entry_count, total_size, frozen_at) "
        "VALUES (:id, :s, :r, 0, 0, "
        "CASE WHEN :d > 0 THEN now() - make_interval(days => :d) ELSE now() END)",
        {"id": wm_id, "s": session_id, "r": "w" * 64, "d": days_ago},
    )
    return wm_id


async def _seed_commit_attempt(
    engine: AsyncEngine,
    *,
    session_id: uuid.UUID,
    status: str,
    days_ago: int = 0,
    manifest_id: uuid.UUID | None = None,
) -> uuid.UUID:
    ca_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO commit_attempts "
        "(id, session_id, status, staging_hash, workspace_manifest_id, "
        "base_knowledge_revision, base_dependency_graph_revision, finished_at) "
        "VALUES (:id, :s, :st, :h, :m, 0, 0, "
        "CASE WHEN :d > 0 THEN now() - make_interval(days => :d) ELSE NULL END)",
        {"id": ca_id, "s": session_id, "st": status, "h": "s" * 64, "m": manifest_id, "d": days_ago},
    )
    return ca_id


@pytest.mark.asyncio
async def test_gc_root_set_survives_apply_sweep(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """Every §15.3 root class survives; only the orphan expired row dies."""
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    snap = await _bootstrap_snapshot_id(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # (1) current domain FKs: evidence observation artifact + attestation
    live_artifact = await _seed_artifact_row(engine, tag="live")
    claim_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO claims (id, statement, claim_type) "
        "VALUES (:id, 's', 'local_observation')",
        {"id": claim_id},
    )
    # a computation evidence (the simplest valid shape: observation
    # artifact only) — its observation artifact is a GC root
    ev_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        "observation_artifact_id) "
        "VALUES (:id, :c, 'supports', 'computation', :h, :a)",
        {"id": ev_id, "c": claim_id, "h": "i" * 64, "a": live_artifact},
    )
    att_artifact = await _seed_artifact_row(engine, tag="att")
    await _ins_async(
        engine,
        "INSERT INTO operator_attestations (id, actor_id, claim_id, body) "
        "VALUES (:id, 'op', :c, 'b')",
        {"id": uuid.uuid4(), "c": claim_id},
    )
    await _ins_async(
        engine,
        "UPDATE operator_attestations SET supporting_artifact_id = :a "
        "WHERE claim_id = :c",
        {"a": att_artifact, "c": claim_id},
    )

    # (3) active staging/overlays: a non-terminal session with staging
    active_session = await _seed_session(engine, state="exploring", snapshot_id=snap)
    await _ins_async(
        engine,
        "INSERT INTO session_staging (id, session_id, op, payload, payload_hash) "
        "VALUES (:id, :s, 'claim', :p, :h)",
        {
            "id": uuid.uuid4(),
            "s": active_session,
            "p": '{"statement": "x", "claim_type": "fact"}',
            "h": "p" * 64,
        },
    )

    # (4) unresolved commit attempt + its workspace manifest
    uns_session = await _seed_session(engine, state="committing", snapshot_id=snap)
    uns_manifest = await _seed_workspace_manifest(engine, session_id=uns_session, days_ago=40)
    await _seed_commit_attempt(
        engine, session_id=uns_session, status="prepared", manifest_id=uns_manifest
    )

    # (5) reassessment/resolution basis artifact: a second evidence
    # (the resolution basis) with its own observation artifact
    basis_artifact = await _seed_artifact_row(engine, tag="basis")
    basis_ev_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        "observation_artifact_id) "
        "VALUES (:id, :c, 'counters', 'computation', :h, :a)",
        {"id": basis_ev_id, "c": claim_id, "h": "j" * 64, "a": basis_artifact},
    )
    await _ins_async(
        engine,
        "INSERT INTO counterevidence_resolutions "
        "(id, evidence_id, basis_evidence_id, actor, rules_version, valid) "
        "VALUES (:id, :e, :b, 'op', 'r1', true)",
        {"id": uuid.uuid4(), "e": ev_id, "b": basis_ev_id},
    )

    # (6) active barrier + closure manifest
    cm_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO closure_manifests (id, root_claim_id, graph_revision, "
        "claim_ids, ranks, count, sha256) "
        "VALUES (:id, :c, 0, :ids, :ranks, 1, :s)",
        {"id": cm_id, "c": claim_id, "ids": '["x"]', "ranks": '{"x": 1}', "s": "c" * 64},
    )
    await _ins_async(
        engine,
        "INSERT INTO dependency_invalidation_barriers "
        "(id, root_claim_id, graph_revision, generation, status, closure_manifest_id, "
        "member_count, next_offset) "
        "VALUES (:id, :c, 0, 1, 'active', :cm, 1, 0)",
        {"id": uuid.uuid4(), "c": claim_id, "cm": cm_id},
    )

    # (8) backup manifest in the retention window + its inventory
    # document (the inventory DOCUMENT is the root; the individual
    # objects it lists are restored via re-fetch by the drill, not by
    # the GC sweep — the sweep never deletes backups, only reports
    # the expired ones)
    inv_artifact = await _seed_artifact_row(engine, tag="inv")
    bm_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO backup_manifests (id, database_recovery_point, "
        "artifact_inventory_hash, artifact_inventory_artifact_id, retention_until) "
        "VALUES (:id, '0/1', :h, :a, now() + interval '30 days')",
        {"id": bm_id, "h": "b" * 64, "a": inv_artifact},
    )

    # the ORPHAN: an expired artifact with no references
    orphan = await _seed_artifact_row(engine, tag="orphan", days_ago=60)

    # pinned: an orphan that is pinned (must survive even if expired)
    pinned = await _seed_artifact_row(engine, tag="pinned", days_ago=60)
    await _ins_async(
        engine,
        "INSERT INTO gc_pinned (kind, object_id, reason) "
        "VALUES ('artifact', :id, 'legal hold')",
        {"id": str(pinned)},
    )

    # a terminal (committed) attempt past retention + its expired
    # workspace manifest (no checkpoint, no unresolved attempt)
    term_session = await _seed_session(engine, state="succeeded", snapshot_id=snap)
    dead_manifest = await _seed_workspace_manifest(engine, session_id=term_session, days_ago=40)
    await _seed_commit_attempt(
        engine, session_id=term_session, status="committed", days_ago=40, manifest_id=dead_manifest
    )
    # a checkpointed manifest of the same session: the checkpoint row
    # is the GC root for it (§15.3) — it must survive even though it is
    # past the retention window
    kept_manifest = await _seed_workspace_manifest(engine, session_id=term_session, days_ago=40)
    await _ins_async(
        engine,
        "INSERT INTO checkpoints (id, session_id, workspace_manifest_id, "
        "knowledge_revision, dependency_graph_revision) "
        "VALUES (:id, :s, :m, 0, 0)",
        {"id": uuid.uuid4(), "s": term_session, "m": kept_manifest},
    )

    # dry run first: nothing is deleted
    async with factory() as db, transaction(db):
        dry = await run_gc(db, apply=False)
    assert dry["root_artifact_count"] >= 5  # the live roots
    assert str(orphan) in dry["candidate_artifacts"]
    assert str(pinned) not in dry["candidate_artifacts"]  # pinned
    assert str(live_artifact) not in dry["candidate_artifacts"]
    assert str(att_artifact) not in dry["candidate_artifacts"]
    assert str(basis_artifact) not in dry["candidate_artifacts"]
    assert str(inv_artifact) not in dry["candidate_artifacts"]
    assert str(uns_manifest) not in dry["candidate_workspace_manifests"]  # unresolved attempt
    assert str(dead_manifest) in dry["candidate_workspace_manifests"]
    assert str(kept_manifest) not in dry["candidate_workspace_manifests"]  # checkpoint
    assert str(term_session) not in "".join(dry["reconciling_sessions"])

    # apply
    async with factory() as db, transaction(db):
        applied = await run_gc(db, artifact_store=store, apply=True)
    assert applied["deleted"].get("artifacts") == 1
    assert applied["deleted"].get("workspace_manifests") == 1
    assert applied["deleted"].get("commit_attempts") == 1
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM workspace_manifests WHERE id = :id", {"id": kept_manifest}
        )
        == 1
    )

    # the orphan is gone; every root survives (the pinned row is kept
    # by the pin, not by the root set)
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM artifacts WHERE id = :id", {"id": orphan}
        )
        == 0
    )
    for survivor in (live_artifact, att_artifact, basis_artifact, inv_artifact, pinned):
        assert (
            await _scalar(
                engine, "SELECT count(*)::int FROM artifacts WHERE id = :id", {"id": survivor}
            )
            == 1
        )
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM workspace_manifests WHERE id = :id", {"id": uns_manifest}
        )
        == 1
    )
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM workspace_manifests WHERE id = :id", {"id": dead_manifest}
        )
        == 0
    )
    assert (
        await _scalar(
            engine,
            "SELECT count(*)::int FROM dependency_invalidation_barriers WHERE status = 'active'",
        )
        == 1
    )
    # the audit is in the same transaction
    assert await _scalar(
        engine, "SELECT count(*)::int FROM audit_events WHERE type = 'gc_sweep'"
    ) >= 2


@pytest.mark.asyncio
async def test_gc_forbidden_for_reconciling_session(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """«При reconciling_commit GC соответствующей сессии запрещён»."""
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    snap = await _bootstrap_snapshot_id(engine)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    recon_session = await _seed_session(engine, state="reconciling_commit", snapshot_id=snap)
    recon_manifest = await _seed_workspace_manifest(engine, session_id=recon_session, days_ago=40)
    # its unresolved attempt
    await _seed_commit_attempt(
        engine, session_id=recon_session, status="prepared", manifest_id=recon_manifest
    )
    # an expired orphan of the SAME session (staging-era workspace)
    orphan_manifest = await _seed_workspace_manifest(engine, session_id=recon_session, days_ago=40)
    # a terminal committed attempt of the same session, past retention
    await _seed_commit_attempt(
        engine, session_id=recon_session, status="committed", days_ago=40
    )

    async with factory() as db, transaction(db):
        result = await run_gc(db, artifact_store=store, apply=True)

    assert result["reconciling_sessions"] == [str(recon_session)]
    assert str(orphan_manifest) not in result["candidate_workspace_manifests"]
    # the committed attempt of the reconciling session is not a candidate
    assert result["candidate_terminal_attempts"] == []
    # nothing of the session is deleted
    assert (
        await _scalar(
            engine,
            "SELECT count(*)::int FROM workspace_manifests WHERE id = :id",
            {"id": orphan_manifest},
        )
        == 1
    )
    assert (
        await _scalar(
            engine,
            "SELECT count(*)::int FROM commit_attempts WHERE session_id = :id",
            {"id": recon_session},
        )
        == 2
    )


@pytest.mark.asyncio
async def test_gc_dry_run_deletes_nothing(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch_url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orphan = await _seed_artifact_row(engine, tag="dry", days_ago=60)
    async with factory() as db, transaction(db):
        dry = await run_gc(db, apply=False)
    assert str(orphan) in dry["candidate_artifacts"]
    assert dry["deleted"] == {}
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM artifacts WHERE id = :id", {"id": orphan}
        )
        == 1
    )
    assert (
        await _scalar(
            engine,
            "SELECT count(*)::int FROM audit_events "
            "WHERE type = 'gc_sweep' AND payload->>'apply' = 'false'",
        )
        == 1
    )


@pytest.mark.asyncio
async def test_gc_reports_expired_backups_and_terminal_config_attempts(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch_url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # an expired backup manifest: reported, never deleted
    bm_id = uuid.uuid4()
    await _ins_async(
        engine,
        "INSERT INTO backup_manifests (id, database_recovery_point, "
        "artifact_inventory_hash, retention_until) "
        "VALUES (:id, '0/1', :h, now() - interval '1 days')",
        {"id": bm_id, "h": "e" * 64},
    )
    async with factory() as db, transaction(db):
        result = await run_gc(db, apply=True)
    assert str(bm_id) in result["expired_backup_manifests"]
    assert (
        await _scalar(
            engine, "SELECT count(*)::int FROM backup_manifests WHERE id = :id", {"id": bm_id}
        )
        == 1
    )
