"""Scenario (DB): counterevidence resolutions (T4.8, §8.7.4, §14).

Covers: an unresolved counter caps the claim (disputed ≤E1); a valid
resolution (evidence basis or source-graph correction basis) cascades
the recomputation and the grade comes back; the deterministic
invariants (XOR, uniqueness, counter-relation target, scope
compatibility, no transitive dependency); invalidating a resolution —
or the correction under it — makes the counter unresolved again and
cascades back to disputed. Resolutions are written by the trusted host
(the sanctioned curator path in v1).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.services.audit import AuditService
from packages.memory.resolutions import (
    ResolutionError,
    apply_counter_resolution,
    invalidate_counter_resolution,
    invalidate_resolutions_for_correction,
)
from tests.scenario.test_source_graph import (
    _all,
    _head,
    _host_source,
    _host_source_evidence,
    _run_worker,
    _scalar,
    _seed_claim_pending,
    _seed_job,
    _worker_reasons,
)

pytestmark = [pytest.mark.scenario]


async def _host_counter_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    source_id: uuid.UUID,
    *,
    scope: dict | None = None,
    tag: str = "c",
) -> uuid.UUID:
    """A trusted-host-registered counters evidence bound to a source."""
    eid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'counters', 'source_assertion', :h, :s, :src, :k)",
        {
            "id": eid,
            "c": claim_id,
            "h": f"cnt-{tag}",
            "s": json.dumps(scope or {"x": 1}),
            "src": source_id,
            "k": f"chunk-{tag}",
        },
    )
    return eid


async def _host_basis_evidence(
    engine: AsyncEngine,
    claim_id: uuid.UUID,
    *,
    scope: dict | None = None,
    tag: str = "b",
) -> uuid.UUID:
    """A trusted-host-registered support evidence on ANOTHER claim —
    the resolution basis (explains the divergence / out-of-scope
    counterexample). source_assertion requires a source (CHECK), so the
    helper registers a basis source on a distinct domain."""
    src = await _host_source(engine, uri=f"https://basis-{tag}.example.org/story")
    eid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
        " scope, source_id, chunk_id) "
        "VALUES (:id, :c, 'supports', 'source_assertion', :h, :s, :src, :k)",
        {
            "id": eid,
            "c": claim_id,
            "h": f"basis-{tag}",
            "s": json.dumps(scope or {"x": 1, "note": "out of scope"}),
            "src": src,
            "k": f"chunk-{tag}",
        },
    )
    return eid


async def _host_correction(
    engine: AsyncEngine,
    from_id: uuid.UUID,
    to_id: uuid.UUID,
    *,
    kind: str = "merge",
    valid: bool = True,
) -> uuid.UUID:
    cid = uuid.uuid4()
    await _scalar(
        engine,
        "INSERT INTO source_graph_corrections "
        "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
        "VALUES (:id, 'operator', :k, :f, :t, 'rules-v1', :v)",
        {"id": cid, "k": kind, "f": from_id, "t": to_id, "v": valid},
    )
    return cid


async def _in_tx(engine: AsyncEngine, fn) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        return await fn(db)


@pytest.mark.asyncio
async def test_unresolved_counter_disputes(migrated_db: Any) -> None:
    """Two independent sources + one unresolved counter: disputed E1,
    reason counterevidence_unresolved (§3.7)."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт с контрпримером", "external_fact")
    s1 = await _host_source(engine, uri="https://one.press/story")
    s2 = await _host_source(engine, uri="https://two.media/story")
    s3 = await _host_source(engine, uri="https://critic.third.net/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    await _host_counter_evidence(engine, claim_id, s3)
    jid = await _seed_job(engine, claim_id)

    await _run_worker(engine)

    assert (await _scalar(engine, "SELECT status FROM reassessment_jobs WHERE id = :j", {"j": jid}))[0] == "completed"
    head = await _head(engine, claim_id)
    assert head[1] == "disputed"
    assert head[3] == "E1"
    assert "counterevidence_unresolved" in await _worker_reasons(engine, claim_id)


@pytest.mark.asyncio
async def test_evidence_basis_resolution_lifts_to_supported(migrated_db: Any) -> None:
    """A valid resolution (evidence basis on another claim, scope
    compatible, not dependent) → cascade → the counter no longer caps:
    supported E3 with the counterevidence_resolved reason."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт, контрпример вне scope", "external_fact")
    s1 = await _host_source(engine, uri="https://alpha.co/story")
    s2 = await _host_source(engine, uri="https://beta.io/story")
    s3 = await _host_source(engine, uri="https://gamma.dev/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    counter_id = await _host_counter_evidence(engine, claim_id, s3, scope={"x": 1})

    # phase A: disputed
    await _seed_job(engine, claim_id)
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "disputed" and head[3] == "E1"

    # the basis: support evidence on ANOTHER claim, scope covers the
    # counter's scope
    basis_claim = uuid.uuid4()
    await _seed_claim_pending(engine, basis_claim, "контрпример вне scope", "external_fact")
    basis_id = await _host_basis_evidence(engine, basis_claim, scope={"x": 1, "note": "out"})

    result = await _in_tx(
        engine,
        lambda db: apply_counter_resolution(
            db,
            AuditService(db),
            evidence_id=counter_id,
            basis_evidence_id=basis_id,
            actor="operator-1",
        ),
    )
    assert result.invalidated == 1
    assert result.job_created is True
    assert result.claim_id == claim_id

    # cascade state: head pending + the job is queued
    head = await _head(engine, claim_id)
    assert head[0] == "pending"
    job = await _scalar(
        engine,
        "SELECT reason FROM reassessment_jobs WHERE claim_id = :c "
        "ORDER BY enqueued_at DESC LIMIT 1",
        {"c": claim_id},
    )
    assert job[0] == "counter_resolution_change"

    # phase B: the worker re-grades — the counter is resolved
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[0] == "current"
    assert head[1] == "supported"
    assert head[3] == "E3"
    reasons = await _worker_reasons(engine, claim_id)
    assert "requirements_met" in reasons
    assert "counterevidence_resolved" in reasons

    # the audit carries the basis + actor
    audit = await _scalar(
        engine,
        "SELECT actor, payload->>'basis_evidence_id', payload->>'basis_kind' "
        "FROM audit_events WHERE type = 'counter_resolution_created' ORDER BY sequence DESC LIMIT 1",
    )
    assert audit is not None
    assert audit[0] == "operator-1"
    assert audit[1] == str(basis_id)
    assert audit[2] == "evidence"


@pytest.mark.asyncio
async def test_resolution_invariants_reject(migrated_db: Any) -> None:
    """The deterministic invariants (§8.7.4, spec lines 2141–2147):
    XOR, counter-relation target, scope compatibility, no transitive
    dependency, uniqueness — every violation is rejected BEFORE the
    write (no invalid row)."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт для проверки инвариантов", "external_fact")
    s1 = await _host_source(engine, uri="https://r1.net/story")
    s2 = await _host_source(engine, uri="https://r2.org/story")
    s3 = await _host_source(engine, uri="https://r3.io/story")
    await _host_source_evidence(engine, claim_id, s1, tag="p1")
    await _host_source_evidence(engine, claim_id, s2, tag="p2")
    counter_id = await _host_counter_evidence(engine, claim_id, s3, scope={"x": 1})

    # the claim is current (disputed) before the resolution attempts
    await _seed_job(engine, claim_id)
    await _run_worker(engine)
    assert (await _head(engine, claim_id))[1] == "disputed"

    basis_claim = uuid.uuid4()
    await _seed_claim_pending(engine, basis_claim, "база", "external_fact")
    basis_id = await _host_basis_evidence(engine, basis_claim, scope={"x": 1, "extra": 2})

    # the support evidence id on the target claim (a non-counter target)
    support_eid = (
        await _scalar(
            engine,
            "SELECT id FROM evidence WHERE claim_id = :c AND relation = 'supports' "
            "ORDER BY created_at LIMIT 1",
            {"c": claim_id},
        )
    )[0]

    # 1. XOR: both bases set
    with pytest.raises(ResolutionError, match="xor"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=basis_id,
                basis_correction_id=uuid.uuid4(),
                actor="op",
            ),
        )
    # 2. XOR: no basis
    with pytest.raises(ResolutionError, match="xor"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db, AuditService(db), evidence_id=counter_id, actor="op"
            ),
        )
    # 3. target is not a counters relation
    with pytest.raises(ResolutionError, match="not a counters"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=support_eid,
                basis_evidence_id=basis_id,
                actor="op",
            ),
        )
    # 4. target evidence does not exist
    with pytest.raises(ResolutionError, match="does not exist"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=uuid.uuid4(),
                basis_evidence_id=basis_id,
                actor="op",
            ),
        )
    # 5. basis evidence equals the target
    with pytest.raises(ResolutionError, match="equals the target"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=counter_id,
                actor="op",
            ),
        )
    # 6. basis scope misses a claim key of the target scope
    other_claim = uuid.uuid4()
    await _seed_claim_pending(engine, other_claim, "база 2", "external_fact")
    narrow_basis = await _host_basis_evidence(
        engine, other_claim, scope={"y": 1}, tag="narrow"
    )
    with pytest.raises(ResolutionError, match="misses claim key"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=narrow_basis,
                actor="op",
            ),
        )
    # 7. transitive dependency: the basis claim depends on the target
    dep_claim = uuid.uuid4()
    await _seed_claim_pending(engine, dep_claim, "зависимая база", "external_fact")
    dep_basis = await _host_basis_evidence(engine, dep_claim, scope={"x": 1}, tag="dep")
    await _scalar(
        engine,
        "INSERT INTO claim_dependencies (id, from_claim_id, to_claim_id, kind) "
        "VALUES (:id, :f, :t, 'evidential')",
        {"id": uuid.uuid4(), "f": dep_claim, "t": claim_id},
    )
    with pytest.raises(ResolutionError, match="transitively dependent"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=dep_basis,
                actor="op",
            ),
        )
    # 8. the basis claim is the target claim itself (circular)
    own_basis = await _host_basis_evidence(engine, claim_id, scope={"x": 1}, tag="own")
    with pytest.raises(ResolutionError, match="transitively dependent"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=own_basis,
                actor="op",
            ),
        )

    # the valid resolution goes through…
    result = await _in_tx(
        engine,
        lambda db: apply_counter_resolution(
            db,
            AuditService(db),
            evidence_id=counter_id,
            basis_evidence_id=basis_id,
            actor="op",
        ),
    )
    assert result.invalidated == 1
    # …and the second valid one for the same target is rejected (uniqueness)
    with pytest.raises(ResolutionError, match="already exists"):
        await _in_tx(
            engine,
            lambda db: apply_counter_resolution(
                db,
                AuditService(db),
                evidence_id=counter_id,
                basis_evidence_id=basis_id,
                actor="op",
            ),
        )
    # nothing but the one valid row was written
    rows = await _all(
        engine,
        "SELECT valid FROM counterevidence_resolutions WHERE evidence_id = :e",
        {"e": counter_id},
    )
    assert len(rows) == 1
    assert rows[0][0] is True

    # the DB-level XOR CHECK still guards against bypass writers
    with pytest.raises(IntegrityError):
        await _scalar(
            engine,
            "INSERT INTO counterevidence_resolutions "
            "(id, evidence_id, basis_evidence_id, basis_correction_id, actor, rules_version) "
            "VALUES (:id, :e, :b, :b, 'op', 'rules-v1')",
            {"id": uuid.uuid4(), "e": counter_id, "b": basis_id},
        )


