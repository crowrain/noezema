"""Online activation of a config snapshot (§8.7.2, T4.5).

The fenced protocol for a LIVE system: the reassessment worker is a
concurrent knowledge writer, so every step is a conditional write over
the activation tuple ``(scope, activating_config_snapshot_id,
activation_fence, activation_lease_owner, lease_expires_at > now())``
and the runtime head is the first canonical lock of every transaction.

Protocol (one activating candidate per scope):

  1. acquire: the exclusive ``knowledge_write_gate`` (waits for a
     running worker batch to finish) + a fenced lease on
     ``runtime_config_heads`` (the fence increases monotonically; a
     recovery takeover bumps it — the old runner is fenced out of
     every subsequent write);
  2. freeze the cohort + activation manifest against the knowledge
     revision (the worker is quiesced by the activating pointer from
     step 1);
  3. prepare shadow heads in bounded idempotent batches — an AFFECTED
     claim (its claim-type rule changed, or it has no current head
     under the base) gets a ``pending`` head + a durable reassessment
     job targeting the candidate; an UNCHANGED claim is carried over
     by the fast path (the shadow head references the old valid
     assessment — the rules are deterministic, so the result is
     unchanged);
  4. verify the complete cohort in a separate transaction and persist
     the immutable seal (``state=ready``); the sealed-interval trigger
     then freezes the shadow heads;
  5. ``publishing`` → the atomic flip (ONE transaction): lock head →
     knowledge, verify fence/lease/base pointer/complete seal/revision
     equality, move the runtime pointer, candidate → ``post_publish``
     (the manifest starts at cursor 0), the previous snapshot →
     ``superseded`` (except the immutable bootstrap), knowledge
     revision bump, audit + outbox;
  6. the post-publish manifest (deterministic UUIDv5 research
     questions for the pending heads) runs in bounded CAS batches —
     the slot stays held, so the worker and sessions remain quiesced;
  7. terminal cleanup (ONE transaction): success → ``active``, retry
     exhausted → ``post_publish_blocked`` + alert, pre-publish
     failure → ``failed``; the activating slot and lease clear and
     admission resumes. A terminal state with a non-empty slot is an
     invariant violation, not a normal intermediate.

Crash semantics: every step keys on the candidate state, so a
repeated run resumes where the crash left it (same owner; an expired
lease is a takeover with a fence bump). After terminal cleanup the
slot is empty and the trusted repair runner completes a
``post_publish_blocked`` manifest via the repair CAS (or closes it as
``superseded`` when a newer flip owns the pointer).
"""

from __future__ import annotations

import asyncio
import json
import random
import time
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.config import QUESTION_UUID5_NAMESPACE, config_snapshot_sha256
from packages.domain.db.uow import transaction
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.models.enums import (
    AuditEventType,
    AuditVisibility,
    QuestionOrigin,
    ReassessmentJobStatus,
)
from packages.domain.models.memory import ORMClaim, ORMClaimAssessmentHead, ORMReassessmentJob
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.audit import AuditService
from packages.memory.writer_gate import (
    GATE_PRIORITY_ACTIVATION,
    OWNER_ACTIVATION,
    acquire_writer_gate,
    release_writer_gate,
)

SCOPE = "global"
ACTOR = "system:activation"

ACTIVE_SESSION_STATES = (
    "selecting_question",
    "planning",
    "exploring",
    "verifying",
    "consolidating",
    "reporting",
    "committing",
)
UNRESOLVED_ATTEMPT_STATES = ("prepared", "reconciling")

DEFAULT_PREPARE_BATCH = 128
DEFAULT_POST_PUBLISH_BATCH = 64
DEFAULT_MAX_POST_PUBLISH_BATCHES = 32
DEFAULT_GATE_LEASE_SECONDS = 600
DEFAULT_ACTIVATION_LEASE_SECONDS = 3600
DEFAULT_GATE_WAIT_SECONDS = 900

#: post-publish/repair retry budget when the snapshot does not pin one
DEFAULT_MAX_ATTEMPTS = 78


class ActivationError(RuntimeError):
    """The online activation cannot proceed (fence mismatch, seal
    mismatch, preconditions, ...)."""


@dataclass(frozen=True)
class ActivationResult:
    candidate_id: uuid.UUID
    state: str
    fence: int
    already_active: bool = False
    published: bool = False
    resumed: bool = False
    cohort_count: int = 0
    heads_digest: str | None = None
    pending_heads: int = 0
    questions_created: int = 0


@dataclass(frozen=True)
class PostPublishResult:
    candidate_id: uuid.UUID
    state: str
    cursor: int
    total: int
    questions_created: int
    deferred: bool = False
    blocked: bool = False
    superseded: bool = False


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _post_publish_backoff_seconds(attempt: int) -> float:
    """The post-publish retry backoff (same shape as the worker's:
    30 s · 2^min(n-1, 7), cap 3600 s, jitter ≤ 15 s)."""
    exp = min(max(attempt - 1, 0), 7)
    base = float(min(3600.0, 30.0 * (2**exp)))
    return base + random.uniform(0.0, 15.0)


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> datetime:
    return datetime.now(UTC)


# ─── shared locks / the activation tuple ────────────────────────────────────


async def _lock_head(db: AsyncSession) -> ORMRuntimeConfigHead | None:
    head = (
        await db.execute(
            select(ORMRuntimeConfigHead)
            .where(ORMRuntimeConfigHead.scope == SCOPE)
            .with_for_update()
        )
    ).scalars().first()
    return head


def _lease_live(head: ORMRuntimeConfigHead) -> bool:
    return head.activation_lease_expires_at is not None and head.activation_lease_expires_at > _now()


def _tuple_ok(
    head: ORMRuntimeConfigHead, *, candidate_id: uuid.UUID, fence: int, owner: str
) -> bool:
    return (
        _as_uuid(head.activating_config_snapshot_id) == candidate_id
        and int(head.activation_fence or 0) == fence
        and head.activation_lease_owner == owner
        and _lease_live(head)
    )


async def _check_tuple(
    db: AsyncSession, *, candidate: ORMConfigSnapshot, fence: int, owner: str
) -> None:
    """Lock the runtime head first (canonical order) and verify the
    activation tuple; a fenced-out runner fails here, never by writing."""
    head = await _lock_head(db)
    if head is None:
        raise ActivationError("global runtime head missing")
    if not _tuple_ok(head, candidate_id=candidate.id, fence=fence, owner=owner):
        raise ActivationError(
            "activation tuple mismatch (fence/owner/lease) — this runner is fenced out"
        )


async def _pending_claim_ids(db: AsyncSession, *, candidate_id: uuid.UUID) -> list[uuid.UUID]:
    rows = (
        await db.execute(
            text(
                "SELECT claim_id FROM claim_assessment_heads "
                "WHERE config_snapshot_id = :c AND assessment_state = 'pending' "
                "ORDER BY claim_id"
            ),
            {"c": candidate_id},
        )
    ).scalars().all()
    out: list[uuid.UUID] = []
    for r in rows:
        u = _as_uuid(r)
        if u is not None:
            out.append(u)
    return out


