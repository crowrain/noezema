"""Cascade invalidation over the evidential dependency DAG (T4.2, §8.6).

Protocol (spec §8.6, steps 1–6):

1. OUTSIDE a transaction: compute the reverse closure of the invalidated
   root against the current ``domain_revisions(dependency_graph)`` and
   serialize it into an immutable content-addressed closure manifest
   (root, graph revision, ordered claim IDs, topological rank, count,
   sha256).
2. Short transaction: the knowledge writer gate + locks
   ``runtime_config_heads → knowledge → dependency_graph`` (canonical
   order), verify the graph revision, invalidate the root head and bump
   the knowledge revision.
3. The root's effective head goes ``pending`` with a durable
   reassessment job for the effective config snapshot (plus a
   deterministic UUIDv5 question).
4. Closure within the inline limit: invalidate the downstream claims
   and create their jobs in topological order, same transaction.
5. Larger closure: atomically create a barrier referencing the manifest
   (``next_offset=0``), apply the first batch and advance the cursor in
   the same transaction.
6. ``process_barrier`` applies subsequent batches; invalidation, job
   creation and the cursor move commit as ONE transaction.

A batch is idempotent: an already pending/invalid head is skipped and
the partial unique index never creates a duplicate active job. If the
graph revision moved, the processor publishes a new generation from the
fresh closure (already processed claims are safely skipped). Before
``resolved`` the processor re-checks the live closure: no current
descendant may remain. A manifest hash mismatch, a missing manifest or
an impossible cursor moves the barrier to ``blocked`` — it keeps
ancestor protection and never auto-resolves (§8.6).
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_json_bytes, sha256_hex
from packages.domain.config import CLOSURE_MANIFEST_UUID5_NAMESPACE, QUESTION_UUID5_NAMESPACE
from packages.domain.db.uow import transaction
from packages.domain.models.enums import (
    AuditEventType,
    BarrierStatus,
    QuestionOrigin,
    ReassessmentJobStatus,
)
from packages.domain.models.memory import (
    ORMClaim,
    ORMClosureManifest,
    ORMDependencyInvalidationBarrier,
    ORMReassessmentJob,
)
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.audit import AuditService
from packages.domain.services.locks import validate_lock_order

#: inline invalidation limit: closures of at most this many downstream
#: claims are applied in the start transaction (PLAN «Пороги M4»: batch 32)
INLINE_CLOSURE_LIMIT = 32

#: durable batch size for barrier processing (PLAN «Пороги M4»: batch 32)
BATCH_SIZE = 32

#: the writer gate for knowledge writers (T4.2; T4.4 formalizes the
#: NOWAIT gate + session-intent yield): a session-level advisory lock
#: taken before the canonical row locks and released after the short
#: transaction settles. On a crash the session closes and Postgres
#: releases the lock.
WRITER_GATE_NAME = "noezema:knowledge_writer"

#: the row locks the cascade short transactions actually take — a
#: subsequence of the canonical order (locks.py module docstring)
_CASCADE_LOCK_PLAN: tuple[tuple[str, str], ...] = (
    ("runtime_config_heads", "scope"),
    ("domain_revisions", "knowledge"),
    ("domain_revisions", "dependency_graph"),
)

validate_lock_order(list(_CASCADE_LOCK_PLAN))  # static invariant (raises)


class CascadeError(RuntimeError):
    """A cascade precondition failed (root missing, stale graph
    revision, writer gate busy) — retryable."""


class BarrierBlockedError(RuntimeError):
    """The barrier is in ``blocked`` — irrecoverable without an audited
    operator recovery (§8.6)."""


@dataclass(frozen=True)
class ClosureResult:
    """The reverse closure of a root claim (root excluded)."""

    root: uuid.UUID
    graph_revision: int
    #: closure members ordered by (topological rank, claim id)
    claim_ids: tuple[uuid.UUID, ...]
    #: claim id (str) -> depth (direct dependents of the root are depth 1)
    ranks: dict[str, int]

    @property
    def count(self) -> int:
        return len(self.claim_ids)

    def batch(self, offset: int, size: int) -> tuple[uuid.UUID, ...]:
        """One durable cursor window of the ordered closure."""
        return self.claim_ids[offset : offset + size]


@dataclass(frozen=True)
class CascadeStartResult:
    root: uuid.UUID
    manifest_sha256: str
    closure_count: int
    #: "inline" — applied in the start transaction; "barrier" — durable
    #: barrier created (its first batch was applied)
    mode: str
    barrier_id: uuid.UUID | None
    invalidated: int
    jobs_created: int
    graph_revision: int
    knowledge_revision: int | None


def compute_reverse_closure(
    edges: list[tuple[Any, Any]],
    root: uuid.UUID,
    graph_revision: int,
) -> ClosureResult:
    """Pure reverse-closure walk over evidential edges.

    ``edges`` are (from_claim_id, to_claim_id) pairs; direction:
    ``from`` depends on ``to``. The closure of ``root`` is every claim
    that transitively depends on it. Deterministic output: members
    ordered by (rank, claim id), ranks = BFS depth (direct dependents
    are 1). The root itself is excluded (it is invalidated by the
    start transaction, not the closure).
    """
    reverse: dict[uuid.UUID, list[uuid.UUID]] = {}
    for f, t in edges:
        f_id = f if isinstance(f, uuid.UUID) else uuid.UUID(str(f))
        t_id = t if isinstance(t, uuid.UUID) else uuid.UUID(str(t))
        if f_id == t_id:
            continue  # self-edges are rejected at commit (T4.1); be safe
        reverse.setdefault(t_id, []).append(f_id)

    depths: dict[uuid.UUID, int] = {}
    frontier: list[uuid.UUID] = [root]
    depth = 0
    seen: set[uuid.UUID] = {root}
    while frontier:
        depth += 1
        nxt: list[uuid.UUID] = []
        for node in frontier:
            for dep in reverse.get(node, ()):  # claims depending on node
                if dep in seen:
                    continue  # already ranked at a smaller depth (DAG)
                seen.add(dep)
                depths[dep] = depth
                nxt.append(dep)
        frontier = nxt

    ordered = sorted(depths, key=lambda c: (depths[c], c))
    return ClosureResult(
        root=root,
        graph_revision=graph_revision,
        claim_ids=tuple(ordered),
        ranks={str(c): depths[c] for c in ordered},
    )


@asynccontextmanager
async def _writer_gate(db: AsyncSession) -> AsyncIterator[None]:
    """Session-level advisory writer gate: acquired before the short
    transaction, released after it settles (commit or rollback)."""
    got = (
        await db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:n))"), {"n": WRITER_GATE_NAME}
        )
    ).scalar_one()
    if not got:
        raise CascadeError("could not acquire the knowledge writer gate")
    try:
        yield
    finally:
        await db.execute(
            text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": WRITER_GATE_NAME}
        )


async def _graph_revision(db: AsyncSession) -> int:
    rev = (
        await db.execute(
            text("SELECT revision FROM domain_revisions WHERE scope = 'dependency_graph'")
        )
    ).scalar_one()
    return int(rev)


async def _evidential_edges(db: AsyncSession) -> list[tuple[Any, Any]]:
    rows = await db.execute(
        text(
            "SELECT from_claim_id, to_claim_id FROM claim_dependencies "
            "WHERE kind = 'evidential'"
        )
    )
    return [(r[0], r[1]) for r in rows.all()]


async def _load_closure(db: AsyncSession, root: uuid.UUID) -> ClosureResult:
    """Step 1 input: closure against the CURRENT graph revision."""
    revision = await _graph_revision(db)
    edges = await _evidential_edges(db)
    return compute_reverse_closure(edges, root, revision)


def _as_uuid(raw: Any) -> uuid.UUID:
    """JSONB UUID coercion (asyncpg may return its own UUID type)."""
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


def _manifest_for(closure: ClosureResult) -> tuple[ORMClosureManifest, str]:
    """Content-addressed manifest row (id derived from the sha256)."""
    body: dict[str, Any] = {
        "root": str(closure.root),
        "graph_revision": closure.graph_revision,
        "claim_ids": [str(c) for c in closure.claim_ids],
        "ranks": closure.ranks,
        "count": closure.count,
    }
    sha = sha256_hex(canonical_json_bytes(body))
    return (
        ORMClosureManifest(
            id=uuid.uuid5(uuid.UUID(CLOSURE_MANIFEST_UUID5_NAMESPACE), sha),
            root_claim_id=closure.root,
            graph_revision=closure.graph_revision,
            claim_ids=[str(c) for c in closure.claim_ids],
            ranks=closure.ranks,
            count=closure.count,
            sha256=sha,
        ),
        sha,
    )


async def _get_or_create_manifest(
    db: AsyncSession, closure: ClosureResult
) -> tuple[ORMClosureManifest, str]:
    """Dedup-by-content: the same closure under the same graph revision
    resolves to the same manifest row."""
    row, sha = _manifest_for(closure)
    existing = (
        (await db.execute(select(ORMClosureManifest).where(ORMClosureManifest.id == row.id)))
        .scalars()
        .first()
    )
    if existing is not None:
        return existing, sha
    db.add(row)
    await db.flush()
    return row, sha


async def _effective_snapshot_id(db: AsyncSession) -> uuid.UUID:
    """Locks the global runtime head (first canonical row) and returns
    the effective snapshot (pointer equality, §14.1)."""
    row = (
        await db.execute(
            text(
                "SELECT active_config_snapshot_id FROM runtime_config_heads "
                "WHERE scope = 'global' FOR UPDATE"
            )
        )
    ).scalar_one()
    return row if isinstance(row, uuid.UUID) else uuid.UUID(str(row))


async def _invalidate_head(
    db: AsyncSession,
    *,
    claim_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    prepared_by: str,
) -> str:
    """Idempotent head invalidation (step 3). Returns the prior state —
    'pending'/'invalid'/'missing' mean the batch was already applied to
    this claim and the job insert is skipped (idempotent batch, §8.6)."""
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
    state = row[0]
    return state if isinstance(state, str) else str(state)


async def _enqueue_job(
    db: AsyncSession,
    *,
    claim_id: uuid.UUID,
    snapshot_id: uuid.UUID,
    reason: str,
) -> bool:
    """Insert the durable reassessment job. Pre-check + the partial
    unique index (uq_reassessment_jobs_active) keep at most one active
    job per (claim, snapshot): a replayed batch is a no-op (§8.6).
    Returns True if a new row was inserted."""
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


async def _enqueue_invalid_question(
    db: AsyncSession,
    *,
    claim_id: uuid.UUID,
    snapshot_id: uuid.UUID,
) -> bool:
    """UUIDv5 question for a pending (reassessment-in-flight) claim —
    deterministic per (snapshot, claim) so replays never duplicate
    (pattern: offline rules atomic_publish)."""
    qid = uuid.uuid5(
        uuid.UUID(QUESTION_UUID5_NAMESPACE), f"cascade-invalidation:{snapshot_id}:{claim_id}"
    )
    exists = (
        (await db.execute(text("SELECT 1 FROM questions WHERE id = :q"), {"q": str(qid)}))
        .scalar_one_or_none()
    )
    if exists is not None:
        return False
    claim = await db.get(ORMClaim, claim_id)
    stmt = claim.statement if claim is not None else f"claim {claim_id}"
    db.add(
        ORMQuestion(
            id=qid,
            text=f"Переоценить утверждение (каскадная инвалидация): {stmt[:1900]}"[:2000],
            origin=QuestionOrigin.INVALID_ASSESSMENT.value,
        )
    )
    return True


async def _invalidate_batch(
    db: AsyncSession,
    *,
    claims: tuple[uuid.UUID, ...],
    snapshot_id: uuid.UUID,
    prepared_by: str,
    reason: str,
    audit: AuditService | None,
    session_id: uuid.UUID | None,
    actor: str,
    barrier_id: uuid.UUID | None,
    batch_offset: int | None,
) -> tuple[int, int]:
    """Apply one idempotent invalidation window (heads → jobs →
    questions). Returns (invalidated, jobs_created)."""
    invalidated = 0
    jobs_created = 0
    for claim_id in claims:
        prior = await _invalidate_head(
            db, claim_id=claim_id, snapshot_id=snapshot_id, prepared_by=prepared_by
        )
        if prior == "current":
            invalidated += 1
            if await _enqueue_job(
                db, claim_id=claim_id, snapshot_id=snapshot_id, reason=reason
            ):
                jobs_created += 1
            await _enqueue_invalid_question(db, claim_id=claim_id, snapshot_id=snapshot_id)
        # prior pending/invalid/missing → skip (idempotent batch)
    if audit is not None and barrier_id is not None:
        await audit.record(
            AuditEventType.BARRIER_BATCH_APPLIED,
            session_id=session_id,
            actor=actor,
            payload={
                "barrier_id": str(barrier_id),
                "batch_offset": batch_offset,
                "claim_count": len(claims),
                "invalidated": invalidated,
                "jobs_created": jobs_created,
            },
            public_summary=f"barrier {barrier_id}: batch at {batch_offset}",
        )
    return invalidated, jobs_created


async def _bump_knowledge_revision(db: AsyncSession) -> int:
    rev = (
        await db.execute(
            text(
                "UPDATE domain_revisions SET revision = revision + 1, updated_at = now() "
                "WHERE scope = 'knowledge' RETURNING revision"
            )
        )
    ).first()
    if rev is None:
        raise CascadeError("knowledge revision row missing under lock")
    return int(rev[0])


async def start_cascade(
    db: AsyncSession,
    *,
    root_claim_id: uuid.UUID,
    reason: str,
    actor: str = "system:cascade",
    session_id: uuid.UUID | None = None,
) -> CascadeStartResult:
    """Steps 1–5 of the §8.6 protocol.

    The closure is computed against the current graph revision (step 1,
    outside the write transaction); ONE short transaction then takes the
    writer gate + canonical locks, verifies the graph revision and
    applies root + closure (inline) or root + barrier (large). If the
    graph moved in between, ``CascadeError`` — the caller retries (the
    closure is cheap to recompute)."""
    root = root_claim_id if isinstance(root_claim_id, uuid.UUID) else uuid.UUID(str(root_claim_id))
    closure = await _load_closure(db, root)
    audit = AuditService(db)

    async with _writer_gate(db), transaction(db):
        # step 2 — canonical locks, verify the graph revision
        snapshot_id = await _effective_snapshot_id(db)
        await db.execute(
            text("SELECT scope FROM domain_revisions WHERE scope = 'knowledge' FOR UPDATE")
        )
        await db.execute(
            text(
                "SELECT scope FROM domain_revisions WHERE scope = 'dependency_graph' FOR UPDATE"
            )
        )
        locked_revision = await _graph_revision(db)
        if locked_revision != closure.graph_revision:
            raise CascadeError(
                "graph revision moved during closure computation "
                f"({closure.graph_revision} -> {locked_revision}); retry the cascade"
            )
        manifest, sha = await _get_or_create_manifest(db, closure)

        # step 3 — root head pending + its job (root is NOT in the
        # closure set)
        invalidated, jobs_created = await _invalidate_batch(
            db,
            claims=(root,),
            snapshot_id=snapshot_id,
            prepared_by=actor,
            reason=f"root: {reason}",
            audit=audit,
            session_id=session_id,
            actor=actor,
            barrier_id=None,
            batch_offset=None,
        )

        mode = "inline"
        barrier_id: uuid.UUID | None = None
        if closure.count <= INLINE_CLOSURE_LIMIT:
            # step 4 — whole closure in topological order, same txn
            inv, jobs = await _invalidate_batch(
                db,
                claims=closure.claim_ids,
                snapshot_id=snapshot_id,
                prepared_by=actor,
                reason=reason,
                audit=audit,
                session_id=session_id,
                actor=actor,
                barrier_id=None,
                batch_offset=None,
            )
            invalidated += inv
            jobs_created += jobs
        else:
            # step 5 — barrier + first batch, one transaction
            barrier = ORMDependencyInvalidationBarrier(
                id=uuid.uuid4(),
                root_claim_id=root,
                graph_revision=closure.graph_revision,
                generation=1,
                status=BarrierStatus.ACTIVE.value,
                closure_manifest_id=manifest.id,
                member_count=closure.count,
                next_offset=0,
            )
            db.add(barrier)
            await db.flush()
            first = closure.batch(0, BATCH_SIZE)
            inv, jobs = await _invalidate_batch(
                db,
                claims=first,
                snapshot_id=snapshot_id,
                prepared_by=actor,
                reason=reason,
                audit=audit,
                session_id=session_id,
                actor=actor,
                barrier_id=barrier.id,
                batch_offset=0,
            )
            invalidated += inv
            jobs_created += jobs
            barrier.next_offset = len(first)
            if barrier.next_offset < closure.count:
                barrier.status = BarrierStatus.ACTIVE.value
            else:
                barrier.status = BarrierStatus.CLOSING.value
            barrier_id = barrier.id
            mode = "barrier"

        knowledge_revision: int | None = None
        if invalidated > 0:
            # the knowledge changed: in-flight session commits must
            # fence against it (fencing predicate honesty)
            knowledge_revision = await _bump_knowledge_revision(db)

        await audit.record(
            AuditEventType.CASCADE_STARTED,
            session_id=session_id,
            actor=actor,
            payload={
                "root_claim_id": str(root),
                "manifest_sha256": sha,
                "closure_count": closure.count,
                "mode": mode,
                "barrier_id": str(barrier_id) if barrier_id else None,
                "invalidated": invalidated,
                "jobs_created": jobs_created,
                "graph_revision": closure.graph_revision,
                "knowledge_revision": knowledge_revision,
            },
            public_summary=f"cascade from {root}: closure={closure.count} mode={mode}",
        )

    return CascadeStartResult(
        root=root,
        manifest_sha256=sha,
        closure_count=closure.count,
        mode=mode,
        barrier_id=barrier_id,
        invalidated=invalidated,
        jobs_created=jobs_created,
        graph_revision=closure.graph_revision,
        knowledge_revision=knowledge_revision,
    )


def _verify_manifest(manifest: ORMClosureManifest) -> str | None:
    """Tamper check of the immutable manifest: recompute the content
    hash from the row's own fields. A mismatch = blocked (never a
    silent re-computation, §8.6)."""
    body: dict[str, Any] = {
        "root": str(manifest.root_claim_id),
        "graph_revision": manifest.graph_revision,
        "claim_ids": list(manifest.claim_ids),
        "ranks": dict(manifest.ranks),
        "count": manifest.count,
    }
    sha = sha256_hex(canonical_json_bytes(body))
    return None if sha == manifest.sha256 else "manifest_hash_mismatch"


async def _live_current_descendants(
    db: AsyncSession, root: uuid.UUID, snapshot_id: uuid.UUID
) -> list[uuid.UUID]:
    """The final check (§8.6): recompute the live reverse closure under
    the graph lock and list its members whose effective head is still
    current."""
    edges = await _evidential_edges(db)
    closure = compute_reverse_closure(edges, root, await _graph_revision(db))
    if not closure.claim_ids:
        return []
    rows = await db.execute(
        text(
            "SELECT claim_id FROM claim_assessment_heads "
            "WHERE config_snapshot_id = :s AND assessment_state = 'current' "
            "AND claim_id = ANY(:ids)"
        ),
        {"s": snapshot_id, "ids": [str(c) for c in closure.claim_ids]},
    )
    return [r[0] if isinstance(r[0], uuid.UUID) else uuid.UUID(str(r[0])) for r in rows.all()]


async def _block_barrier(
    db: AsyncSession,
    barrier: ORMDependencyInvalidationBarrier,
    audit: AuditService,
    actor: str,
    *,
    error_class: str,
    message: str,
    session_id: uuid.UUID | None,
) -> None:
    """Irreversible without an audited operator recovery (§8.6): the
    barrier keeps ancestor protection and never auto-closes."""
    barrier.status = BarrierStatus.BLOCKED.value
    barrier.last_error = f"{error_class}: {message}"
    await audit.record(
        AuditEventType.BARRIER_BLOCKED,
        session_id=session_id,
        actor=actor,
        payload={
            "barrier_id": str(barrier.id),
            "root_claim_id": str(barrier.root_claim_id),
            "generation": barrier.generation,
            "error_class": error_class,
            "message": message,
        },
        public_summary=f"barrier {barrier.id} blocked: {error_class}",
    )


async def process_barrier(
    db: AsyncSession,
    barrier_id: uuid.UUID,
    *,
    actor: str = "system:barrier",
    session_id: uuid.UUID | None = None,
) -> BarrierStatus:
    """One step of the barrier processor (step 6) — crash-resumable:
    it always continues from the durable cursor. Call again while the
    result is discovering/active/closing. ``blocked`` raises
    ``BarrierBlockedError``; ``resolved`` returns idempotently."""
    barrier_id = barrier_id if isinstance(barrier_id, uuid.UUID) else uuid.UUID(str(barrier_id))
    audit = AuditService(db)

    async with _writer_gate(db), transaction(db):
        barrier = (
            (
                await db.execute(
                    select(ORMDependencyInvalidationBarrier)
                    .where(ORMDependencyInvalidationBarrier.id == barrier_id)
                    .with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if barrier is None:
            raise LookupError(f"barrier {barrier_id} not found")
        status = BarrierStatus(barrier.status)
        if status is BarrierStatus.RESOLVED:
            return status
        if status is BarrierStatus.BLOCKED:
            raise BarrierBlockedError(
                f"barrier {barrier.id} is blocked: {barrier.last_error}"
            )

        # canonical row locks: head → knowledge → graph
        snapshot_id = await _effective_snapshot_id(db)
        await db.execute(
            text("SELECT scope FROM domain_revisions WHERE scope = 'knowledge' FOR UPDATE")
        )
        await db.execute(
            text(
                "SELECT scope FROM domain_revisions WHERE scope = 'dependency_graph' FOR UPDATE"
            )
        )
        live_revision = await _graph_revision(db)

        if status is BarrierStatus.DISCOVERING or (
            status is BarrierStatus.ACTIVE and live_revision != barrier.graph_revision
        ):
            # the graph moved: publish a new generation from the
            # fresh closure (edges read under the graph lock — a
            # consistent snapshot; §8.6: already processed claims
            # are safely skipped). The barrier row IS the
            # generation: it moves to generation+1 with cursor 0.
            closure = await _load_closure(db, barrier.root_claim_id)
            gen_manifest, gen_sha = await _get_or_create_manifest(db, closure)
            new_gen = barrier.generation + 1
            barrier.generation = new_gen
            barrier.graph_revision = closure.graph_revision
            barrier.closure_manifest_id = gen_manifest.id
            barrier.member_count = closure.count
            barrier.next_offset = 0
            barrier.status = (
                BarrierStatus.CLOSING.value
                if closure.count == 0
                else BarrierStatus.ACTIVE.value
            )
            barrier.last_error = None
            await audit.record(
                AuditEventType.BARRIER_GENERATION_PUBLISHED,
                session_id=session_id,
                actor=actor,
                payload={
                    "barrier_id": str(barrier.id),
                    "root_claim_id": str(barrier.root_claim_id),
                    "generation": new_gen,
                    "manifest_sha256": gen_sha,
                    "graph_revision": closure.graph_revision,
                    "member_count": closure.count,
                },
                public_summary=(
                    f"barrier {barrier.id}: generation {new_gen} "
                    f"(closure={closure.count})"
                ),
            )

        invalidated_total = 0
        if barrier.status == BarrierStatus.ACTIVE.value:
            manifest = (
                (
                    await db.execute(
                        select(ORMClosureManifest).where(
                            ORMClosureManifest.id == barrier.closure_manifest_id
                        )
                    )
                )
                .scalars()
                .first()
            )
            if manifest is None:
                await _block_barrier(
                    db, barrier, audit, actor,
                    error_class="manifest_missing",
                    message="closure manifest row missing under lock",
                    session_id=session_id,
                )
                return BarrierStatus.BLOCKED
            problem = _verify_manifest(manifest)
            if problem is not None:
                await _block_barrier(
                    db, barrier, audit, actor, error_class=problem,
                    message="immutable manifest hash mismatch",
                    session_id=session_id,
                )
                return BarrierStatus.BLOCKED
            ordered = tuple(_as_uuid(c) for c in manifest.claim_ids)
            assert len(ordered) == manifest.count  # CHECK (count = len(claim_ids))
            if barrier.next_offset > manifest.count:
                await _block_barrier(
                    db, barrier, audit, actor, error_class="impossible_cursor",
                    message=f"next_offset {barrier.next_offset} > count {manifest.count}",
                    session_id=session_id,
                )
                return BarrierStatus.BLOCKED
            if barrier.next_offset < manifest.count:
                window = ordered[barrier.next_offset : barrier.next_offset + BATCH_SIZE]
                inv, _jobs = await _invalidate_batch(
                    db,
                    claims=window,
                    snapshot_id=snapshot_id,
                    prepared_by=actor,
                    reason="barrier batch",
                    audit=audit,
                    session_id=session_id,
                    actor=actor,
                    barrier_id=barrier.id,
                    batch_offset=barrier.next_offset,
                )
                invalidated_total = inv
                barrier.next_offset += len(window)
            if barrier.next_offset >= manifest.count:
                barrier.status = BarrierStatus.CLOSING.value

        # final closure scan before resolved (§8.6): the manifest is
        # fully walked AND no current descendant remains in the
        # live closure
        if barrier.status == BarrierStatus.CLOSING.value:
            current_descendants = await _live_current_descendants(
                db, barrier.root_claim_id, snapshot_id
            )
            if live_revision != barrier.graph_revision or current_descendants:
                barrier.status = BarrierStatus.DISCOVERING.value
                barrier.last_error = (
                    None
                    if live_revision == barrier.graph_revision
                    else (
                        "graph revision moved: "
                        f"{barrier.graph_revision} -> {live_revision}"
                    )
                )
            else:
                # ORM expression (flushed with status at commit — the
                # CHECK (resolved_at IS NULL) = (status <> 'resolved')
                # is evaluated per-row, so the two must land together)
                barrier.status = BarrierStatus.RESOLVED.value
                barrier.resolved_at = func.now()
                await audit.record(
                    AuditEventType.BARRIER_RESOLVED,
                    session_id=session_id,
                    actor=actor,
                    payload={
                        "barrier_id": str(barrier.id),
                        "root_claim_id": str(barrier.root_claim_id),
                        "generation": barrier.generation,
                        "manifest_sha256": (
                            (
                                await db.execute(
                                    select(ORMClosureManifest.sha256).where(
                                        ORMClosureManifest.id == barrier.closure_manifest_id
                                    )
                                )
                            )
                            .scalars()
                            .first()
                        ),
                        "member_count": barrier.member_count,
                    },
                    public_summary=f"barrier {barrier.id} resolved",
                )

        if invalidated_total > 0:
            # the knowledge changed: in-flight session commits must
            # fence against it (fencing predicate honesty)
            await _bump_knowledge_revision(db)

        await db.execute(
            text(
                "UPDATE dependency_invalidation_barriers "
                "SET updated_at = clock_timestamp() WHERE id = :i"
            ),
            {"i": barrier.id},
        )
        final = BarrierStatus(barrier.status)

    return final


async def protected_claim_ids(db: AsyncSession) -> set[uuid.UUID]:
    """The ancestor-check set (§8.6): every claim in the closure of an
    OPEN barrier (discovering/active/closing/blocked) must not be
    treated as current by retrieval/rules. A discovering barrier carries
    no manifest yet — its root is already pending (invalidated by the
    start transaction), so the manifest union is the protection set."""
    rows = await db.execute(
        text(
            "SELECT m.claim_ids FROM closure_manifests m "
            "JOIN dependency_invalidation_barriers b ON b.closure_manifest_id = m.id "
            "WHERE b.status IN ('discovering','active','closing','blocked')"
        )
    )
    protected: set[uuid.UUID] = set()
    for (ids,) in rows.all():
        for raw in ids if isinstance(ids, list) else []:
            protected.add(raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw)))
    return protected