@pytest.mark.asyncio
async def test_correction_basis_and_invalidation_cascades(migrated_db: Any) -> None:
    """A source-graph correction as the basis (the provenance changed):
    the resolution works; invalidating the correction invalidates the
    resolution in the same transaction and cascades — the counter is
    unresolved again, the claim drops back to disputed."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт, provenance поправлен", "external_fact")
    s1 = await _host_source(engine, uri="https://c1.net/story")
    s2 = await _host_source(engine, uri="https://c2.org/story")
    s3 = await _host_source(engine, uri="https://c3.io/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    counter_id = await _host_counter_evidence(engine, claim_id, s3)

    await _seed_job(engine, claim_id)
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "disputed"

    # a VALID correction touching the claim's sources: a split between
    # the support source s1 and the counter source s3 (inert for
    # grouping — different domains, no edge — but a verifiable
    # provenance basis per §8.7.4)
    correction_id = await _host_correction(engine, s1, s3, kind="split")
    result = await _in_tx(
        engine,
        lambda db: apply_counter_resolution(
            db,
            AuditService(db),
            evidence_id=counter_id,
            basis_correction_id=correction_id,
            actor="operator-2",
        ),
    )
    assert result.invalidated == 1
    assert result.job_created is True

    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "supported"
    assert head[3] == "E3"

    # the correction is invalidated (the operator retracts it): every
    # resolution resting on it goes invalid + cascade, one transaction
    async def _retract(db: Any) -> Any:
        await db.execute(
            text(
                "UPDATE source_graph_corrections SET valid = false WHERE id = :id"
            ),
            {"id": correction_id},
        )
        return await invalidate_resolutions_for_correction(
            db, AuditService(db), correction_id=correction_id, actor="operator-2",
            reason="correction retracted",
        )

    count = await _in_tx(engine, _retract)
    assert count == 1
    rows = await _all(
        engine,
        "SELECT valid FROM counterevidence_resolutions",
    )
    assert len(rows) == 1
    assert rows[0][0] is False
    head = await _head(engine, claim_id)
    assert head[0] == "pending"

    # the worker: the counter is unresolved again → disputed E1
    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "disputed"
    assert head[3] == "E1"
    assert "counterevidence_unresolved" in await _worker_reasons(engine, claim_id)

    # the invalidation audit is there
    audit = await _scalar(
        engine,
        "SELECT payload->>'reason' FROM audit_events "
        "WHERE type = 'counter_resolution_invalidated' ORDER BY sequence DESC LIMIT 1",
    )
    assert audit is not None
    assert audit[0] == "correction retracted"


@pytest.mark.asyncio
async def test_direct_invalidation_cascades_back(migrated_db: Any) -> None:
    """Direct invalidation of a resolution (the basis no longer holds):
    head → pending + job → the worker restores disputed ≤E1."""
    _url, engine = migrated_db
    claim_id = uuid.uuid4()
    await _seed_claim_pending(engine, claim_id, "факт с отозванным снятием", "external_fact")
    s1 = await _host_source(engine, uri="https://d1.co/story")
    s2 = await _host_source(engine, uri="https://d2.io/story")
    s3 = await _host_source(engine, uri="https://d3.dev/story")
    await _host_source_evidence(engine, claim_id, s1, tag="a")
    await _host_source_evidence(engine, claim_id, s2, tag="b")
    counter_id = await _host_counter_evidence(engine, claim_id, s3, scope={"x": 1})

    basis_claim = uuid.uuid4()
    await _seed_claim_pending(engine, basis_claim, "база", "external_fact")
    basis_id = await _host_basis_evidence(engine, basis_claim, scope={"x": 1})

    await _seed_job(engine, claim_id)
    await _run_worker(engine)
    assert (await _head(engine, claim_id))[1] == "disputed"

    result = await _in_tx(
        engine,
        lambda db: apply_counter_resolution(
            db,
            AuditService(db),
            evidence_id=counter_id,
            basis_evidence_id=basis_id,
            actor="op",
        ),
    )
    await _run_worker(engine)
    assert (await _head(engine, claim_id))[1] == "supported"

    result2 = await _in_tx(
        engine,
        lambda db: invalidate_counter_resolution(
            db, AuditService(db), resolution_id=result.resolution_id,
            actor="op", reason="basis retracted",
        ),
    )
    assert result2.invalidated == 1
    assert result2.job_created is True

    await _run_worker(engine)
    head = await _head(engine, claim_id)
    assert head[1] == "disputed"
    assert head[3] == "E1"
    # the invalidated row stays for history
    rows = await _all(
        engine,
        "SELECT valid FROM counterevidence_resolutions WHERE evidence_id = :e",
        {"e": counter_id},
    )
    assert len(rows) == 1
    assert rows[0][0] is False
    # idempotent: invalidating again is a no-op
    result3 = await _in_tx(
        engine,
        lambda db: invalidate_counter_resolution(
            db, AuditService(db), resolution_id=result.resolution_id,
            actor="op", reason="again",
        ),
    )
    assert result3.invalidated == 0
    assert result3.job_created is False
