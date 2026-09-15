"""Scenario (DB): environment manifests + environment independence
(T4.6, §8.7.3).

Covers: the FULL §14 manifest fields on the session commit path
(content-addressed over the field set — two sessions in the same
environment share ONE manifest), the versioned independence snapshot
recorded on the assessment (distinct groups, never hashes), and the
relation semantics end-to-end: repeatability (same method, same
execution environment, different data instance), reproducibility
(same method, different hardware — NOT independent), and
independent replication (independent implementation — the only
relation that may lift a grade to E3, via the worker path).
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.canonical import canonical_sha256
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.memory.evidence import (
    NOEZEMA_IMPLEMENTATION_HASH,
    manifest_content_hash,
    session_environment_fields,
    tool_fingerprint,
)
from packages.memory.reassessment import run_reassessment_batch
from packages.memory.service import MemoryService
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
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


def _obs_record(content: str) -> Any:
    from packages.domain.models.enums import EvidenceKind
    from packages.domain.schemas.evidence import EvidenceRecord

    return EvidenceRecord(
        kind=EvidenceKind.LOCAL_OBSERVATION,
        identity_hash="h" * 32,
        payload={"content": content},
    )


async def _commit_observation(
    engine: AsyncEngine,
    statement: str,
    content: str,
    claim_type: str = "local_observation",
) -> None:
    """One full session commit (staging → fenced apply) with one
    local_observation evidence."""
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)
    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": statement, "claim_type": claim_type, "scope": {"x": 1}}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, [_obs_record(content)])
    assert result.problems == ()
    assert result.assessments == 1


async def _host_manifest(
    engine: AsyncEngine,
    *,
    protocol: str = "proto-1",
    implementation: str = "impl-1",
    dataset_lineage: str | None = None,
    dataset_hash: str | None = None,
    hardware: str = "hw-1",
    seed: int | None = None,
) -> uuid.UUID:
    """A trusted-host-registered environment manifest (full §14 fields)."""
    fields: JsonDict = {
        "protocol_hash": protocol,
        "implementation_hash": implementation,
        "code_lineage": None,
        "dataset_hash": dataset_hash,
        "dataset_lineage": dataset_lineage,
        "toolchain_hash": "tools-1",
        "dependency_hash": None,
        "runtime_hash": "py-3.11",
        "hardware_hash": hardware,
        "seed": seed,
        "data_order_hash": None,
        "normalizer_version": "env-v2",
    }
    mid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO environment_manifests "
        "(id, protocol_hash, implementation_hash, code_lineage, dataset_hash, "
        " dataset_lineage, toolchain_hash, dependency_hash, runtime_hash, "
        " hardware_hash, seed, data_order_hash, normalizer_version, manifest_hash) "
        "VALUES (:id, :p, :i, :cl, :dh, :dl, :tc, :dep, :rt, :hw, :sd, :do, 'env-v2', :mh)",
        {
            "id": mid,
            "p": protocol,
            "i": implementation,
            "cl": None,
            "dh": dataset_hash,
            "dl": dataset_lineage,
            "tc": "tools-1",
            "dep": None,
            "rt": "py-3.11",
            "hw": hardware,
            "sd": seed,
            "do": None,
            "mh": manifest_content_hash(fields),
        },
    )
    return mid


async def _host_experiment_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    manifest_id: uuid.UUID,
    *,
    tag: str,
    scope: str = '{"gpu": "any"}',
    kind: str = "experiment_run",
) -> None:
    """A trusted-host-registered experiment/observation evidence bound
    to a manifest (the CHECK: observation artifact + manifest are
    required)."""
    art = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO artifacts (id, sha256, size, trust_class) VALUES (:a, :sha, 10, 'session_workspace')",
        {"a": art, "sha": f"exp-{tag}-artifact"},
    )
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, observation_artifact_id, environment_manifest_id) "
        "VALUES (:id, :c, 'supports', :k, :h, :sc, :a, :m)",
        {"id": uuid.uuid4(), "c": claim_id, "k": kind, "h": f"exp-{tag}", "sc": scope, "a": art, "m": manifest_id},
    )


async def _worker_reasons(engine: AsyncEngine, claim_id: uuid.UUID) -> list[str]:
    """The audited reasons of the worker's REASSESSMENT_JOB_COMPLETED
    event (reasons live in the audit payload, not on the assessment
    row)."""
    row = await _scalar(
        engine,
        "SELECT payload->'reasons' FROM audit_events "
        "WHERE type = 'reassessment_job_completed' "
        "AND payload->>'claim_id' = :c ORDER BY sequence DESC LIMIT 1",
        {"c": str(claim_id)},
    )
    assert row is not None, "no reassessment_job_completed audit for the claim"
    return list(row[0] or [])


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
                "VALUES (:a, :c, 'E2', 'supported', 'rules-v1', 'h', 'e', '{\"gpu\": \"any\"}', 0.5, false)"
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


async def _seed_job(engine: AsyncEngine, claim_id: uuid.UUID) -> uuid.UUID:
    jid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO reassessment_jobs (id, claim_id, target_config_snapshot_id, status, "
        f" reason, priority) VALUES (:id, :c, {SNAP_SUBQUERY}, 'queued', 'test', 0)",
        {"id": jid, "c": claim_id},
    )
    return jid


@pytest.mark.asyncio
async def test_session_commit_records_full_manifest_and_snapshot(migrated_db: Any) -> None:
    """The staging path registers the FULL §14 manifest (content-
    addressed) and the assessment fixes the independence snapshot."""
    _url, engine = migrated_db
    await _commit_observation(engine, "наблюдение: 2+2=4 в окружении A", "content-A")

    manifests = await _all(
        engine,
        "SELECT protocol_hash, implementation_hash, runtime_hash, hardware_hash, seed, "
        " normalizer_version, manifest_hash, dataset_lineage FROM environment_manifests",
    )
    assert len(manifests) == 1
    protocol_hash, impl, runtime, hardware, seed, normalizer, mh, lineage = manifests[0]
    assert impl == NOEZEMA_IMPLEMENTATION_HASH
    assert runtime == tool_fingerprint()
    assert hardware and len(hardware) == 64
    assert seed == 42  # the bootstrap sampling seed — part of the environment
    assert normalizer == "env-v2"
    assert lineage is None  # a session run has no dataset
    snap = await _snapshot(engine)
    expected_protocol = canonical_sha256(dict(snap.prompts or {}))
    assert protocol_hash == expected_protocol
    # content-addressed: the hash covers the full field set
    expected = manifest_content_hash(
        session_environment_fields(protocol_hash=expected_protocol, tool_schema_hash="", seed=42)
    )
    assert mh == expected

    # the assessment fixed a snapshot with one member (single manifest)
    rows = await _all(
        engine,
        "SELECT a.environment_independence_snapshot_id, s.algorithm_version, "
        " m.group_id, m.relation, m.basis "
        "FROM claim_assessments a "
        "JOIN environment_independence_snapshots s ON s.id = a.environment_independence_snapshot_id "
        "JOIN environment_independence_members m ON m.snapshot_id = s.id",
    )
    assert len(rows) == 1
    snapshot_id, algo, group, relation, basis = rows[0]
    assert snapshot_id is not None
    assert algo == "env-independence-v1"
    assert group.startswith("envgrp:")
    assert (relation, basis) == ("none", "single")


@pytest.mark.asyncio
async def test_two_sessions_same_environment_share_one_manifest(migrated_db: Any) -> None:
    """§8.7.3: the session identity is NOT part of the environment — two
    sessions under the same protocol/implementation/runtime/hardware
    share ONE content-addressed manifest (their repeats are not
    independent evidence)."""
    _url, engine = migrated_db
    await _commit_observation(engine, "одна и та же гипотеза", "content-1")
    await _commit_observation(engine, "одна и та же гипотеза", "content-2")

    count = await _scalar(engine, "SELECT count(*) FROM environment_manifests")
    assert count[0] == 1
    ev_count = await _scalar(engine, "SELECT count(*) FROM evidence")
    assert ev_count[0] == 2  # different content — two distinct evidence rows
    # ...but one environment, one group, no pair: no false independence.
    # Two assessments → two snapshots (one per commit), each with exactly
    # ONE member (one manifest), the same group, no relation
    snaps = await _scalar(engine, "SELECT count(*) FROM environment_independence_snapshots")
    assert snaps[0] == 2
    rows = await _all(
        engine,
        "SELECT m.group_id, m.relation, "
        " (SELECT count(*) FROM environment_independence_members x "
        "  WHERE x.snapshot_id = m.snapshot_id) AS n "
        "FROM environment_independence_members m",
    )
    assert len(rows) == 2
    assert len({r[0] for r in rows}) == 1  # one group across both snapshots
    assert all(r[1] == "none" and r[2] == 1 for r in rows)


@pytest.mark.asyncio
async def test_repeatability_pair_same_environment_different_data_instance(
    migrated_db: Any,
) -> None:
    """Two manifests with the same method and the same execution
    environment but different data instances (same lineage): a
    repeatability pair — stability, never independence."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "процедура воспроизводится", "local_observation")
    m1 = await _host_manifest(engine, dataset_lineage="ds-x", dataset_hash="inst-1")
    m2 = await _host_manifest(engine, dataset_lineage="ds-x", dataset_hash="inst-2")
    await _host_experiment_evidence(engine, claim_id, m1, tag="r1", kind="local_observation")
    await _host_experiment_evidence(engine, claim_id, m2, tag="r2", kind="local_observation")
    jid = await _seed_job(engine, claim_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        await run_reassessment_batch(db)

    job = await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid})
    assert job[0] == "completed"
    rows = await _all(
        engine,
        "SELECT m.group_id, m.relation, m.basis FROM environment_independence_members m "
        "JOIN claim_assessments a ON a.environment_independence_snapshot_id = m.snapshot_id "
        "WHERE a.claim_id = :c",
        {"c": claim_id},
    )
    assert len(rows) == 2
    groups = {r[0] for r in rows}
    assert len(groups) == 1  # one group: same method + data lineage
    assert all(r[1] == "repeatability" for r in rows)
    # repeatability is NOT independence: the head must not claim it
    head = await _scalar(
        engine,
        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade "
        "FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )
    assert head[0] == "current"
    # local_observation: E2 supported (one group is enough for it), but
    # the grade is exactly the rule minimum — repeatability lifts nothing
    assert head[2] == "E2"


@pytest.mark.asyncio
async def test_reproducibility_is_not_independent_replication(migrated_db: Any) -> None:
    """Same method on a DIFFERENT GPU: a reproducibility pair (portability
    inside the scope) — but §8.7.3: it never creates an independent
    group, so an empirical_conjecture stays a hypothesis."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "эффект переносится на другой GPU", "empirical_conjecture")
    m1 = await _host_manifest(engine, hardware="hw-cpu")
    m2 = await _host_manifest(engine, hardware="hw-gpu")
    await _host_experiment_evidence(engine, claim_id, m1, tag="p1")
    await _host_experiment_evidence(engine, claim_id, m2, tag="p2")
    jid = await _seed_job(engine, claim_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        await run_reassessment_batch(db)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    rows = await _all(
        engine,
        "SELECT m.group_id, m.relation FROM environment_independence_members m "
        "JOIN claim_assessments a ON a.environment_independence_snapshot_id = m.snapshot_id "
        "WHERE a.claim_id = :c",
        {"c": claim_id},
    )
    assert len(rows) == 2
    assert len({r[0] for r in rows}) == 1  # same method — ONE group
    assert all(r[1] == "reproducibility" for r in rows)

    head = await _scalar(
        engine,
        "SELECT h.assessment_state, h.epistemic_status FROM claim_assessment_heads h "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )
    # the head is current with the hypothesis verdict — and the audited
    # reason names the missing independence (one group: same method),
    # not a generic failure
    assert head[1] == "hypothesis"
    assert "insufficient_independence" in await _worker_reasons(engine, claim_id)


@pytest.mark.asyncio
async def test_independent_replication_lifts_e3(migrated_db: Any) -> None:
    """Independently implemented protocol/implementation: distinct
    groups + the independent_replication relation — the only path to
    E3 for an empirical_conjecture (§8.7.3)."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "эффект подтверждается независимо", "empirical_conjecture")
    m1 = await _host_manifest(engine, implementation="impl-team-a")
    m2 = await _host_manifest(engine, implementation="impl-team-b")
    await _host_experiment_evidence(engine, claim_id, m1, tag="i1")
    await _host_experiment_evidence(engine, claim_id, m2, tag="i2")
    jid = await _seed_job(engine, claim_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        await run_reassessment_batch(db)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    rows = await _all(
        engine,
        "SELECT m.group_id, m.relation, m.basis FROM environment_independence_members m "
        "JOIN claim_assessments a ON a.environment_independence_snapshot_id = m.snapshot_id "
        "WHERE a.claim_id = :c",
        {"c": claim_id},
    )
    assert len(rows) == 2
    assert len({r[0] for r in rows}) == 2  # distinct groups
    assert all(r[1] == "independent_replication" for r in rows)
    assert all(r[2].startswith("pair:") for r in rows)

    head = await _scalar(
        engine,
        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade, a.confidence "
        "FROM claim_assessment_heads h "
        "JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE h.claim_id = :c",
        {"c": claim_id},
    )
    assert head[0] == "current"
    assert head[1] == "supported"
    assert head[2] == "E3"


@pytest.mark.asyncio
async def test_shared_dataset_lineage_blocks_independence(migrated_db: Any) -> None:
    """Different implementations but the SAME known dataset lineage: not
    an independent replication (where the claim depends on data, the
    data lineage must be independent too)."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "эффект на общих данных", "empirical_conjecture")
    m1 = await _host_manifest(engine, implementation="impl-team-a", dataset_lineage="ds-shared")
    m2 = await _host_manifest(engine, implementation="impl-team-b", dataset_lineage="ds-shared")
    await _host_experiment_evidence(engine, claim_id, m1, tag="s1")
    await _host_experiment_evidence(engine, claim_id, m2, tag="s2")
    jid = await _seed_job(engine, claim_id)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, transaction(db):
        await run_reassessment_batch(db)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    rows = await _all(
        engine,
        "SELECT m.relation FROM environment_independence_members m "
        "JOIN claim_assessments a ON a.environment_independence_snapshot_id = m.snapshot_id "
        "WHERE a.claim_id = :c",
        {"c": claim_id},
    )
    assert all(r[0] == "variation" for r in rows)
    head = await _scalar(
        engine,
        "SELECT h.epistemic_status FROM claim_assessment_heads h WHERE h.claim_id = :c",
        {"c": claim_id},
    )
    assert head[0] == "hypothesis"
    assert "independence_independent_replication_not_met" in await _worker_reasons(engine, claim_id)