async def _question_for_pending_head(
    db: AsyncSession, *, claim_id: uuid.UUID, candidate_id: uuid.UUID
) -> int:
    """Create the deterministic follow-up question (idempotent).
    Returns 1 if a row was inserted."""
    qid = uuid.uuid5(
        uuid.UUID(QUESTION_UUID5_NAMESPACE), f"activation-pending:{candidate_id}:{claim_id}"
    )
    exists = (
        await db.execute(text("SELECT 1 FROM questions WHERE id = :q"), {"q": str(qid)})
    ).scalar_one_or_none()
    if exists is not None:
        return 0
    claim = await db.get(ORMClaim, claim_id)
    stmt = claim.statement if claim is not None else f"claim {claim_id}"
    db.add(
        ORMQuestion(
            id=qid,
            text=(f"Переоценить утверждение (pending-head при смене правил): {stmt[:1900]}")[:2000],
            origin=QuestionOrigin.PREVIOUS_RESULT.value,
            origin_config_snapshot_id=candidate_id,
        )
    )
    return 1


# ─── candidate identity ─────────────────────────────────────────────────────


async def upsert_online_candidate(
    db: AsyncSession,
    audit: AuditService,
    *,
    base_snapshot: ORMConfigSnapshot,
    requested_payload: dict[str, Any],
) -> tuple[ORMConfigSnapshot, bool]:
    """Deterministic online candidate (the 0009 partial unique on
    ``(base_snapshot_id, payload_sha256)`` for non-terminal states).
    Returns ``(candidate, already_active)``.

    A crashed run leaves its mid-pipeline candidate behind (preparing /
    ready / publishing / post_publish): the upsert must find that row
    and resume it, NOT reset it to draft — the pipeline state is the
    resume cursor.

    The effective snapshot already having the requested payload is only
    a no-op when its activation reached the terminal ``active``: a
    ``post_publish`` effective candidate still has the manifest open
    (resume it), and ``post_publish_blocked`` is repair-lane territory
    (the driver reports it, never touches the slot)."""
    payload_sha = canonical_sha256(requested_payload)
    if base_snapshot.payload_sha256 == payload_sha:
        if base_snapshot.activation_state == "active":
            return base_snapshot, True
        return base_snapshot, False

    row = (
        (
            await db.execute(
                text(
                    """
                    INSERT INTO config_snapshots (
                        id, base_snapshot_id, payload_sha256, sha256,
                        activation_mode, activation_state,
                        model, embeddings, prompts, policy, curiosity,
                        token_budgets, session_limits, activation_limits, claim_type_rules,
                        wake_schedule, reassessment_admission, repair_admission,
                        planning
                    ) VALUES (
                        :id, :base, :payload_sha, :sha, 'online', 'draft',
                        :model, :embeddings, :prompts, :policy, :curiosity,
                        :token_budgets, :session_limits, :activation_limits, :claim_type_rules,
                        :wake_schedule, :reassessment_admission, :repair_admission,
                        :planning
                    )
                    ON CONFLICT (base_snapshot_id, payload_sha256)
                    WHERE activation_mode = 'online'
                      AND activation_state NOT IN ('failed', 'active', 'superseded')
                    DO NOTHING
                    RETURNING id, activation_state
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "base": str(base_snapshot.id),
                    "payload_sha": payload_sha,
                    "sha": config_snapshot_sha256(base_snapshot.id, payload_sha),
                    "model": _json(requested_payload.get("model")),
                    "embeddings": _json(requested_payload.get("embeddings")),
                    "prompts": _json(requested_payload.get("prompts")),
                    "policy": _json(requested_payload.get("policy")),
                    "curiosity": _json(requested_payload.get("curiosity")),
                    "token_budgets": _json(requested_payload.get("token_budgets")),
                    "session_limits": _json(requested_payload.get("session_limits")),
                    "activation_limits": _json(requested_payload.get("activation_limits")),
                    "claim_type_rules": _json(requested_payload.get("claim_type_rules")),
                    # the schedule/admission sections inherit from the base
                    # snapshot when the requested payload does not set them
                    "wake_schedule": _json(
                        requested_payload.get("wake_schedule", base_snapshot.wake_schedule)
                    ),
                    "reassessment_admission": _json(
                        requested_payload.get(
                            "reassessment_admission", base_snapshot.reassessment_admission
                        )
                    ),
                    "repair_admission": _json(
                        requested_payload.get("repair_admission", base_snapshot.repair_admission)
                    ),
                    "planning": _json(
                        requested_payload.get("planning", base_snapshot.planning)
                    ),
                },
            )
        )
        .mappings()
        .first()
    )
    if row is not None:
        # a new candidate row
        candidate = await db.get(ORMConfigSnapshot, uuid.UUID(str(row["id"])))
        assert candidate is not None
        await audit.record(
            AuditEventType.CONFIG_SNAPSHOT_CREATED,
            actor=ACTOR,
            payload={
                "candidate_id": str(candidate.id),
                "base_snapshot_id": str(base_snapshot.id),
                "payload_sha256": payload_sha,
                "activation_mode": "online",
            },
        )
        return candidate, False

    # a crashed run left the mid-pipeline candidate behind (DO NOTHING):
    # resume it in whatever state it is in
    existing = (
        (
            await db.execute(
                select(ORMConfigSnapshot).where(
                    ORMConfigSnapshot.base_snapshot_id == base_snapshot.id,
                    ORMConfigSnapshot.payload_sha256 == payload_sha,
                    ORMConfigSnapshot.activation_mode == "online",
                    ORMConfigSnapshot.activation_state.not_in(("failed", "active", "superseded")),
                )
            )
        )
        .scalars()
        .first()
    )
    if existing is None:
        raise ActivationError("online candidate row missing (conflict target not found)")
    return existing, False


# ─── 1. acquire (fenced lease) ──────────────────────────────────────────────


async def acquire_activation(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: ORMConfigSnapshot,
    owner: str = ACTOR,
    lease_seconds: int = DEFAULT_ACTIVATION_LEASE_SECONDS,
    gate_wait_seconds: int = DEFAULT_GATE_WAIT_SECONDS,
) -> int:
    """Acquire the activation lease (§8.7.2, steps 1-5). Returns the
    fence for every subsequent conditional write.

    Slot states: empty → acquire (fence+1, ``activation_acquired``);
    same candidate + same owner + LIVE lease → idempotent resume (the
    fence is returned as-is, no new event); same candidate + EXPIRED
    lease (own or foreign) → recovery takeover (fence+1,
    ``activation_takeover`` — the old runner is fenced out); a foreign
    candidate or a foreign LIVE lease → error."""
    deadline = time.monotonic() + gate_wait_seconds
    while True:
        async with transaction(db):
            got = await acquire_writer_gate(
                db,
                owner_kind=OWNER_ACTIVATION,
                owner_id=owner,
                priority=GATE_PRIORITY_ACTIVATION,
                lease_seconds=DEFAULT_GATE_LEASE_SECONDS,
            )
        if got:
            break
        if time.monotonic() >= deadline:
            raise ActivationError("could not acquire the writer gate before the deadline")
        # the gate is released when the running batch finishes — poll
        # with jitter (only a live foreign holder can keep it)
        await asyncio.sleep(0.25 + random.uniform(0.0, 0.25))

    try:
        async with transaction(db):
            # the canonical lock order: runtime head → sessions →
            # commit_attempts
            head = await _lock_head(db)
            if head is None:
                raise ActivationError("global runtime head missing")
            states_sql = ", ".join(repr(s) for s in ACTIVE_SESSION_STATES)
            await db.execute(text(f"SELECT id FROM sessions WHERE state IN ({states_sql}) FOR UPDATE"))
            active_sessions = (
                await db.execute(
                    text(f"SELECT count(*) FROM sessions WHERE state IN ({states_sql})")
                )
            ).scalar_one()
            await db.execute(
                text(
                    "SELECT id FROM commit_attempts WHERE status IN "
                    "('prepared', 'reconciling') FOR UPDATE"
                )
            )
            unresolved = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM commit_attempts WHERE status IN "
                        "('prepared', 'reconciling')"
                    )
                )
            ).scalar_one()
            if int(active_sessions) > 0:
                raise ActivationError(f"active sessions present: {int(active_sessions)}")
            if int(unresolved) > 0:
                raise ActivationError(f"unresolved commit attempts: {int(unresolved)}")

            existing = _as_uuid(head.activating_config_snapshot_id)
            fence: int
            event: AuditEventType | None
            if existing is None:
                fence = int(head.activation_fence or 0) + 1
                event = AuditEventType.ACTIVATION_ACQUIRED
            elif (
                existing == candidate.id
                and head.activation_lease_owner == owner
                and _lease_live(head)
            ):
                # idempotent resume: the lease is still ours
                fence = int(head.activation_fence or 0)
                event = None
            elif existing == candidate.id and not _lease_live(head):
                # recovery takeover (own crash or a dead foreign owner):
                # the fence bumps — the old runner is fenced out
                fence = int(head.activation_fence or 0) + 1
                event = AuditEventType.ACTIVATION_TAKEOVER
            else:
                raise ActivationError(
                    "activation slot busy (foreign candidate or live foreign lease)"
                )

            if event is not None:
                head.activating_config_snapshot_id = candidate.id
                head.activation_fence = fence
                head.activation_lease_owner = owner
                head.activation_lease_expires_at = _now() + timedelta(seconds=lease_seconds)
                candidate.activation_mode = "online"
                if candidate.activation_state == "draft":
                    candidate.activation_state = "preparing_heads"
                await db.flush()
                await audit.record(
                    event,
                    actor=owner,
                    payload={
                        "candidate_id": str(candidate.id),
                        "base_snapshot_id": str(candidate.base_snapshot_id),
                        "fence": fence,
                        "scope": SCOPE,
                    },
                    public_summary=(
                        "online activation lease "
                        f"{'taken over' if event is AuditEventType.ACTIVATION_TAKEOVER else 'acquired'} "
                        f"(fence {fence}): {candidate.id}"
                    ),
                )
            return fence
    finally:
        # the gate is released: new worker batches and sessions see the
        # activating pointer and fail admission (quiesce)
        async with transaction(db):
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id=owner)


# ─── 2-4. freeze / prepare / verify ─────────────────────────────────────────


async def freeze_cohort_online(
    db: AsyncSession, *, candidate: ORMConfigSnapshot, fence: int, owner: str
) -> int:
    """Freeze the cohort against the knowledge revision (idempotent: a
    resumed run keeps the frozen cohort)."""
    async with transaction(db):
        await _check_tuple(db, candidate=candidate, fence=fence, owner=owner)
        if candidate.activation_cohort_revision is not None:
            return int(candidate.activation_expected_head_count or 0)
        rev = (
            await db.execute(
                text("SELECT revision FROM domain_revisions WHERE scope='knowledge'")
            )
        ).scalar_one()
        claim_ids = [
            r[0] for r in (await db.execute(text("SELECT id FROM claims ORDER BY id"))).all()
        ]
        manifest = {"cohort_revision": int(rev), "claim_ids": [str(c) for c in claim_ids]}
        candidate.activation_state = "preparing_heads"
        candidate.activation_cohort_revision = int(rev)
        candidate.activation_manifest_hash = canonical_sha256(manifest)
        candidate.activation_expected_head_count = len(claim_ids)
        await db.flush()
        return len(claim_ids)


def _rule_unchanged(
    base_rules: dict[str, Any], cand_rules: dict[str, Any], claim_type: str
) -> bool:
    a = base_rules.get(claim_type)
    b = cand_rules.get(claim_type)
    if a is None or b is None:
        return a is None and b is None
    return canonical_sha256(a) == canonical_sha256(b)


async def _enqueue_activation_job(
    db: AsyncSession, *, claim_id: uuid.UUID, snapshot_id: uuid.UUID
) -> bool:
    """The durable job for an affected claim (reason ``activation``);
    the partial unique index keeps one active job per (claim, target)."""
    exists = (
        await db.execute(
            text(
                "SELECT 1 FROM reassessment_jobs "
                "WHERE claim_id = :c AND target_config_snapshot_id = :s "
                "AND status IN ('queued','leased','retry')"
            ),
            {"c": claim_id, "s": snapshot_id},
        )
    ).scalar_one_or_none()
    if exists is not None:
        return False
    db.add(
        ORMReassessmentJob(
            id=uuid.uuid4(),
            claim_id=claim_id,
            target_config_snapshot_id=snapshot_id,
            status=ReassessmentJobStatus.QUEUED.value,
            reason="activation",
            priority=0,
        )
    )
    return True


async def prepare_heads_online(
    db: AsyncSession,
    *,
    candidate: ORMConfigSnapshot,
    fence: int,
    owner: str,
    batch_size: int = DEFAULT_PREPARE_BATCH,
) -> int:
    """Bounded idempotent shadow-head batches (§8.7.2): affected claims
    → pending head + durable job; unchanged claims → fast path over the
    old valid assessment. Each batch locks the runtime head first and
    verifies the activation tuple (a fenced-out runner fails here)."""
    if candidate.base_snapshot_id is None:
        raise ActivationError("candidate has no base snapshot")
    base = await db.get(ORMConfigSnapshot, candidate.base_snapshot_id)
    if base is None:
        raise ActivationError("base snapshot missing")
    base_rules: dict[str, Any] = dict(base.claim_type_rules or {})
    cand_rules: dict[str, Any] = dict(candidate.claim_type_rules or {})

    prepared = 0
    while True:
        batch = (
            (
                await db.execute(
                    select(ORMClaim).order_by(ORMClaim.id).limit(batch_size).offset(prepared)
                )
            )
            .scalars()
            .all()
        )
        if not batch:
            break
        async with transaction(db):
            await _check_tuple(db, candidate=candidate, fence=fence, owner=owner)
            for claim in batch:
                base_head = (
                    (
                        await db.execute(
                            select(ORMClaimAssessmentHead).where(
                                ORMClaimAssessmentHead.claim_id == claim.id,
                                ORMClaimAssessmentHead.config_snapshot_id == base.id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                carry = (
                    base_head is not None
                    and base_head.assessment_state == "current"
                    and base_head.current_assessment_id is not None
                    and _rule_unchanged(base_rules, cand_rules, claim.claim_type)
                )
                if carry and base_head is not None:
                    state = "current"
                    assessment_id = base_head.current_assessment_id
                    status = base_head.epistemic_status
                else:
                    state = "pending"
                    assessment_id = None
                    status = None

                shadow = (
                    (
                        await db.execute(
                            select(ORMClaimAssessmentHead).where(
                                ORMClaimAssessmentHead.claim_id == claim.id,
                                ORMClaimAssessmentHead.config_snapshot_id == candidate.id,
                            )
                        )
                    )
                    .scalars()
                    .first()
                )
                if shadow is None:
                    db.add(
                        ORMClaimAssessmentHead(
                            claim_id=claim.id,
                            config_snapshot_id=candidate.id,
                            assessment_state=state,
                            current_assessment_id=assessment_id,
                            epistemic_status=status,
                            prepared_by="rules_activation",
                        )
                    )
                else:
                    shadow.assessment_state = state
                    shadow.current_assessment_id = assessment_id
                    shadow.epistemic_status = status
                    shadow.prepared_by = "rules_activation"
                if not carry:
                    await _enqueue_activation_job(
                        db, claim_id=claim.id, snapshot_id=candidate.id
                    )
        prepared += len(batch)
    return prepared


async def verify_and_seal_online(
    db: AsyncSession, *, candidate: ORMConfigSnapshot, fence: int, owner: str
) -> str:
    """The separate verification transaction: the knowledge revision
    must equal the cohort revision, the cohort is complete, and the
    immutable seal persists with ``state=ready`` (idempotent on
    resume)."""
    async with transaction(db):
        await _check_tuple(db, candidate=candidate, fence=fence, owner=owner)
        if candidate.activation_state == "ready":
            assert candidate.activation_heads_sha256 is not None
            return candidate.activation_heads_sha256
        rev = (
            await db.execute(
                text("SELECT revision FROM domain_revisions WHERE scope='knowledge'")
            )
        ).scalar_one()
        if candidate.activation_cohort_revision is not None and int(rev) != int(
            candidate.activation_cohort_revision
        ):
            raise ActivationError(
                "knowledge revision moved since the cohort freeze — rebuild before sealing"
            )
        rows = (
            (
                await db.execute(
                    text(
                        "SELECT claim_id, assessment_state, current_assessment_id, "
                        "epistemic_status, prepared_by FROM claim_assessment_heads "
                        "WHERE config_snapshot_id = :c ORDER BY claim_id"
                    ),
                    {"c": candidate.id},
                )
            )
            .mappings()
            .all()
        )
        expected = int(candidate.activation_expected_head_count or 0)
        if len(rows) != expected:
            raise ActivationError(f"incomplete cohort: {len(rows)} heads != expected {expected}")
        digest_body = [
            {
                "claim_id": str(r["claim_id"]),
                "assessment_state": r["assessment_state"],
                "current_assessment_id": str(r["current_assessment_id"])
                if r["current_assessment_id"]
                else None,
                "epistemic_status": r["epistemic_status"],
                "prepared_by": r["prepared_by"],
            }
            for r in rows
        ]
        digest = canonical_sha256(digest_body)
        candidate.activation_state = "ready"
        candidate.activation_heads_sha256 = digest
        candidate.activation_verified_head_count = len(rows)
        candidate.activation_verified_at = _now()
        await db.flush()
        return digest


# ─── 5. publishing + the atomic flip ────────────────────────────────────────


async def publish_online(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: ORMConfigSnapshot,
    fence: int,
    owner: str,
) -> int:
    """``ready → publishing`` (bounded by the pending-question limit)
    and the atomic flip (ONE transaction: head → knowledge locks,
    fence/lease/base/seal/revision verification, pointer move,
    candidate → ``post_publish`` with the manifest at cursor 0,
    previous → ``superseded`` except the immutable bootstrap,
    knowledge bump, audit + outbox). Returns the pending-head count
    (the manifest total)."""
    async with transaction(db):
        await _check_tuple(db, candidate=candidate, fence=fence, owner=owner)
        if candidate.activation_state == "ready":
            pending = await _pending_claim_ids(db, candidate_id=candidate.id)
            limit = int(
                candidate.activation_limits.get("online_activation_max_pending_questions", 100)
                or 100
            )
            if len(pending) > limit:
                raise ActivationError(
                    f"pending question backlog {len(pending)} exceeds the limit {limit} — "
                    "the rules change invalidates too much at once"
                )
            candidate.activation_state = "publishing"
            await db.flush()
        elif candidate.activation_state != "publishing":
            raise ActivationError(f"cannot publish from state {candidate.activation_state!r}")

    async with transaction(db):
        head = await _lock_head(db)
        if head is None:
            raise ActivationError("global runtime head missing")
        if not _tuple_ok(head, candidate_id=candidate.id, fence=fence, owner=owner):
            raise ActivationError("activation tuple mismatch at the flip — fenced out")
        active = _as_uuid(head.active_config_snapshot_id)
        if active == candidate.id:
            if candidate.activation_state == "post_publish":
                # the flip already committed (crash after the flip,
                # before the post-publish run)
                return len(await _pending_claim_ids(db, candidate_id=candidate.id))
            raise ActivationError("pointer already moved but the candidate is not post_publish")
        if active != candidate.base_snapshot_id:
            raise ActivationError("active pointer is not the candidate base revision")
        if not candidate.activation_heads_sha256 or candidate.activation_verified_head_count is None:
            raise ActivationError("verification seal missing")
        if int(candidate.activation_verified_head_count) != int(
            candidate.activation_expected_head_count or 0
        ):
            raise ActivationError("seal count mismatch")
        rev = (
            await db.execute(
                text("SELECT revision FROM domain_revisions WHERE scope = 'knowledge' FOR UPDATE")
            )
        ).scalar_one()
        if int(rev) != int(candidate.activation_cohort_revision or 0):
            raise ActivationError("knowledge revision moved — rebuild before the flip")

        pending = await _pending_claim_ids(db, candidate_id=candidate.id)
        qids = [
            uuid.uuid5(
                uuid.UUID(QUESTION_UUID5_NAMESPACE),
                f"activation-pending:{candidate.id}:{claim_id}",
            )
            for claim_id in pending
        ]
        candidate.post_publish_manifest_hash = canonical_sha256(
            {"total": len(qids), "question_ids": [str(q) for q in qids]}
        )
        candidate.post_publish_cursor = 0
        candidate.post_publish_attempts = 0
        candidate.post_publish_next_attempt_at = _now()
        candidate.post_publish_started_at = _now()
        candidate.activation_state = "post_publish"

        head.active_config_snapshot_id = candidate.id
        if candidate.base_snapshot_id is not None:
            previous = await db.get(ORMConfigSnapshot, candidate.base_snapshot_id)
            if previous is not None and previous.activation_mode != "bootstrap":
                previous.activation_state = "superseded"
        await db.execute(
            text(
                "UPDATE domain_revisions SET revision = revision + 1, updated_at = now() "
                "WHERE scope = 'knowledge'"
            )
        )
        await audit.record(
            AuditEventType.ACTIVATION_PUBLISHED,
            actor=owner,
            payload={
                "candidate_id": str(candidate.id),
                "base_snapshot_id": str(candidate.base_snapshot_id),
                "fence": fence,
                "heads_sha256": candidate.activation_heads_sha256,
                "pending_heads": len(pending),
                "manifest_hash": candidate.post_publish_manifest_hash,
            },
            public_summary=f"online activation published: {candidate.id} (fence {fence})",
        )
        return len(pending)


# ─── 6-7. post-publish manifest + terminal cleanup ──────────────────────────


async def _terminal_cleanup_tx(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: ORMConfigSnapshot,
    fence: int,
    owner: str,
    outcome: str,
    last_error: str | None = None,
) -> None:
    """The terminal state update + the slot/lease clear in ONE
    transaction (§8.7.2): a terminal state with a non-empty activating
    slot is an invariant violation. ``outcome``: ``active`` |
    ``post_publish_blocked`` | ``failed``."""
    head = await _lock_head(db)
    if head is None:
        raise ActivationError("global runtime head missing")
    if head.activating_config_snapshot_id is not None and not _tuple_ok(
        head, candidate_id=candidate.id, fence=fence, owner=owner
    ):
        raise ActivationError("cannot clean up: the slot is fenced out (takeover happened)")
    snap = await db.get(ORMConfigSnapshot, candidate.id)
    assert snap is not None
    await db.refresh(snap)
    candidate = snap
    if outcome == "active":
        candidate.activation_state = "active"
        await audit.record(
            AuditEventType.ACTIVATION_POST_PUBLISH_COMPLETED,
            actor=owner,
            payload={
                "candidate_id": str(candidate.id),
                "cursor": candidate.post_publish_cursor,
                "manifest_hash": candidate.post_publish_manifest_hash,
            },
            public_summary=f"post-publish manifest completed: {candidate.id}",
        )
    elif outcome == "post_publish_blocked":
        candidate.activation_state = "post_publish_blocked"
        candidate.post_publish_blocked_at = _now()
        candidate.post_publish_next_attempt_at = _now()
        if last_error is not None:
            candidate.post_publish_last_error = last_error[:1500]
        await audit.record(
            AuditEventType.ACTIVATION_POST_PUBLISH_BLOCKED,
            actor=owner,
            payload={
                "candidate_id": str(candidate.id),
                "cursor": candidate.post_publish_cursor,
                "attempts": candidate.post_publish_attempts,
                "last_error": last_error,
            },
            public_summary=f"post-publish blocked: {candidate.id}",
        )
        await audit.record(
            AuditEventType.ALERT_RAISED,
            actor=owner,
            payload={
                "alert_class": "post_publish_blocked",
                "candidate_id": str(candidate.id),
                "cursor": candidate.post_publish_cursor,
            },
            public_summary=f"alert: post-publish manifest blocked ({candidate.id})",
            visibility=AuditVisibility.DIAGNOSTIC,
        )
    elif outcome == "failed":
        candidate.activation_state = "failed"
        if last_error is not None:
            candidate.post_publish_last_error = last_error[:1500]
    else:
        raise ActivationError(f"unknown terminal outcome {outcome!r}")
    head.activating_config_snapshot_id = None
    head.activation_lease_owner = None
    head.activation_lease_expires_at = None
    await audit.record(
        AuditEventType.ACTIVATION_CLEANED_UP,
        actor=owner,
        payload={"candidate_id": str(candidate.id), "outcome": outcome, "fence": fence},
        public_summary=f"activation slot cleaned up ({outcome})",
    )


async def run_post_publish(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: ORMConfigSnapshot,
    fence: int,
    owner: str = ACTOR,
    batch_size: int = DEFAULT_POST_PUBLISH_BATCH,
    max_batches: int = DEFAULT_MAX_POST_PUBLISH_BATCHES,
) -> PostPublishResult:
    """The durable post-publish manifest (trusted writer, §8.7.2):
    deterministic UUIDv5 questions for the pending heads in bounded CAS
    batches — each batch locks runtime head → knowledge and increments
    the knowledge revision together with the cursor. The activating
    slot stays held (the worker and sessions remain quiesced until the
    terminal cleanup). A transient failure backs off in the same cursor
    transaction; retry exhaustion is a terminal cleanup with
    ``post_publish_blocked`` (the repair runner takes over via the
    repair CAS). A fenced-out tuple (takeover) is not an error when the
    candidate is already terminal."""
    cand_id = candidate.id  # captured pre-rollback (a session rollback expires ORM state)
    async with transaction(db):
        got = await acquire_writer_gate(
            db,
            owner_kind=OWNER_ACTIVATION,
            owner_id=owner,
            priority=GATE_PRIORITY_ACTIVATION,
            lease_seconds=DEFAULT_GATE_LEASE_SECONDS,
        )
    if not got:
        c = await db.get(ORMConfigSnapshot, cand_id)
        return PostPublishResult(
            candidate_id=cand_id,
            state=c.activation_state if c is not None else "unknown",
            cursor=0,
            total=0,
            questions_created=0,
            deferred=True,
        )
    created = 0
    try:
        for _ in range(max_batches):
            batch_created = 0
            next_cursor = 0
            outcome: str | None = None  # 'completed' | 'fenced' | None
            fenced_state: str | None = None
            async with transaction(db):
                head = await _lock_head(db)
                if head is None:
                    raise ActivationError("global runtime head missing")
                c = await db.get(ORMConfigSnapshot, cand_id)
                assert c is not None
                await db.refresh(c)
                if c.activation_state in ("active", "superseded"):
                    return PostPublishResult(
                        candidate_id=cand_id, state=c.activation_state,
                        cursor=int(c.post_publish_cursor or 0), total=0,
                        questions_created=created,
                        superseded=c.activation_state == "superseded",
                    )
                if c.activation_state != "post_publish":
                    raise ActivationError(
                        f"post-publish run from unexpected state {c.activation_state!r}"
                    )
                if not _tuple_ok(head, candidate_id=cand_id, fence=fence, owner=owner):
                    # a takeover fenced this run out: the manifest is
                    # post_publish and the slot is held by someone else
                    # who will continue it (or the candidate is being
                    # rebuilt) — leave it for the owner of the tuple
                    outcome = "fenced"
                    fenced_state = c.activation_state
                    next_cursor = int(c.post_publish_cursor or 0)
                elif c.post_publish_next_attempt_at is not None and c.post_publish_next_attempt_at > _now():
                    # the backoff window is not over — the next run
                    # continues
                    return PostPublishResult(
                        candidate_id=cand_id, state=c.activation_state,
                        cursor=int(c.post_publish_cursor or 0), total=0,
                        questions_created=created, deferred=True,
                    )
                else:
                    pending = await _pending_claim_ids(db, candidate_id=cand_id)
                    cursor = int(c.post_publish_cursor or 0)
                    if cursor >= len(pending):
                        # complete: the terminal cleanup in this
                        # transaction
                        await _terminal_cleanup_tx(
                            db, audit, candidate=c, fence=fence, owner=owner,
                            outcome="active",
                        )
                        outcome = "completed"
                        next_cursor = cursor
                    else:
                        slice_ = pending[cursor : cursor + batch_size]
                        for claim_id in slice_:
                            batch_created += await _question_for_pending_head(
                                db, claim_id=claim_id, candidate_id=c.id
                            )
                        c.post_publish_cursor = cursor + len(slice_)
                        await db.execute(
                            text(
                                "UPDATE domain_revisions SET revision = revision + 1, "
                                "updated_at = now() WHERE scope = 'knowledge'"
                            )
                        )
                        await audit.record(
                            AuditEventType.ACTIVATION_POST_PUBLISH_BATCH,
                            actor=owner,
                            payload={
                                "phase": "publish",
                                "candidate_id": str(c.id),
                                "cursor": c.post_publish_cursor,
                                "batch": batch_created,
                            },
                        )
                        next_cursor = int(c.post_publish_cursor or 0)
            if outcome == "completed":
                return PostPublishResult(
                    candidate_id=cand_id, state="active",
                    cursor=next_cursor, total=next_cursor,
                    questions_created=created + batch_created,
                )
            if outcome == "fenced":
                return PostPublishResult(
                    candidate_id=cand_id, state=fenced_state or "post_publish",
                    cursor=next_cursor, total=0, questions_created=created, deferred=True,
                )
            created += batch_created
        # max_batches exhausted: the manifest continues on the next run
        c = await db.get(ORMConfigSnapshot, cand_id)
        return PostPublishResult(
            candidate_id=cand_id,
            state=c.activation_state if c is not None else "unknown",
            cursor=int(c.post_publish_cursor or 0) if c is not None else 0,
            total=0,
            questions_created=created,
        )
    except Exception as exc:  # transient — backoff, then the terminal cleanup
        message = f"{type(exc).__name__}: {exc}"[:1500]
        c = await db.get(ORMConfigSnapshot, cand_id)
        if c is None:
            raise
        budget = int(
            c.activation_limits.get("online_activation_max_attempts", DEFAULT_MAX_ATTEMPTS)
            or DEFAULT_MAX_ATTEMPTS
        )
        blocked = False
        async with transaction(db):
            head = await _lock_head(db)
            if head is not None and _tuple_ok(
                head, candidate_id=cand_id, fence=fence, owner=owner
            ):
                await db.refresh(c)
                c.post_publish_attempts = int(c.post_publish_attempts or 0) + 1
                c.post_publish_last_error = message
                if c.post_publish_attempts >= budget:
                    blocked = True
                    c.activation_state = "post_publish_blocked"
                    c.post_publish_blocked_at = _now()
                    c.post_publish_next_attempt_at = _now()
                    await audit.record(
                        AuditEventType.ACTIVATION_POST_PUBLISH_BLOCKED,
                        actor=owner,
                        payload={
                            "candidate_id": str(c.id),
                            "cursor": c.post_publish_cursor,
                            "attempts": c.post_publish_attempts,
                            "last_error": message,
                        },
                        public_summary=f"post-publish blocked: {c.id}",
                    )
                    await audit.record(
                        AuditEventType.ALERT_RAISED,
                        actor=owner,
                        payload={
                            "alert_class": "post_publish_blocked",
                            "candidate_id": str(c.id),
                            "cursor": c.post_publish_cursor,
                        },
                        public_summary=f"alert: post-publish manifest blocked ({c.id})",
                        visibility=AuditVisibility.DIAGNOSTIC,
                    )
                    # the terminal cleanup: the slot clears, the repair
                    # runner (repair CAS) takes over
                    head.activating_config_snapshot_id = None
                    head.activation_lease_owner = None
                    head.activation_lease_expires_at = None
                    await audit.record(
                        AuditEventType.ACTIVATION_CLEANED_UP,
                        actor=owner,
                        payload={
                            "candidate_id": str(c.id),
                            "outcome": "post_publish_blocked",
                            "fence": fence,
                        },
                        public_summary="activation slot cleaned up (post_publish_blocked)",
                    )
                else:
                    c.post_publish_next_attempt_at = _now() + timedelta(
                        seconds=_post_publish_backoff_seconds(c.post_publish_attempts)
                    )
        if blocked:
            return PostPublishResult(
                candidate_id=cand_id, state="post_publish_blocked",
                cursor=int(c.post_publish_cursor or 0), total=0,
                questions_created=created, blocked=True,
            )
        raise
    finally:
        async with transaction(db):
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id=owner)


# ─── the trusted repair runner (post-cleanup) ───────────────────────────────


async def find_repair_backlog(db: AsyncSession) -> ORMConfigSnapshot | None:
    """The runnable repair backlog: the ``post_publish_blocked``
    candidate that owns the pointer (the repair CAS's candidate)."""
    row = (
        await db.execute(
            text(
                "SELECT id FROM config_snapshots c "
                "WHERE c.activation_mode = 'online' "
                "AND c.activation_state = 'post_publish_blocked' "
                "AND c.id = (SELECT active_config_snapshot_id FROM runtime_config_heads "
                "WHERE scope = 'global') LIMIT 1"
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    return await db.get(ORMConfigSnapshot, row)


async def run_activation_repair(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: uuid.UUID | ORMConfigSnapshot,
    owner: str = ACTOR,
    batch_size: int = DEFAULT_POST_PUBLISH_BATCH,
    max_batches: int = DEFAULT_MAX_POST_PUBLISH_BATCHES,
) -> PostPublishResult:
    """The trusted repair runner for a ``post_publish_blocked``
    manifest (§8.7.2). The slot is already cleared (terminal cleanup),
    so the batch CAS is the REPAIR CAS::

        runtime_config_heads.active_config_snapshot_id = candidate.id
        AND runtime_config_heads.activating_config_snapshot_id IS NULL
        AND candidate.activation_mode = 'online'
        AND candidate.activation_state = 'post_publish_blocked'
        AND candidate.post_publish_cursor = :expected_cursor
        AND candidate.post_publish_next_attempt_at <= now()

    Each batch locks runtime head → knowledge and increments the
    knowledge revision together with the cursor; the follow-up IDs are
    deterministic (a replayed batch creates no duplicates). If the
    pointer already belongs to a newer config, the runner closes the
    remainder as ``superseded`` and never returns the old config to
    ``active``. A permanent failure parks the backlog
    (``next_attempt_at = NULL``) with a severity alert until an audited
    operator retry sets a new time."""
    if not isinstance(candidate, ORMConfigSnapshot):
        loaded = await db.get(ORMConfigSnapshot, candidate)
        if loaded is None:
            raise ActivationError("candidate missing")
        candidate = loaded
    cand_id = candidate.id  # captured pre-rollback (a session rollback expires ORM state)
    async with transaction(db):
        got = await acquire_writer_gate(
            db,
            owner_kind=OWNER_ACTIVATION,
            owner_id=owner,
            priority=GATE_PRIORITY_ACTIVATION,
            lease_seconds=DEFAULT_GATE_LEASE_SECONDS,
        )
    if not got:
        c = await db.get(ORMConfigSnapshot, cand_id)
        return PostPublishResult(
            candidate_id=cand_id,
            state=c.activation_state if c is not None else "unknown",
            cursor=0,
            total=0,
            questions_created=0,
            deferred=True,
        )
    created = 0
    try:
        for _ in range(max_batches):
            batch_created = 0
            next_cursor = 0
            outcome: str | None = None  # 'completed' | 'superseded' | None
            async with transaction(db):
                head = await _lock_head(db)
                if head is None:
                    raise ActivationError("global runtime head missing")
                c = await db.get(ORMConfigSnapshot, cand_id)
                if c is None:
                    raise ActivationError("candidate missing")
                await db.refresh(c)
                # the superseded check FIRST: a newer flip owns the
                # pointer — close the remainder, never reactivate
                if _as_uuid(head.active_config_snapshot_id) != cand_id:
                    if c.activation_state == "post_publish_blocked":
                        c.activation_state = "superseded"
                        await audit.record(
                            AuditEventType.ACTIVATION_SUPERSEDED,
                            actor=owner,
                            payload={
                                "candidate_id": str(c.id),
                                "cursor": c.post_publish_cursor,
                                "new_active": str(head.active_config_snapshot_id),
                            },
                            public_summary=f"repair backlog closed as superseded: {c.id}",
                        )
                    return PostPublishResult(
                        candidate_id=cand_id, state="superseded",
                        cursor=int(c.post_publish_cursor or 0), total=0,
                        questions_created=created, superseded=True,
                    )
                if (
                    head.activating_config_snapshot_id is not None
                    or c.activation_mode != "online"
                    or c.activation_state != "post_publish_blocked"
                    or c.post_publish_next_attempt_at is None
                    or c.post_publish_next_attempt_at > _now()
                ):
                    # the repair CAS does not hold: a new activation is
                    # in flight, the manifest is not blocked, or the
                    # backoff window is not over
                    return PostPublishResult(
                        candidate_id=cand_id, state=c.activation_state,
                        cursor=int(c.post_publish_cursor or 0), total=0,
                        questions_created=created, deferred=True,
                    )
                pending = await _pending_claim_ids(db, candidate_id=cand_id)
                cursor = int(c.post_publish_cursor or 0)
                if cursor >= len(pending):
                    # the repair CAS (cursor == total) closes the manifest
                    c.activation_state = "active"
                    await audit.record(
                        AuditEventType.ACTIVATION_POST_PUBLISH_COMPLETED,
                        actor=owner,
                        payload={
                            "candidate_id": str(c.id),
                            "cursor": cursor,
                            "manifest_hash": c.post_publish_manifest_hash,
                        },
                        public_summary=f"post-publish manifest repaired to completion: {c.id}",
                    )
                    outcome = "completed"
                    next_cursor = cursor
                else:
                    slice_ = pending[cursor : cursor + batch_size]
                    for claim_id in slice_:
                        batch_created += await _question_for_pending_head(
                            db, claim_id=claim_id, candidate_id=c.id
                        )
                    c.post_publish_cursor = cursor + len(slice_)
                    await db.execute(
                        text(
                            "UPDATE domain_revisions SET revision = revision + 1, "
                            "updated_at = now() WHERE scope = 'knowledge'"
                        )
                    )
                    await audit.record(
                        AuditEventType.ACTIVATION_POST_PUBLISH_BATCH,
                        actor=owner,
                        payload={
                            "phase": "repair",
                            "candidate_id": str(c.id),
                            "cursor": c.post_publish_cursor,
                            "batch": batch_created,
                        },
                    )
                    next_cursor = int(c.post_publish_cursor or 0)
            if outcome == "completed":
                return PostPublishResult(
                    candidate_id=cand_id, state="active",
                    cursor=next_cursor, total=next_cursor,
                    questions_created=created + batch_created,
                )
            created += batch_created
        # max_batches exhausted: the backlog continues on the next tick
        c = await db.get(ORMConfigSnapshot, cand_id)
        return PostPublishResult(
            candidate_id=cand_id,
            state=c.activation_state if c is not None else "unknown",
            cursor=int(c.post_publish_cursor or 0) if c is not None else 0,
            total=0,
            questions_created=created,
        )
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"[:1500]
        permanent = isinstance(exc, (LookupError, ValueError))
        c = await db.get(ORMConfigSnapshot, cand_id)
        if c is not None:
            async with transaction(db):
                head = await _lock_head(db)
                if head is not None and c.activation_state == "post_publish_blocked" and (
                    _as_uuid(head.active_config_snapshot_id) == cand_id
                    and head.activating_config_snapshot_id is None
                ):
                    await db.refresh(c)
                    c.post_publish_attempts = int(c.post_publish_attempts or 0) + 1
                    c.post_publish_last_error = message
                    if permanent:
                        # the backlog is NOT admission-runnable until an
                        # audited operator retry sets a new time
                        c.post_publish_next_attempt_at = None
                        await audit.record(
                            AuditEventType.ALERT_RAISED,
                            actor=owner,
                            payload={
                                "alert_class": "repair_backlog_permanent_failure",
                                "candidate_id": str(c.id),
                                "error_class": type(exc).__name__,
                            },
                            public_summary=f"alert: repair backlog parked ({c.id})",
                            visibility=AuditVisibility.DIAGNOSTIC,
                        )
                    else:
                        c.post_publish_next_attempt_at = _now() + timedelta(
                            seconds=_post_publish_backoff_seconds(c.post_publish_attempts)
                        )
        if not permanent:
            raise
        cursor = int(c.post_publish_cursor or 0) if c is not None else 0
        return PostPublishResult(
            candidate_id=cand_id, state="post_publish_blocked",
            cursor=cursor, total=0, questions_created=created, deferred=True,
        )
    finally:
        async with transaction(db):
            await release_writer_gate(db, owner_kind=OWNER_ACTIVATION, owner_id=owner)


# ─── the crash-idempotent driver ────────────────────────────────────────────


_TERMINAL_STATES = ("active", "superseded", "failed", "post_publish_blocked")


async def run_online_change(
    db: AsyncSession,
    audit: AuditService,
    *,
    requested_payload: dict[str, Any],
    owner: str = ACTOR,
    prepare_batch: int = DEFAULT_PREPARE_BATCH,
    gate_wait_seconds: int = DEFAULT_GATE_WAIT_SECONDS,
) -> ActivationResult:
    """The full online change, crash-idempotent: every step keys on the
    candidate state, so a repeated run resumes where the crash left it
    (same owner; an expired lease is a takeover with a fence bump).

    The caller provides a plain session (no outer transaction): every
    step manages its own bounded transaction. A pre-publish failure is
    a terminal cleanup with ``failed`` (re-raised); a post-publish
    backoff/blocked state is RETURNED (the repair runner or the next
    run continues it)."""
    async with transaction(db):
        head_row = (
            await db.execute(
                select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == SCOPE)
            )
        ).scalars().first()
        if head_row is None:
            raise ActivationError("global runtime head missing")
        base = await db.get(ORMConfigSnapshot, head_row.active_config_snapshot_id)
        if base is None:
            raise ActivationError("effective snapshot missing")
        candidate, already_active = await upsert_online_candidate(
            db, audit, base_snapshot=base, requested_payload=requested_payload
        )
    if already_active:
        return ActivationResult(
            candidate_id=candidate.id, state="active", fence=0, already_active=True
        )

    async with transaction(db):
        await db.refresh(candidate)
        early_state = candidate.activation_state
    if early_state == "post_publish_blocked":
        # a blocked manifest is repair-lane territory: report it, never
        # re-acquire the slot or touch the manifest from the normal run
        return ActivationResult(
            candidate_id=candidate.id, state="post_publish_blocked", fence=0,
            published=True,
        )

    # the fence/owner of the current slot (a resumed run reuses them; a
    # fresh run acquires; an expired own lease is a self-takeover)
    async with transaction(db):
        head = await _lock_head(db)
        assert head is not None
        existing = _as_uuid(head.activating_config_snapshot_id)
        own_live = (
            existing == candidate.id
            and head.activation_lease_owner == owner
            and _lease_live(head)
        )
        own_dead = (
            existing == candidate.id
            and head.activation_lease_owner == owner
            and not _lease_live(head)
        )
    if own_live:
        fence = int(head.activation_fence or 0)
    elif existing is None or own_dead:
        fence = await acquire_activation(
            db, audit, candidate=candidate, owner=owner, gate_wait_seconds=gate_wait_seconds
        )
    elif existing == candidate.id:
        # foreign owner — only a dead lease can be taken over
        if _lease_live(head):
            raise ActivationError(
                "the candidate's activation lease is held by another live owner"
            )
        fence = await acquire_activation(
            db, audit, candidate=candidate, owner=owner, gate_wait_seconds=gate_wait_seconds
        )
    else:
        raise ActivationError("another candidate is being activated")

    async with transaction(db):
        await db.refresh(candidate)
        state = candidate.activation_state
    digest: str | None = None
    pending_count = 0
    questions_created = 0
    published = False
    resumed = own_live

    try:
        if state in ("preparing_heads", "ready", "publishing"):
            await freeze_cohort_online(db, candidate=candidate, fence=fence, owner=owner)
            await prepare_heads_online(
                db, candidate=candidate, fence=fence, owner=owner, batch_size=prepare_batch
            )
            digest = await verify_and_seal_online(
                db, candidate=candidate, fence=fence, owner=owner
            )
        elif state != "post_publish":
            raise ActivationError(f"cannot resume from state {state!r}")

        async with transaction(db):
            await db.refresh(candidate)
        if candidate.activation_state == "ready":
            pending_count = await publish_online(
                db, audit, candidate=candidate, fence=fence, owner=owner
            )
            published = True
        elif candidate.activation_state != "post_publish":
            raise ActivationError(
                f"publish did not reach post_publish: {candidate.activation_state!r}"
            )
        elif not published:
            pass  # resumed after the flip

        result = await run_post_publish(
            db, audit, candidate=candidate, fence=fence, owner=owner
        )
        questions_created = result.questions_created
        if result.state in ("post_publish",) or result.deferred:
            # partial: the backoff window / max_batches / a fenced
            # takeover left the manifest open — the next run continues
            return ActivationResult(
                candidate_id=candidate.id, state=result.state, fence=fence,
                published=published, resumed=resumed,
                cohort_count=int(candidate.activation_expected_head_count or 0),
                heads_digest=digest, pending_heads=pending_count,
                questions_created=questions_created,
            )
        if result.blocked:
            return ActivationResult(
                candidate_id=candidate.id, state="post_publish_blocked", fence=fence,
                published=True, resumed=resumed,
                cohort_count=int(candidate.activation_expected_head_count or 0),
                heads_digest=digest, pending_heads=pending_count,
                questions_created=questions_created,
            )
        if result.state != "active":
            raise ActivationError(
                f"post-publish run ended in {result.state!r} — resume on the next run"
            )
        return ActivationResult(
            candidate_id=candidate.id, state=result.state, fence=fence,
            published=True, resumed=resumed,
            cohort_count=int(candidate.activation_expected_head_count or 0),
            heads_digest=digest, pending_heads=pending_count,
            questions_created=questions_created,
        )
    except ActivationError as exc:
        # a pre-publish (or an unrecoverable post-publish) failure: the
        # SAME terminal cleanup with state=failed — a terminal state
        # with a non-empty slot is an invariant violation. The cleanup
        # is skipped when the candidate already reached a terminal
        # state (blocked/superseded/active) — those own their cleanup.
        async with transaction(db):
            await db.refresh(candidate)
            terminal = candidate.activation_state in _TERMINAL_STATES
        if not terminal:
            # the terminal cleanup commits in its own transaction — a
            # terminal state with a non-empty slot is an invariant
            # violation, so the row must be durable before the raise
            async with transaction(db):
                await _terminal_cleanup_tx(
                    db, audit, candidate=candidate, fence=fence, owner=owner,
                    outcome="failed", last_error=str(exc),
                )
        raise
