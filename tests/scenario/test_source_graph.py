"""Scenario (DB): the full source graph (T4.7, §11.3, §14).

Covers: the versioned source-independence snapshot recorded on the
assessment (both paths — worker and session commit), the merge criteria
(same registrable domain, dependency edge, unknown lineage), and the
§11.3 cascade: a correction/edge that MERGES previously distinct groups
invalidates the dependent claim assessments and the worker recomputes
(the grade may drop supported → hypothesis); a SPLIT correction reopens
the groups and the grade comes back. Sources, edges and corrections are
seeded directly as the trusted host (the sanctioned writer).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.memory.reassessment import run_reassessment_batch
from packages.memory.service import MemoryService
from packages.memory.source_graph import apply_source_graph_change
from tests.unit.test_memory_service import _record_staging, _seed_session

pytestmark = [pytest.mark.scenario]

SNAP_SUBQUERY = "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"


async def _scalar(engine: AsyncEngine, sql: str, params: dict | None = None) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


async def _all(engine: AsyncEngine, sql: str, params: dict | None = None) -> list[Any]:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        return (await db.execute(text(sql), params or {})).all()


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    from sqlalchemy import select

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


async def _head(engine: AsyncEngine, claim_id: uuid.UUID) -> Any:
    return await _scalar(
        engine,
        "SELECT h.assessment_state, h.epistemic_status, h.current_assessment_id, a.effective_grade "
        "FROM claim_assessment_heads h "
        "LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )


async def _worker_reasons(engine: AsyncEngine, claim_id: uuid.UUID) -> list[str]:
    row = await _scalar(
        engine,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = 'reassessment_job_completed' "
        "AND payload->>'claim_id' = :c ORDER BY sequence DESC LIMIT 1",
        {"c": str(claim_id)},
    )
    assert row is not None, "no reassessment_job_completed audit for the claim"
    return list(row[0] or [])


async def _host_source(
    engine: AsyncEngine,
    *,
    uri: str | None = None,
    content_hash: str | None = None,
    parent: uuid.UUID | None = None,
) -> uuid.UUID:
    """A trusted-host-registered source (the retrieval module's rows)."""
    sid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata, "
        " parent_source_id) VALUES (:id, 'external_url', :uri, :ch, '{}', :p)",
        {"id": sid, "uri": uri, "ch": content_hash, "p": parent},
    )
    return sid


async def _host_source_evidence(
    engine: AsyncEngine, claim_id: uuid.UUID, source_id: uuid.UUID, *, tag: str
) -> None:
    """A trusted-host-registered source_assertion bound to the source
    (the CHECK: source_id + chunk_id are required)."""
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, '{\"x\": 1}', :s, :k)",
        {"id": uuid.uuid4(), "c": claim_id, "h": f"src-{tag}", "s": source_id, "k": f"chunk-{tag}"},
    )


async def _seed_claim_pending(
    engine: AsyncEngine, claim_id: uuid.UUID, statement: str, claim_type: str
) -> None:
    """A claim with a pending head + the old (invalid) assessment that
    carries the claim scope (the worker reads it back)."""
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, :t, 'fresh')"
            ),
            {"id": claim_id, "s": statement, "t": claim_type},
        )
        aid = uuid.uuid4()
        await db.execute(
            text(
                "INSERT INTO claim_assessments "
                "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', '{\"x\": 1}', 0.5, false)"
            ),
            {"a": aid, "c": claim_id},
        )
        await db.execute(
            text(
                "INSERT INTO claim_assessment_heads "
                "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                " epistemic_status, prepared_by) "
                f"VALUES (:c, {SNAP_SUBQUERY}, 'pending', NULL, NULL, 'rules_activation')"
            ),
            {"c": claim_id},
        )


async def _seed_job(engine: AsyncEngine, claim_id: uuid.UUID, reason: str = "test") -> uuid.UUID:
    jid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, status, "
        f" reason, priority) VALUES (:id, :c, {SNAP_SUBQUERY}, 'queued', :r, 0)",
        {"id": jid, "c": claim_id, "r": reason},
    )
    return jid


async def _run_worker(engine: AsyncEngine) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        await run_reassessment_batch(db)


async def _source_members(engine: AsyncEngine, claim_id: uuid.UUID) -> list[Any]:
    """(group_id, basis) per member of the head's CURRENT assessment's
    source-independence snapshot (older valid assessments keep their
    snapshots — the immutable history, T4.2 pattern)."""
    return await _all(
        engine,
        "SELECT m.group_id, m.basis FROM source_independence_members m "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = m.snapshot_id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )


async def _graph_change(
    engine: AsyncEngine, source_ids: frozenset[uuid.UUID], actor: str
) -> None:
    """The trusted-host cascade in one transaction (the graph rows are
    inserted by the test right before/inside the caller's tx)."""
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        result = await apply_source_graph_change(
            db, AuditService(db), source_ids=source_ids, actor=actor
        )
    assert result.invalidated >= 1, "the current head must be invalidated"
    assert result.jobs_created >= 1, "the recomputation job must be enqueued"


