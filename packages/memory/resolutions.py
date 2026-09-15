"""Counterevidence resolutions (T4.8, §8.7.4, §14, §20.11).

A resolution is a SEPARATE audited entity, not a model flag: it records
that one ``counters`` evidence no longer counts against its claim, with
exactly one verifiable basis — either an evidence row (the
counterexample is out of the claim scope, a verifiable method error was
found, or new evidence explains the divergence) or a valid
source-graph correction (the provenance changed, T4.7).

Invariants (spec lines 2141–2148):

- XOR — exactly one of ``basis_evidence_id`` / ``basis_correction_id``
  is set (DB CHECK);
- at most one VALID resolution per target evidence (DB partial unique);
- the target evidence belongs to the same claim and participates as
  ``counters``;
- the basis is currently valid, scope-compatible (its scope covers the
  target's scope) and NOT transitively dependent on the target — the
  inter-row invariants the deferred trigger / rules engine checks;
- a correction basis must reference a VALID ``source_graph_corrections``
  row that touches one of the target claim's sources.

Invalidating a resolution (or the correction under it) makes the
resolution invalid in the same transaction and cascades the
recomputation of the claim whose assessment counted the counterevidence
resolved — the worker re-runs the rules engine, the counter is
unresolved again and the grade drops (disputed, ≤E1, §3.7).

The resolution is PROPOSED by the trusted host (the curator path):
v1 has no LLM staging op for resolutions (M5 surface), the same
decision as the source-graph corrections (T4.7).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType, ReassessmentJobStatus
from packages.domain.models.memory import (
    ORMCounterevidenceResolution,
    ORMReassessmentJob,
)
from packages.domain.services.audit import AuditService

JOB_REASON = "counter_resolution_change"
PREPARED_BY = "system:counter_resolution"
DEFAULT_RULES_VERSION = "rules-v1"


class ResolutionError(ValueError):
    """The resolution basis fails the deterministic checks (§8.7.4):
    the caller rejects the operation instead of writing an invalid row
    (the rules-engine verification, spec line 2147)."""


@dataclass(frozen=True)
class CounterResolutionResult:
    resolution_id: uuid.UUID
    claim_id: uuid.UUID
    invalidated: int
    job_created: bool


def claim_depends_on(
    from_claim: uuid.UUID,
    to_claim: uuid.UUID,
    edges: Sequence[tuple[uuid.UUID, uuid.UUID]],
) -> bool:
    """Pure transitive check over claim_dependencies edges
    (``(from, to)`` = from DEPENDS ON to): does ``from_claim``
    transitively depend on ``to_claim``? The same claim counts as a
    dependency (a resolution cannot rest on the claim's own evidence —
    circular)."""
    if from_claim == to_claim:
        return True
    adjacency: dict[uuid.UUID, list[uuid.UUID]] = {}
    for f, t in edges:
        adjacency.setdefault(f, []).append(t)
    seen: set[uuid.UUID] = set()
    frontier = [from_claim]
    while frontier:
        current = frontier.pop()
        for nxt in adjacency.get(current, ()):
            if nxt == to_claim:
                return True
            if nxt not in seen:
                seen.add(nxt)
                frontier.append(nxt)
    return False


async def _cascade_claim(
    db: AsyncSession, *, claim_id: uuid.UUID, reason: str
) -> tuple[int, bool]:
    """Head → pending + one durable job for the claim (T4.7 pattern,
    cascade.py). Returns (invalidated, job_created)."""
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

    res = await db.execute(
        text(
            "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
            "current_assessment_id = NULL, epistemic_status = NULL, "
            "prepared_by = :p, updated_at = clock_timestamp() "
            "WHERE claim_id = :c AND config_snapshot_id = :s "
            "AND assessment_state = 'current' RETURNING 1"
        ),
        {"c": claim_id, "s": snapshot_id, "p": PREPARED_BY},
    )
    if res.first() is None:
        return (0, False)

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
        return (1, False)
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
    return (1, True)


async def apply_counter_resolution(
    db: AsyncSession,
    audit: AuditService,
    *,
    evidence_id: uuid.UUID,
    basis_evidence_id: uuid.UUID | None = None,
    basis_correction_id: uuid.UUID | None = None,
    actor: str,
    rules_version: str = DEFAULT_RULES_VERSION,
) -> CounterResolutionResult:
    """Create a VALID resolution + the recomputation cascade (one
    transaction, trusted host). The assessment that counted the
    counterevidence resolved is invalidated; the worker re-runs the
    rules engine (the counter no longer caps the grade)."""
    target_claim, basis_kind = await _validate(
        db,
        evidence_id=evidence_id,
        basis_evidence_id=basis_evidence_id,
        basis_correction_id=basis_correction_id,
    )

    existing = (
        await db.execute(
            text(
                "SELECT 1 FROM counterevidence_resolutions "
                "WHERE evidence_id = :e AND valid"
            ),
            {"e": evidence_id},
        )
    ).first()
    if existing is not None:
        raise ResolutionError("a valid resolution already exists for this evidence")

    resolution_id = uuid.uuid4()
    db.add(
        ORMCounterevidenceResolution(
            id=resolution_id,
            evidence_id=evidence_id,
            basis_evidence_id=basis_evidence_id,
            basis_correction_id=basis_correction_id,
            actor=actor,
            rules_version=rules_version,
            valid=True,
        )
    )
    await db.flush()

    invalidated, job_created = await _cascade_claim(
        db, claim_id=target_claim, reason=JOB_REASON
    )

    await audit.record(
        AuditEventType.COUNTER_RESOLUTION_CREATED,
        actor=actor,
        payload={
            "resolution_id": str(resolution_id),
            "evidence_id": str(evidence_id),
            "claim_id": str(target_claim),
            "basis_evidence_id": str(basis_evidence_id) if basis_evidence_id else None,
            "basis_correction_id": str(basis_correction_id) if basis_correction_id else None,
            "basis_kind": basis_kind,
            "rules_version": rules_version,
            "invalidated": invalidated,
            "jobs_created": job_created,
        },
        public_summary=f"counterevidence resolution created for claim {target_claim}",
    )
    return CounterResolutionResult(resolution_id, target_claim, invalidated, job_created)


async def invalidate_counter_resolution(
    db: AsyncSession,
    audit: AuditService,
    *,
    resolution_id: uuid.UUID,
    actor: str,
    reason: str,
) -> CounterResolutionResult:
    """Make a resolution invalid (the basis no longer holds) + cascade:
    the counter is unresolved again — the worker re-grades (disputed,
    ≤E1). Idempotent: an already-invalid row is a no-op."""
    row = (
        await db.execute(
            select(ORMCounterevidenceResolution).where(
                ORMCounterevidenceResolution.id == resolution_id
            )
        )
    ).scalar_one_or_none()
    if row is None:
        raise ResolutionError("resolution does not exist")
    target_claim = (
        (
            await db.execute(
                text("SELECT claim_id FROM evidence WHERE id = :id"),
                {"id": row.evidence_id},
            )
        )
        .scalar_one_or_none()
    )
    if target_claim is None:
        raise ResolutionError("target evidence does not exist")
    if not row.valid:
        return CounterResolutionResult(row.id, target_claim, 0, False)
    row.valid = False
    await db.flush()

    invalidated, job_created = await _cascade_claim(
        db, claim_id=target_claim, reason=JOB_REASON
    )

    await audit.record(
        AuditEventType.COUNTER_RESOLUTION_INVALIDATED,
        actor=actor,
        payload={
            "resolution_id": str(resolution_id),
            "claim_id": str(target_claim),
            "reason": reason,
            "invalidated": invalidated,
            "jobs_created": job_created,
        },
        public_summary=f"counterevidence resolution invalidated (claim {target_claim})",
    )
    return CounterResolutionResult(row.id, target_claim, invalidated, job_created)


async def invalidate_resolutions_for_correction(
    db: AsyncSession,
    audit: AuditService,
    *,
    correction_id: uuid.UUID,
    actor: str,
    reason: str,
) -> int:
    """§8.7.4 / spec line 2148: invalidating the correction basis makes
    every resolution resting on it invalid in the same revision and
    cascades the dependent claims. Called by the trusted host in the
    same transaction that flips the correction. Returns the count of
    resolutions invalidated."""
    rows = (
        await db.execute(
            select(ORMCounterevidenceResolution)
            .where(
                ORMCounterevidenceResolution.basis_correction_id == correction_id,
                ORMCounterevidenceResolution.valid.is_(True),
            )
            .order_by(ORMCounterevidenceResolution.created_at)
        )
    ).scalars().all()
    for row in rows:
        await invalidate_counter_resolution(
            db, audit, resolution_id=row.id, actor=actor, reason=reason
        )
    return len(rows)


async def _validate(
    db: AsyncSession,
    *,
    evidence_id: uuid.UUID,
    basis_evidence_id: uuid.UUID | None,
    basis_correction_id: uuid.UUID | None,
) -> tuple[uuid.UUID, str]:
    """Deterministic basis verification (§8.7.4, spec line 2147).
    Returns (target_claim_id, basis_kind) or raises ResolutionError."""
    if basis_evidence_id is None and basis_correction_id is None:
        raise ResolutionError("xor: exactly one basis is required")
    if basis_evidence_id is not None and basis_correction_id is not None:
        raise ResolutionError("xor: exactly one basis is required")

    target = (
        await db.execute(
            text("SELECT claim_id, relation, scope FROM evidence WHERE id = :id"),
            {"id": evidence_id},
        )
    ).first()
    if target is None:
        raise ResolutionError("target evidence does not exist")
    target_claim, target_relation, target_scope = target[0], target[1], dict(target[2] or {})
    if target_relation != "counters":
        raise ResolutionError("target evidence is not a counters relation")

    if basis_evidence_id is not None:
        if basis_evidence_id == evidence_id:
            raise ResolutionError("basis evidence equals the target evidence")
        basis = (
            await db.execute(
                text("SELECT claim_id, scope FROM evidence WHERE id = :id"),
                {"id": basis_evidence_id},
            )
        ).first()
        if basis is None:
            raise ResolutionError("basis evidence does not exist")
        basis_claim, basis_scope = basis[0], dict(basis[1] or {})
        # scope-compatible: the basis scope covers the target scope
        for key, value in target_scope.items():
            if key not in basis_scope:
                raise ResolutionError(f"basis scope misses claim key {key!r}")
            if value is not None and basis_scope[key] not in (None, value):
                raise ResolutionError(f"basis scope value for {key!r} is incompatible")
        # not transitively dependent on the target (claim_dependencies,
        # from DEPENDS ON to)
        edges = [
            (f, t)
            for f, t in (
                await db.execute(
                    text("SELECT from_claim_id, to_claim_id FROM claim_dependencies")
                )
            ).all()
        ]
        if claim_depends_on(basis_claim, target_claim, edges):
            raise ResolutionError(
                "basis claim is transitively dependent on the target claim"
            )
        return target_claim, "evidence"

    # correction basis: a VALID row touching one of the target claim's sources
    corr = (
        await db.execute(
            text(
                "SELECT id, valid, from_source_id, to_source_id "
                "FROM source_graph_corrections WHERE id = :id"
            ),
            {"id": basis_correction_id},
        )
    ).first()
    if corr is None:
        raise ResolutionError("basis correction does not exist")
    if not corr[1]:
        raise ResolutionError("basis correction is not valid")
    claim_sources = [
        row[0]
        for row in (
            await db.execute(
                text(
                    "SELECT DISTINCT source_id FROM evidence "
                    "WHERE claim_id = :c AND source_id IS NOT NULL ORDER BY source_id"
                ),
                {"c": target_claim},
            )
        ).all()
    ]
    if corr[2] not in claim_sources and corr[3] not in claim_sources:
        raise ResolutionError("correction does not touch the target claim's sources")
    return target_claim, "correction"
