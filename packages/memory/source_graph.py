"""T4.7 (§11.3, §14): the full source graph — independence snapshots and
the merge/correction cascade.

- ``build_source_independence_snapshot`` — the versioned algorithm over
  the claim's sources (with their parents, dependency edges and
  corrections) → ``source_independence_snapshots`` + ``_members``; the
  assessment fixes the snapshot id, so a later algorithm change cannot
  silently re-grade old knowledge;
- ``apply_source_graph_change`` — the trusted-host path for graph
  changes (new sources, dependency edges, corrections): every claim
  whose evidence touches the affected sources is invalidated (head →
  pending, one durable job each) and the ``source_graph`` domain
  revision is bumped — §11.3: a new source or a correction that merges
  previously distinct groups invalidates and recomputes the dependent
  claim assessments. The correction itself is NOT evidence for a claim.

Both run inside the caller's transaction (audit + outbox in the same
tx, §3.8).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType, ReassessmentJobStatus
from packages.domain.models.memory import (
    ORMReassessmentJob,
    ORMSource,
    ORMSourceDependencyEdge,
    ORMSourceGraphCorrection,
    ORMSourceIndependenceMember,
    ORMSourceIndependenceSnapshot,
)
from packages.domain.services.audit import AuditService
from packages.memory.independence import (
    SourceGraphCorrection,
    SourceGraphEdge,
    SourceGraphInput,
    group_source_graph,
)


@dataclass(frozen=True)
class SourceGraphChangeResult:
    affected_claims: int
    invalidated: int
    jobs_created: int
    revision: int


async def build_source_independence_snapshot(
    db: AsyncSession, *, claim_id: uuid.UUID
) -> tuple[uuid.UUID | None, dict[uuid.UUID, tuple[str, str]]]:
    """The §11.3 snapshot over the claim's sources (deterministic,
    versioned). Returns (snapshot_id, {source_id: (group_id, basis)});
    (None, {}) when the claim has no source-based evidence. The snapshot
    and members rows are immutable (the assessment fixes the id); a new
    assessment always gets a fresh snapshot — like T4.6."""
    source_ids = [
        row[0]
        for row in (
            await db.execute(
                text(
                    "SELECT DISTINCT source_id FROM evidence "
                    "WHERE claim_id = :c AND source_id IS NOT NULL "
                    "ORDER BY source_id"
                ),
                {"c": claim_id},
            )
        ).all()
    ]
    if not source_ids:
        return None, {}

    sources = (
        (
            await db.execute(
                select(ORMSource).where(ORMSource.id.in_(source_ids)).order_by(ORMSource.id)
            )
        )
        .scalars()
        .all()
    )
    # direct parents of the set: graph nodes (two children of one parent
    # merge through it) but not members of the snapshot
    parent_ids = sorted(
        {s.parent_source_id for s in sources if s.parent_source_id is not None}
        - set(source_ids)
    )
    parent_rows = (
        (
            await db.execute(
                select(ORMSource).where(ORMSource.id.in_(parent_ids)).order_by(ORMSource.id)
            )
        )
        .scalars()
        .all()
        if parent_ids
        else []
    )
    node_ids = set(source_ids) | set(parent_ids)

    edges_rows = (
        (
            await db.execute(
                select(ORMSourceDependencyEdge)
                .where(
                    ORMSourceDependencyEdge.from_source_id.in_(node_ids),
                    ORMSourceDependencyEdge.to_source_id.in_(node_ids),
                )
                .order_by(ORMSourceDependencyEdge.id)
            )
        )
        .scalars()
        .all()
    )
    correction_rows = (
        (
            await db.execute(
                select(ORMSourceGraphCorrection)
                .where(
                    ORMSourceGraphCorrection.from_source_id.in_(node_ids),
                    ORMSourceGraphCorrection.to_source_id.in_(node_ids),
                )
                .order_by(ORMSourceGraphCorrection.id)
            )
        )
        .scalars()
        .all()
    )

    inputs = [
        SourceGraphInput(
            source_id=s.id,
            canonical_uri=s.canonical_uri,
            content_hash=s.content_hash,
            parent_source_id=s.parent_source_id,
        )
        for s in (*sources, *parent_rows)
    ]
    edges = [
        SourceGraphEdge(
            edge_id=e.id,
            from_id=e.from_source_id,
            to_id=e.to_source_id,
            kind=e.kind,
        )
        for e in edges_rows
    ]
    corrections = [
        SourceGraphCorrection(
            correction_id=c.id,
            from_id=c.from_source_id,
            to_id=c.to_source_id,
            kind=c.kind,
            valid=c.valid,
        )
        for c in correction_rows
    ]

    result = group_source_graph(inputs, edges, corrections)

    snapshot = ORMSourceIndependenceSnapshot(
        id=uuid.uuid4(),
        algorithm_version=result.algorithm_version,
        thresholds=dict(result.thresholds),
        psl_fingerprint=result.psl_fingerprint,
        uri_normalizer_version=result.uri_normalizer_version,
    )
    db.add(snapshot)
    # explicit flush: the members batch carries the composite PK with the
    # fresh parent row — the UoW does not guarantee parent-first order
    # (T4.6, the same trap)
    await db.flush()
    for sid in source_ids:
        group_id, basis = result.groups[sid], result.bases[sid]
        db.add(
            ORMSourceIndependenceMember(
                snapshot_id=snapshot.id,
                source_id=sid,
                group_id=group_id,
                basis=basis,
            )
        )
    await db.flush()
    return snapshot.id, {sid: (result.groups[sid], result.bases[sid]) for sid in source_ids}


async def _invalidate_head(
    db: AsyncSession, *, claim_id: uuid.UUID, snapshot_id: uuid.UUID, prepared_by: str
) -> str:
    """Idempotent head invalidation (T4.2 pattern, cascade.py). Returns
    the prior state — only 'current' proceeds to the job insert."""
    res = await db.execute(
        text(
            "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
            "current_assessment_id = NULL, epistemic_status = NULL, "
            "prepared_by = :p, updated_at = clock_timestamp() "
            "WHERE claim_id = :c AND config_snapshot_id = :s "
            "AND assessment_state = 'current' RETURNING 1"
        ),
        {"c": claim_id, "s": snapshot_id, "p": prepared_by},
    )
    if res.first() is not None:
        return "current"
    row = (
        await db.execute(
            text(
                "SELECT assessment_state FROM claim_assessment_heads "
                "WHERE claim_id = :c AND config_snapshot_id = :s"
            ),
            {"c": claim_id, "s": snapshot_id},
        )
    ).first()
    if row is None:
        return "missing"
    return row[0] if isinstance(row[0], str) else str(row[0])


async def _enqueue_job(
    db: AsyncSession, *, claim_id: uuid.UUID, snapshot_id: uuid.UUID, reason: str
) -> bool:
    """One active job per (claim, snapshot) — the partial unique index
    keeps a replayed change idempotent (§8.6)."""
    exists = (
        (
            await db.execute(
                text(
                    "SELECT 1 FROM reassessment_jobs "
                    "WHERE claim_id = :c AND target_config_snapshot_id = :s "
                    "AND status IN ('queued','leased','retry')"
                ),
                {"c": claim_id, "s": snapshot_id},
            )
        )
        .scalar_one_or_none()
    )
    if exists is not None:
        return False
    db.add(
        ORMReassessmentJob(
            id=uuid.uuid4(),
            claim_id=claim_id,
            target_config_snapshot_id=snapshot_id,
            status=ReassessmentJobStatus.QUEUED.value,
            reason=reason,
        )
    )
    await db.flush()
    return True


async def apply_source_graph_change(
    db: AsyncSession,
    audit: AuditService,
    *,
    source_ids: frozenset[uuid.UUID],
    actor: str,
) -> SourceGraphChangeResult:
    """Trusted-host cascade for a source-graph change (§11.3): the graph
    rows themselves (sources / dependency edges / corrections) are
    written by the caller in this transaction; this invalidates every
    claim whose evidence touches the affected sources and enqueues the
    recomputation. The worker rebuilds the independence snapshot and the
    rules engine re-grades (a merged group may drop supported →
    hypothesis). Deterministic and idempotent; bumps the ``source_graph``
    domain revision; audit in the same tx."""
    if not source_ids:
        return SourceGraphChangeResult(0, 0, 0, 0)

    # the graph revision row: lock it (canonical single-scope lock)
    rev = (
        await db.execute(
            text(
                "SELECT revision FROM domain_revisions "
                "WHERE scope = 'source_graph' FOR UPDATE"
            )
        )
    ).first()
    if rev is None:
        raise RuntimeError("domain_revisions source_graph row missing")

    snapshot_id = (
        (
            await db.execute(
                text(
                    "SELECT active_config_snapshot_id FROM runtime_config_heads "
                    "WHERE scope = 'global' FOR UPDATE"
                )
            )
        )
        .scalar_one_or_none()
    )
    if snapshot_id is None:
        raise RuntimeError("no effective config snapshot (fail-closed)")
    if not isinstance(snapshot_id, uuid.UUID):
        snapshot_id = uuid.UUID(str(snapshot_id))

    affected = [
        row[0]
        for row in (
            await db.execute(
                text(
                    "SELECT DISTINCT claim_id FROM evidence "
                    "WHERE source_id = ANY(:ids) ORDER BY claim_id"
                ),
                {"ids": list(source_ids)},
            )
        ).all()
    ]

    invalidated = 0
    jobs_created = 0
    for claim_id in affected:
        prior = await _invalidate_head(
            db, claim_id=claim_id, snapshot_id=snapshot_id, prepared_by="system:source_graph"
        )
        if prior == "current":
            invalidated += 1
            if await _enqueue_job(
                db, claim_id=claim_id, snapshot_id=snapshot_id, reason="source_graph_change"
            ):
                jobs_created += 1

    new_rev = (
        await db.execute(
            text(
                "UPDATE domain_revisions SET revision = revision + 1, updated_at = now() "
                "WHERE scope = 'source_graph' RETURNING revision"
            )
        )
    ).first()
    revision = int(new_rev[0]) if new_rev is not None else int(rev[0]) + 1

    await audit.record(
        AuditEventType.SOURCE_GRAPH_CHANGED,
        actor=actor,
        payload={
            "source_ids": [str(i) for i in sorted(source_ids, key=str)],
            "affected_claims": len(affected),
            "invalidated": invalidated,
            "jobs_created": jobs_created,
            "source_graph_revision": revision,
        },
        public_summary=f"source graph change: {invalidated} claim(s) requeued",
    )
    return SourceGraphChangeResult(len(affected), invalidated, jobs_created, revision)
