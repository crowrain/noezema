"""Reassessment worker (T4.3, §5.9.1, §8.7, §14.1).

The trusted-contour worker with actor ``system:reassessment`` drains the
durable ``reassessment_jobs`` queue. It re-evaluates a claim's head for
its ``target_config_snapshot_id`` using ONLY that snapshot's rules and
the stored evidence set: no LLM, no network, no new evidence (§5.9.1).

Protocol:

- **Runnability predicate** (the only definition of runnable): the job
  has ``status IN ('queued','retry')``, ``next_attempt_at`` has arrived
  (or is NULL), the retry budget is not exhausted, its target equals the
  effective runtime pointer AND the activating slot is empty. The
  literal ``config_snapshots.activation_state`` is NOT part of the
  predicate (§5.9.1, §14.1).
- **Lease**: a short admission transaction takes the writer gate + the
  canonical head lock and leases a bounded batch (status ``leased``,
  ``lease_owner``, ``lease_expires_at``, ``attempts+1``).
- **Per-job transaction**: re-checks the admission under the same locks
  (an activation that appeared during validation sends the job back to
  the queue, un-leased — the session always wins), re-runs the rules
  engine on the stored evidence, and writes the new assessment + head
  (``prepared_by='reassessment_worker'``) + knowledge bump as ONE
  transaction. Insufficient data (no evidence) moves the target head to
  ``invalid`` and creates a research question.
- **Error classes** (trusted code decides): transient → ``retry`` with
  exponential backoff + jitter until ``max_attempts``; deterministic or
  exhausted budget → ``blocked`` (head kept invalid + critical alert),
  never auto-cleared — a blocked job is not runnable and never blocks
  global wake (§5.9.1).
- **Crash**: the lease transaction and the job transaction are separate
  — a crash between them leaves a ``leased`` row; ``recover_expired_leases``
  (called by the scheduler before a batch) returns expired leases to the
  queue, restoring the consumed attempt (the atomic job tx either
  committed — job completed — or rolled back).

The writer admission of §5.9.1 (T4.4): the worker takes the
``knowledge_write_gate`` NOWAIT (a live foreign holder defers the batch
with jitter, rule 3), yields to the session commit intent at admission
AND mid-batch (rule 4: the prepared batch is not committed, jobs go back
to the queue with the attempt restored), and the ``T_escalate`` /
``T_worker_admission`` wake gates live in the scheduler (the queue gets
the window between sessions, §5.9.1 liveness).
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.config import QUESTION_UUID5_NAMESPACE
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import (
    AssessmentState,
    AuditEventType,
    AuditVisibility,
    FreshnessStatus,
    QuestionOrigin,
    ReassessmentJobStatus,
)
from packages.domain.models.memory import (
    ORMAssessmentEvidence,
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMEvidence,
    ORMReassessmentJob,
)
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.audit import AuditService
from packages.memory.cascade import writer_gate
from packages.memory.env_independence import (
    UNTRACKED_GROUP,
    build_environment_independence_snapshot,
)
from packages.memory.evidence import rules_hash
from packages.memory.rules_engine import (
    RULES_ENGINE_VERSION,
    EvaluatedEvidence,
    RuleValidationError,
    evaluate,
    reverify_after,
)
from packages.memory.service import MemoryService, _evidence_set_hash
from packages.memory.writer_gate import (
    GATE_PRIORITY_WORKER,
    OWNER_WORKER,
    acquire_writer_gate,
    active_session_intent,
    release_writer_gate,
)

#: the worker's actor (closed prepared_by enum value, §14.1)
ACTOR = "system:reassessment"

#: default lease: one full batch of reassessments on target hardware
DEFAULT_LEASE_SECONDS = 300

#: default bounded batch per batch run (§5.9.1 "ограниченными батчами")
DEFAULT_BATCH_SIZE = 8

#: backoff base/cap (§5.9.1: backoff/jitter до max_attempts)
_BACKOFF_BASE_SECONDS = 30
_BACKOFF_CAP_SECONDS = 3600
_JITTER_SECONDS = 15.0

#: the writer-gate lease: long enough for one full batch (8 jobs), short
#: enough that a crashed worker does not block activation acquisition
#: (T4.5) for more than ten minutes
DEFAULT_GATE_LEASE_SECONDS = 600

#: gate-conflict deferral (§5.9.1 rule 3: «при конфликте откладывает job
#: с jitter»): now() + 5..15 s
_DEFER_BASE_SECONDS = 5.0
_DEFER_JITTER_SECONDS = 10.0


class ReassessmentWorkerError(RuntimeError):
    """The worker could not run at all (writer gate busy)."""


@dataclass(frozen=True)
class WorkerOutcome:
    #: jobs leased in this run
    processed: int
    #: finished with a new current head
    completed: int
    #: sent to retry (transient error or admission lost mid-batch)
    retried: int
    #: blocked (permanent error or exhausted budget)
    blocked: int
    #: nothing was leased (no runnable jobs / activating slot / gate)
    deferred: bool


def _backoff_seconds(attempts: int) -> float:
    """Exponential backoff with jitter; ``attempts`` is the count AFTER
    the failed attempt (the lease increments it)."""
    exp = min(max(attempts - 1, 0), 7)
    base = float(min(_BACKOFF_CAP_SECONDS, _BACKOFF_BASE_SECONDS * (2**exp)))
    return base + random.uniform(0.0, _JITTER_SECONDS)


def _as_uuid(raw: object) -> uuid.UUID:
    return raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))


async def _head_row(db: AsyncSession) -> tuple[uuid.UUID, uuid.UUID | None] | None:
    """Lock the global runtime head (first canonical row)."""
    row = (
        await db.execute(
            text(
                "SELECT active_config_snapshot_id, activating_config_snapshot_id "
                "FROM runtime_config_heads WHERE scope = 'global' FOR UPDATE"
            )
        )
    ).first()
    if row is None:
        return None
    active = row[0] if isinstance(row[0], uuid.UUID) else uuid.UUID(str(row[0]))
    activating = row[1] if isinstance(row[1], uuid.UUID) else None
    return active, activating


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
        raise ReassessmentWorkerError("knowledge revision row missing under lock")
    return int(rev[0])


async def _latest_assessed_scope(
    db: AsyncSession, claim_id: uuid.UUID, snapshot_id: uuid.UUID
) -> JsonDict:
    """The claim scope of the newest assessment of the claim (scope is
    not a column of ``claims``; it is carried by the assessments that
    assessed the claim — the claim's scope is stable across snapshots,
    so the newest known assessment is the best source, including after
    the head was invalidated and the old assessment is no longer
    referenced by it)."""
    row = (
        await db.execute(
            text(
                "SELECT a.assessed_scope FROM claim_assessments a "
                "WHERE a.claim_id = :c "
                "ORDER BY a.created_at DESC, a.id DESC LIMIT 1"
            ),
            {"c": claim_id},
        )
    ).first()
    if row is None or row[0] is None:
        return {}
    return dict(row[0])


async def _enqueue_insufficient_question(
    db: AsyncSession,
    *,
    claim: ORMClaim,
    snapshot_id: uuid.UUID,
) -> uuid.UUID | None:
    """Deterministic UUIDv5 research question for an unassessable claim
    (pattern: cascade invalidation / offline rules)."""
    qid = uuid.uuid5(
        uuid.UUID(QUESTION_UUID5_NAMESPACE),
        f"reassessment-insufficient:{snapshot_id}:{claim.id}",
    )
    exists = (
        await db.execute(text("SELECT 1 FROM questions WHERE id = :q"), {"q": str(qid)})
    ).scalar_one_or_none()
    if exists is not None:
        return qid
    db.add(
        ORMQuestion(
            id=qid,
            text=f"Недостаточно данных для переоценки утверждения: {claim.statement[:1900]}"[:2000],
            origin=QuestionOrigin.INVALID_ASSESSMENT.value,
            origin_config_snapshot_id=snapshot_id,
        )
    )
    return qid


async def _set_head_invalid(
    db: AsyncSession, claim_id: uuid.UUID, snapshot_id: uuid.UUID
) -> str:
    """pending → invalid (or keep invalid): current fields stay NULL per
    the lifecycle invariant. Returns the prior state — 'pending' means
    the knowledge actually changed (the caller bumps the knowledge
    revision, the same fencing-honesty rule as the cascade)."""
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
    prior = row[0] if isinstance(row[0], str) else str(row[0])
    if prior in ("pending", "invalid"):
        await db.execute(
            text(
                "UPDATE claim_assessment_heads SET assessment_state = 'invalid', "
                "updated_at = clock_timestamp() "
                "WHERE claim_id = :c AND config_snapshot_id = :s"
            ),
            {"c": claim_id, "s": snapshot_id},
        )
    return prior


async def _block_job(
    db: AsyncSession,
    audit: AuditService,
    job: ORMReassessmentJob,
    *,
    error_class: str,
    message: str,
    prior_head_state: str,
    question_id: uuid.UUID | None,
) -> None:
    """Terminal blocked state (§5.9.1): the claim stays invalid, the job
    keeps its history, a critical alert is raised. Restarting a blocked
    job is an audited operator action (new attempt row), not a worker
    path."""
    now = text("clock_timestamp()")
    job.status = ReassessmentJobStatus.BLOCKED.value
    job.error_class = error_class
    job.last_error = message[:2000]
    job.blocked_at = now  # type: ignore[assignment]
    job.lease_owner = None
    job.lease_expires_at = None
    await audit.record(
        AuditEventType.REASSESSMENT_JOB_BLOCKED,
        actor=ACTOR,
        payload={
            "job_id": str(job.id),
            "claim_id": str(job.claim_id),
            "target_config_snapshot_id": str(job.target_config_snapshot_id),
            "attempts": job.attempts,
            "error_class": error_class,
            "prior_head_state": prior_head_state,
            "question_id": str(question_id) if question_id else None,
        },
        public_summary=f"reassessment job {job.id} blocked: {error_class}",
    )
    await audit.record(
        AuditEventType.ALERT_RAISED,
        actor=ACTOR,
        payload={
            "alert_class": "reassessment_job_blocked",
            "job_id": str(job.id),
            "claim_id": str(job.claim_id),
            "error_class": error_class,
        },
        public_summary=f"alert: reassessment job blocked ({error_class})",
        visibility=AuditVisibility.DIAGNOSTIC,
    )


async def _process_one_job(
    db: AsyncSession,
    job_id: uuid.UUID,
    *,
    audit: AuditService,
) -> str:
    """One leased job inside its own short transaction (the job row is
    re-fetched under FOR UPDATE HERE, so the lease lock survives until
    the job commits). Returns one of
    'completed' | 'retried' | 'blocked' | 'unleased' | 'gone'."""
    job = (
        (
            await db.execute(
                select(ORMReassessmentJob).where(ORMReassessmentJob.id == job_id).with_for_update()
            )
        )
        .scalars()
        .first()
    )
    if job is None or job.status != ReassessmentJobStatus.LEASED.value:
        # crash-recovered or changed between the lease tx and this one
        return "gone"
    head = await _head_row(db)
    if head is None:
        # impossible under the bootstrap invariant; treat as transient
        raise ReassessmentWorkerError("runtime config head missing")
    effective, activating = head
    if activating is not None or effective != _as_uuid(job.target_config_snapshot_id):
        # an activation (or a config change) appeared during validation:
        # the session/activation wins — back to the queue, attempt
        # restored (no work was done in this transaction)
        await _unlease(db, (job,))
        return "unleased"
    if await active_session_intent(db):
        # §5.9.1 rule 4: a session commit intent appeared during
        # validation — the worker does not commit the prepared batch;
        # the job goes back to the queue with the attempt restored
        await _unlease(db, (job,))
        return "intent_conflict"

    snapshot = await db.get(ORMConfigSnapshot, effective)
    claim = await db.get(ORMClaim, _as_uuid(job.claim_id))
    if snapshot is None or claim is None:
        # permanent: the row the job points at is gone
        question_id = await _enqueue_insufficient_question(
            db, claim=claim, snapshot_id=effective
        ) if claim is not None else None
        prior = "missing" if claim is None else await _set_head_invalid(db, claim.id, effective)
        if prior == "pending":
            await _bump_knowledge_revision(db)
        await _block_job(
            db,
            audit,
            job,
            error_class="claim_or_config_missing",
            message=f"claim={claim is not None} snapshot={snapshot is not None}",
            prior_head_state=prior,
            question_id=question_id,
        )
        return "blocked"

    service = MemoryService(snapshot)
    evidence = (
        (
            await db.execute(select(ORMEvidence).where(ORMEvidence.claim_id == claim.id))
        )
        .scalars()
        .all()
    )
    scope = await _latest_assessed_scope(db, claim.id, effective)
    now = datetime.now(UTC)

    if not evidence:
        # §5.9.1: "Если данных недостаточно, он переводит target head в
        # `invalid` и создаёт исследовательский вопрос"
        prior = await _set_head_invalid(db, claim.id, effective)
        if prior == "pending":
            await _bump_knowledge_revision(db)
        question_id = await _enqueue_insufficient_question(
            db, claim=claim, snapshot_id=effective
        )
        job.status = ReassessmentJobStatus.COMPLETED.value
        job.completed_at = text("clock_timestamp()")  # type: ignore[assignment]
        job.lease_owner = None
        job.lease_expires_at = None
        await audit.record(
            AuditEventType.REASSESSMENT_JOB_COMPLETED,
            actor=ACTOR,
            payload={
                "job_id": str(job.id),
                "claim_id": str(claim.id),
                "target_config_snapshot_id": str(effective),
                "attempt": job.attempts,
                "outcome": "insufficient_data",
                "prior_head_state": prior,
                "question_id": str(question_id) if question_id else None,
            },
            public_summary=f"reassessment {claim.id}: insufficient data → invalid",
        )
        return "completed"

    # §8.7.3 (T4.6): the versioned environment-independence snapshot —
    # the same groups/relations the commit path computes; the assessment
    # fixes it. Evidence without a tracked environment is the
    # conservative untracked group (never fake-independent).
    env_snapshot_id, env_mapping = await build_environment_independence_snapshot(
        db, claim_id=claim.id, rules_hash=rules_hash(dict(snapshot.claim_type_rules))
    )
    group_by_manifest = {mid: g for mid, (g, _r) in env_mapping.items()}
    relation_by_manifest = {mid: r for mid, (_g, r) in env_mapping.items()}
    evs = [
        EvaluatedEvidence(
            identity_hash=e.identity_hash,
            kind=e.evidence_kind,
            relation=e.relation,
            scope=dict(e.scope or {}),
            independence_group=(
                group_by_manifest.get(e.environment_manifest_id, UNTRACKED_GROUP)
                if e.environment_manifest_id is not None
                else UNTRACKED_GROUP
            ),
            env_relation=(
                relation_by_manifest.get(e.environment_manifest_id, "none")
                if e.environment_manifest_id is not None
                else "none"
            ),
        )
        for e in evidence
    ]
    result = evaluate(
        claim.claim_type,
        service.rule(claim.claim_type),
        claim_scope=scope,
        evidences=evs,
        has_as_of=claim.as_of is not None,
    )

    head_row = (
        (
            await db.execute(
                select(ORMClaimAssessmentHead).where(
                    ORMClaimAssessmentHead.claim_id == claim.id,
                    ORMClaimAssessmentHead.config_snapshot_id == effective,
                )
            )
        )
        .scalars()
        .first()
    )
    if head_row is None:
        await _block_job(
            db,
            audit,
            job,
            error_class="head_missing",
            message="no assessment head under the target snapshot",
            prior_head_state="missing",
            question_id=None,
        )
        return "blocked"

    if head_row.assessment_state == AssessmentState.CURRENT.value:
        # the claim was re-assessed concurrently (a session commit won
        # the head): the worker does not overwrite session work — the
        # job is satisfied by the existing current head
        job.status = ReassessmentJobStatus.COMPLETED.value
        job.completed_at = text("clock_timestamp()")  # type: ignore[assignment]
        job.lease_owner = None
        job.lease_expires_at = None
        await audit.record(
            AuditEventType.REASSESSMENT_JOB_COMPLETED,
            actor=ACTOR,
            payload={
                "job_id": str(job.id),
                "claim_id": str(claim.id),
                "attempt": job.attempts,
                "outcome": "head_reassessed_externally",
            },
            public_summary=f"reassessment {claim.id}: head already current",
        )
        return "completed"

    # new assessment + head promotion (rules engine is the only
    # producer of grade/confidence, §3.7)
    if head_row.current_assessment_id is not None:
        prev = await db.get(ORMClaimAssessment, head_row.current_assessment_id)
        if prev is not None and prev.valid:
            prev.valid = False
            prev.invalidation_reason = "superseded"

    assessment = ORMClaimAssessment(
        id=uuid.uuid4(),
        claim_id=claim.id,
        effective_grade=result.grade.value,
        epistemic_status=result.epistemic_status.value,
        rules_version=RULES_ENGINE_VERSION,
        rules_hash=rules_hash(dict(snapshot.claim_type_rules)),
        environment_independence_snapshot_id=env_snapshot_id,
        evidence_set_hash=_evidence_set_hash(list(evidence)),
        assessed_scope=dict(scope),
        confidence=result.confidence,
        valid=True,
    )
    db.add(assessment)
    await db.flush()
    for e in evidence:
        db.add(
            ORMAssessmentEvidence(
                assessment_id=assessment.id,
                evidence_id=e.id,
                role="counter" if e.relation == "counters" else "support",
            )
        )

    head_row.assessment_state = AssessmentState.CURRENT.value
    head_row.current_assessment_id = assessment.id
    head_row.epistemic_status = result.epistemic_status.value
    head_row.prepared_by = "reassessment_worker"
    claim.reverify_after = reverify_after(result, claim.as_of, now)
    claim.freshness_status = (
        FreshnessStatus.FRESH.value
        if now < claim.reverify_after
        else FreshnessStatus.DUE.value
    )

    job.status = ReassessmentJobStatus.COMPLETED.value
    job.completed_at = text("clock_timestamp()")  # type: ignore[assignment]
    job.lease_owner = None
    job.lease_expires_at = None
    await _bump_knowledge_revision(db)
    await db.flush()
    await audit.record(
        AuditEventType.REASSESSMENT_JOB_COMPLETED,
        actor=ACTOR,
        payload={
            "job_id": str(job.id),
            "claim_id": str(claim.id),
            "assessment_id": str(assessment.id),
            "target_config_snapshot_id": str(effective),
            "attempt": job.attempts,
            "grade": result.grade.value,
            "epistemic_status": result.epistemic_status.value,
            "confidence": result.confidence,
            "reasons": list(result.reasons),
        },
        public_summary=(
            f"reassessment {claim.id}: {result.grade.value}/"
            f"{result.epistemic_status.value}"
        ),
    )
    return "completed"


async def _unlease(db: AsyncSession, jobs: tuple[ORMReassessmentJob, ...]) -> None:
    """Return jobs to the queue WITHOUT consuming the attempt (no work
    happened): status queued, lease cleared, attempts restored."""
    for job in jobs:
        job.status = ReassessmentJobStatus.QUEUED.value
        job.lease_owner = None
        job.lease_expires_at = None
        job.attempts = max(0, (job.attempts or 0) - 1)


async def _retry_job(
    db: AsyncSession,
    audit: AuditService,
    job: ORMReassessmentJob,
    *,
    error_class: str,
    message: str,
) -> None:
    """Transient error: back off and try again until max_attempts."""
    delay = timedelta(seconds=_backoff_seconds(job.attempts))
    job.status = ReassessmentJobStatus.RETRY.value
    job.error_class = error_class
    job.last_error = message[:2000]
    job.lease_owner = None
    job.lease_expires_at = None
    job.next_attempt_at = func.now() + delay
    await audit.record(
        AuditEventType.REASSESSMENT_JOB_RETRY,
        actor=ACTOR,
        payload={
            "job_id": str(job.id),
            "claim_id": str(job.claim_id),
            "attempt": job.attempts,
            "max_attempts": job.max_attempts,
            "error_class": error_class,
        },
        public_summary=f"reassessment job {job.id}: retry {job.attempts}/{job.max_attempts}",
    )


async def _fetch_job(
    db: AsyncSession, job_id: uuid.UUID, *, lock: bool
) -> ORMReassessmentJob | None:
    stmt = select(ORMReassessmentJob).where(ORMReassessmentJob.id == job_id)
    if lock:
        stmt = stmt.with_for_update()
    return ((await db.execute(stmt)).scalars().first())


async def _block_lost_job(
    db: AsyncSession,
    audit: AuditService,
    job_id: uuid.UUID,
    *,
    error_class: str,
    message: str,
    with_question: bool,
) -> bool:
    """Block a job whose evaluation tx rolled back (the row is still
    ``leased``). Re-fetches under FOR UPDATE inside ONE transaction and
    no-ops if the lease was already taken over (crash recovery)."""
    async with transaction(db):
        job = await _fetch_job(db, job_id, lock=True)
        if job is None or job.status != ReassessmentJobStatus.LEASED.value:
            return False
        prior = await _set_head_invalid(
            db, _as_uuid(job.claim_id), _as_uuid(job.target_config_snapshot_id)
        )
        if prior == "pending":
            await _bump_knowledge_revision(db)
        claim = (
            await db.get(ORMClaim, _as_uuid(job.claim_id)) if with_question else None
        )
        question_id = (
            await _enqueue_insufficient_question(
                db,
                claim=claim,
                snapshot_id=_as_uuid(job.target_config_snapshot_id),
            )
            if claim is not None
            else None
        )
        await _block_job(
            db,
            audit,
            job,
            error_class=error_class,
            message=message,
            prior_head_state=prior,
            question_id=question_id,
        )
    return True


async def _retry_lost_job(
    db: AsyncSession,
    audit: AuditService,
    job_id: uuid.UUID,
    *,
    error_class: str,
    message: str,
) -> bool:
    """Same as ``_block_lost_job`` for the transient path."""
    async with transaction(db):
        job = await _fetch_job(db, job_id, lock=True)
        if job is None or job.status != ReassessmentJobStatus.LEASED.value:
            return False
        await _retry_job(db, audit, job, error_class=error_class, message=message)
    return True


async def _defer_runnable_with_jitter(
    db: AsyncSession, *, effective: uuid.UUID, limit: int
) -> None:
    """§5.9.1 rule 3: a NOWAIT gate conflict defers the jobs with jitter
    (now() + 5..15 s) instead of queueing them — the worker retries in
    the gap and never fights the current gate holder. Only jobs that are
    DUE now are touched (a job already inside its backoff window keeps
    its schedule)."""
    await db.execute(
        text(
            f"""
            UPDATE reassessment_jobs
            SET next_attempt_at = now()
                + interval '{_DEFER_BASE_SECONDS:g} seconds'
                + (random() * {_DEFER_JITTER_SECONDS:g}) * interval '1 second'
            WHERE id IN (
                SELECT id FROM reassessment_jobs
                WHERE status IN ('queued','retry')
                  AND target_config_snapshot_id = :eff
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                  AND attempts < max_attempts
                ORDER BY priority DESC, enqueued_at ASC, id
                LIMIT :n
            )
            """
        ),
        {"eff": effective, "n": limit},
    )


async def run_reassessment_batch(
    db: AsyncSession,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    gate_lease_seconds: int = DEFAULT_GATE_LEASE_SECONDS,
) -> WorkerOutcome:
    """One bounded batch of the reassessment worker (§5.9.1).

    Transaction 1 (admission + lease): the knowledge writer gate is
    acquired NOWAIT (a live foreign holder defers the batch with jitter,
    rule 3), a live session commit intent aborts the batch (rule 4),
    then the canonical head lock + the exact runnability predicate lease
    a bounded batch.
    Transactions 2..N+1 (one per job): the job row is re-fetched under
    FOR UPDATE INSIDE the job transaction (the lease lock survives
    until the job commits), the admission is re-checked under the same
    head lock (an activation or a session intent that appeared during
    validation sends the job back to the queue — the session always
    wins), then evaluation and write/retry/blocked. Each job commits on
    its own so a failure never poisons the rest of the batch; a crash
    between the lease tx and the job tx is recoverable via
    ``recover_expired_leases`` (and the gate lease bounds the gate
    crash window). The gate is released on the success paths; a crash
    leaves the holder row for the lease TTL."""
    audit = AuditService(db)

    async with writer_gate(db):
        async with transaction(db):
            # §5.9.1 rule 3: the worker takes the gate NOWAIT
            acquired = await acquire_writer_gate(
                db,
                owner_kind=OWNER_WORKER,
                owner_id=ACTOR,
                priority=GATE_PRIORITY_WORKER,
                lease_seconds=gate_lease_seconds,
            )
            if not acquired:
                # a live foreign holder (activation, or another writer
                # class) — defer the due jobs with jitter, no lease
                head = await _head_row(db)
                if head is None:
                    raise ReassessmentWorkerError("runtime config head missing")
                await _defer_runnable_with_jitter(db, effective=head[0], limit=batch_size)
                return WorkerOutcome(0, 0, 0, 0, True)
            if await active_session_intent(db):
                # rule 4: a session declares the priority writer intent —
                # no batch, no lease; the session wins
                await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id=ACTOR)
                return WorkerOutcome(0, 0, 0, 0, True)
            head = await _head_row(db)
            if head is None:
                raise ReassessmentWorkerError("runtime config head missing")
            effective, activating = head
            if activating is not None:
                # §5.9.1 step 7: activating candidate present — no
                # validation/write batch, jobs stay in the queue
                await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id=ACTOR)
                return WorkerOutcome(0, 0, 0, 0, True)
            rows = (
                await db.execute(
                    text(
                        "SELECT id FROM reassessment_jobs "
                        "WHERE status IN ('queued','retry') "
                        "AND target_config_snapshot_id = :eff "
                        "AND (next_attempt_at IS NULL OR next_attempt_at <= now()) "
                        "AND attempts < max_attempts "
                        "ORDER BY priority DESC, enqueued_at ASC, id "
                        "LIMIT :n FOR UPDATE"
                    ),
                    {"eff": effective, "n": batch_size},
                )
            ).scalars().all()
            if not rows:
                await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id=ACTOR)
                return WorkerOutcome(0, 0, 0, 0, True)
            job_ids = [_as_uuid(r) for r in rows]
            await db.execute(
                text(
                    "UPDATE reassessment_jobs SET status = 'leased', "
                    "lease_owner = :o, lease_expires_at = now() + make_interval(secs => :l), "
                    "attempts = attempts + 1 "
                    "WHERE id = ANY(:ids) AND status IN ('queued','retry')"
                ),
                {"o": ACTOR, "l": lease_seconds, "ids": job_ids},
            )

        processed = 0
        completed = 0
        retried = 0
        blocked = 0
        for job_id in job_ids:
            try:
                async with transaction(db):
                    outcome = await _process_one_job(db, job_id, audit=audit)
            except RuleValidationError as exc:
                # deterministic: the rules cannot assess this claim —
                # blocked, head kept invalid, critical alert (§5.9.1)
                processed += 1
                blocked += await _block_lost_job(
                    db,
                    audit,
                    job_id,
                    error_class="rule_validation",
                    message=str(exc),
                    with_question=True,
                )
                continue
            except Exception as exc:
                # transient (or unknown): retry with backoff until the
                # budget is exhausted, then blocked
                processed += 1
                job = await _fetch_job(db, job_id, lock=False)
                exhausted = job is not None and (job.attempts or 0) >= (
                    job.max_attempts or 0
                )
                if exhausted:
                    blocked += await _block_lost_job(
                        db,
                        audit,
                        job_id,
                        error_class="retry_budget_exhausted",
                        message=str(exc)[:1500],
                        with_question=False,
                    )
                else:
                    retried += await _retry_lost_job(
                        db,
                        audit,
                        job_id,
                        error_class=type(exc).__name__,
                        message=str(exc)[:1500],
                    )
                continue

            if outcome == "gone":
                continue  # lease already taken over; not counted as work
            processed += 1
            if outcome == "completed":
                completed += 1
            elif outcome == "retried":
                retried += 1
            elif outcome == "blocked":
                blocked += 1
            # 'unleased' / 'intent_conflict' — admission lost mid-batch,
            # not counted as work

        # the batch is done: release the gate (a crash on the job paths
        # leaves the holder row for the gate lease TTL, rule 3 crash
        # window)
        async with transaction(db):
            await release_writer_gate(db, owner_kind=OWNER_WORKER, owner_id=ACTOR)

    return WorkerOutcome(processed, completed, retried, blocked, False)


async def recover_expired_leases(db: AsyncSession) -> int:
    """Crash recovery: return ``leased`` jobs whose lease expired back to
    the queue. The atomic job transaction either committed (job
    completed — the row is no longer leased) or rolled back, so the
    consumed attempt is restored. The scheduler calls this before each
    batch (§5.9.1 liveness)."""
    audit = AuditService(db)
    async with transaction(db):
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT id FROM reassessment_jobs "
                        "WHERE status = 'leased' AND lease_expires_at < now() FOR UPDATE"
                    )
                )
            )
            .scalars()
            .all()
        )
        if not rows:
            return 0
        for raw in rows:
            job = (
                (
                    await db.execute(
                        select(ORMReassessmentJob).where(
                            ORMReassessmentJob.id == _as_uuid(raw)
                        ).with_for_update()
                    )
                )
                .scalars()
                .first()
            )
            if job is None or job.status != ReassessmentJobStatus.LEASED.value:
                continue
            await _unlease(db, (job,))
            await audit.record(
                AuditEventType.REASSESSMENT_JOB_RETRY,
                actor=ACTOR,
                payload={
                    "job_id": str(job.id),
                    "claim_id": str(job.claim_id),
                    "error_class": "lease_expired",
                    "note": "crash recovery: attempt restored",
                },
                public_summary=f"reassessment job {job.id}: lease expired, requeued",
            )
    return len(rows)


async def worker_admission_metrics(db: AsyncSession) -> dict[str, object]:
    """The admission primitives for the scheduler gates (T4.4, §5.9.1):
    whether runnable dependency-critical work exists and its age.
    Runnable = the exact runnability predicate (effective pointer, empty
    activating slot, due, budget left) — read-only, no locks."""
    row = (
        await db.execute(
            text(
                "SELECT count(*), "
                "MAX(EXTRACT(EPOCH FROM (now() - j.enqueued_at))) "
                "FROM reassessment_jobs j "
                "JOIN runtime_config_heads h ON h.scope = 'global' "
                "WHERE j.status IN ('queued','retry') "
                "AND j.target_config_snapshot_id = h.active_config_snapshot_id "
                "AND h.activating_config_snapshot_id IS NULL "
                "AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= now()) "
                "AND j.attempts < j.max_attempts"
            )
        )
    ).first()
    count = int(row[0]) if row is not None else 0
    age = float(row[1]) if row is not None and row[1] is not None else None
    return {"runnable_count": count, "oldest_runnable_age_seconds": age}