@pytest.mark.asyncio
async def test_independent_sources_lift_e3_via_worker(migrated_db: Any) -> None:
    """Two sources on different registrable domains: distinct groups —
    the external_fact reaches E3/supported, and the assessment fixes the
    versioned source-independence snapshot."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт подтверждён двумя изданиями", "external_fact")
    s1 = await _host_source(engine, uri="https://press.example.com/story")
    s2 = await _host_source(engine, uri="https://wire.other.org/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    jid = await _seed_job(engine, claim_id)

    await _run_worker(engine)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    head = await _head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "supported"
    assert head[3] == "E3"

    members = await _source_members(engine, claim_id)
    assert len(members) == 2
    assert len({m[0] for m in members}) == 2  # distinct groups

    snap = await _scalar(
        engine,
        "SELECT s.algorithm_version, s.thresholds, s.psl_fingerprint, s.uri_normalizer_version "
        "FROM claim_assessments a "
        "JOIN source_independence_snapshots s ON s.id = a.source_independence_snapshot_id "
        "WHERE a.claim_id = :c AND a.valid",
        {"c": claim_id},
    )
    assert snap is not None
    assert snap[0] == "independence-v2"
    assert snap[1] == {"text_overlap": 0.8}
    assert snap[2] and snap[3] == "uri-normalizer-v1"


@pytest.mark.asyncio
async def test_same_domain_mirrors_are_one_group(migrated_db: Any) -> None:
    """One document mirrored across subdomains of the same registrable
    domain: ONE group — one document, N mirrors, is not independent
    evidence (§11.3 merge criterion 1)."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт из одного издания (зеркала)", "external_fact")
    s1 = await _host_source(engine, uri="https://a.example.com/story")
    s2 = await _host_source(engine, uri="https://www.b.example.com/story/")
    await _host_source_evidence(engine, claim_id, s1, tag="m1")
    await _host_source_evidence(engine, claim_id, s2, tag="m2")
    jid = await _seed_job(engine, claim_id)

    await _run_worker(engine)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    members = await _source_members(engine, claim_id)
    assert len(members) == 2
    assert len({m[0] for m in members}) == 1  # one group: same registrable domain
    assert all(m[1].startswith("domain:example.com") for m in members)

    head = await _head(engine, claim_id)
    assert head[1] == "hypothesis"
    assert "insufficient_independence" in await _worker_reasons(engine, claim_id)


@pytest.mark.asyncio
async def test_merge_correction_cascades_recompute(migrated_db: Any) -> None:
    """§11.3 cascade: a correction that MERGES previously distinct groups
    invalidates the dependent claim assessment; the worker recomputes and
    the grade drops supported → hypothesis."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт, источники которого слили", "external_fact")
    s1 = await _host_source(engine, uri="https://alpha.net/story")
    s2 = await _host_source(engine, uri="https://beta.org/story")
    await _host_source_evidence(engine, claim_id, s1, tag="x")
    await _host_source_evidence(engine, claim_id, s2, tag="y")
    await _seed_job(engine, claim_id)

    # phase A: two independent sources → supported E3
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "supported" and head[3] == "E3"
    first_snapshot = (
        await _scalar(
            engine,
            "SELECT a.source_independence_snapshot_id FROM claim_assessments a "
            "WHERE a.claim_id = :c AND a.valid",
            {"c": claim_id},
        )
    )[0]
    assert first_snapshot is not None

    # phase B: the operator correction merges the two sources (the
    # trusted host writes the row + runs the cascade, one transaction)
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
    assert result.invalidated == 1
    assert result.jobs_created == 1
    assert result.revision == 1  # the source_graph domain revision bumps

    # the head is pending with the NULL pair (lifecycle invariant)
    head = await _head(engine, claim_id)
    assert head[0] == "pending"
    assert head[2] is None

    # the cascade job is the one the worker must pick up
    job = await _scalar(
        engine,
        "SELECT reason FROM reassessment_jobs WHERE claim_id = :c "
        "ORDER BY enqueued_at DESC LIMIT 1",
        {"c": claim_id},
    )
    assert job[0] == "source_graph_change"

    # phase C: the worker recomputes — one group now, hypothesis
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "hypothesis"
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 1  # merged into one group
    assert all(m[1] == "correction:merge" for m in members)
    # a FRESH snapshot: the old assessment's snapshot is not reused
    new_row = await _scalar(
        engine,
        "SELECT a.source_independence_snapshot_id "
        "FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )
    assert new_row[0] is not None
    assert new_row[0] != first_snapshot

    # the audit carries the change (actor + revision)
    audit = await _scalar(
        engine,
        "SELECT payload->>'jobs_created', payload->>'source_graph_revision' "
        "FROM audit_events WHERE type = 'source_graph_changed' ORDER BY sequence DESC LIMIT 1",
    )
    assert audit is not None
    assert audit[0] == "1" and audit[1] == "1"


@pytest.mark.asyncio
async def test_split_correction_reopens_groups(migrated_db: Any) -> None:
    """The correction of a false merge: a SPLIT correction on the pair
    cancels the direct dependency edge — the groups separate and the
    grade comes back to supported E3."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт с ошибочным слиянием источников", "external_fact")
    s1 = await _host_source(engine, uri="https://gamma.io/story")
    s2 = await _host_source(engine, uri="https://delta.dev/story")
    await _host_source_evidence(engine, claim_id, s1, tag="z")
    await _host_source_evidence(engine, claim_id, s2, tag="w")
    # the (false) dependency edge: s2 derives from s1
    await _scalar(
        engine,
        "INSERT INTO source_dependency_edges (id, from_source_id, to_source_id, kind, origin) "
        "VALUES (:id, :f, :t, 'derived_from', 'retrieval-v1')",
        {"id": uuid.uuid4(), "f": s2, "t": s1},
    )
    await _seed_job(engine, claim_id)

    # phase A: the edge merges the sources → one group → hypothesis
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "hypothesis"
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 1
    assert any(m[1].startswith("edge:derived_from") for m in members)

    # phase B: the operator corrects the false merge
    await _scalar(
        engine,
        "INSERT INTO source_graph_corrections "
        "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
        "VALUES (:id, :a, 'split', :f, :t, 'rules-v1', true)",
        {"id": uuid.uuid4(), "a": "operator-2", "f": s2, "t": s1},
    )
    await _graph_change(engine, frozenset({s1, s2}), "operator-2")

    # phase C: the groups separate again → supported E3
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "supported"
    assert head[3] == "E3"
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 2


@pytest.mark.asyncio
async def test_unknown_lineage_sources_are_not_independent(migrated_db: Any) -> None:
    """Two sources with no URI and no content hash: the conservative
    shared unknown group — never a false independence."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт из источников без провенанса", "external_fact")
    s1 = await _host_source(engine)
    s2 = await _host_source(engine)
    await _host_source_evidence(engine, claim_id, s1, tag="u1")
    await _host_source_evidence(engine, claim_id, s2, tag="u2")
    jid = await _seed_job(engine, claim_id)

    await _run_worker(engine)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    members = await _source_members(engine, claim_id)
    assert len({m[0] for m in members}) == 1
    assert all(m[1] == "unknown_lineage" for m in members)
    head = await _head(engine, claim_id)
    assert head[1] == "hypothesis"


@pytest.mark.asyncio
async def test_staging_commit_records_source_snapshot(migrated_db: Any) -> None:
    """The SESSION commit path: re-committing a claim whose evidence is
    source-based (host-seeded) builds the source-independence snapshot in
    the same assessment — source_independence_snapshot_id is fixed."""
    _url, engine = migrated_db
    statement = "факт, перечитанный сессией"
    claim_id = uuid.uuid4()
    # host-seeded claim + two independent sources (no head yet: the
    # session commit creates it)
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :s, 'external_fact', 'fresh')"
            ),
            {"id": claim_id, "s": statement},
        )
    s1 = await _host_source(engine, uri="https://one.press/story")
    s2 = await _host_source(engine, uri="https://two.media/story")
    await _host_source_evidence(engine, claim_id, s1, tag="v1")
    await _host_source_evidence(engine, claim_id, s2, tag="v2")

    # the session re-commits the same claim (exact statement+type dedup)
    _, sid = await _seed_session(engine)
    snap = await _snapshot(engine)
    await _record_staging(
        engine,
        sid,
        [("claim", {"statement": statement, "claim_type": "external_fact", "scope": {"x": 1}})],
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, [])
    assert result.problems == ()
    assert result.assessments == 1

    head = await _head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "supported"
    assert head[3] == "E3"
    members = await _source_members(engine, claim_id)
    assert len(members) == 2
    assert len({m[0] for m in members}) == 2

    # the audit payload carries the snapshot id
    audit = await _scalar(
        engine,
        "SELECT payload->>'source_independence_snapshot_id' FROM audit_events "
        "WHERE type = 'claim_assessed' AND payload->>'claim_id' = :c "
        "ORDER BY sequence DESC LIMIT 1",
        {"c": str(claim_id)},
    )
    assert audit is not None
    assert audit[0] is not None
    snap_ids = await _all(
        engine,
        "SELECT a.source_independence_snapshot_id FROM claim_assessments a "
        "WHERE a.claim_id = :c AND a.valid",
        {"c": claim_id},
    )
    assert len(snap_ids) == 1
    assert snap_ids[0][0] is not None
