"""Offline rules change — the MVP activation protocol (T3.12, §8.7.1).

The runtime is STOPPED; one systemd-owned maintenance instance runs the
change. There is no knowledge-writer contention, so there is no lease,
fencing token, activating pointer or repair runner — host exclusivity is
the systemd scope and DB exclusivity a session-level PostgreSQL advisory
lock on the scope.

Lifecycle: ``draft -> preparing_heads -> ready -> active`` (with a
``failed`` branch). The candidate identity is deterministic via
``UNIQUE(base_snapshot_id, payload_sha256) WHERE activation_mode='offline'
AND activation_state <> 'failed'`` — a repeated script continues the same
candidate or confirms an already-committed result, it never creates the
next config. The atomic publish moves the runtime pointer and inserts all
invalid-head questions (deterministic UUIDv5 IDs) in ONE transaction.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.config import config_snapshot_sha256
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.models.enums import (
    AssessmentState,
    AuditEventType,
    QuestionOrigin,
)
from packages.domain.models.memory import ORMClaim, ORMClaimAssessment, ORMClaimAssessmentHead
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigService
from packages.memory.rules_engine import (
    ClaimTypeRule,
    EvaluatedEvidence,
    evaluate,
)

GLOBAL_SCOPE = "global"


class OfflineRulesError(RuntimeError):
    """The offline change cannot proceed (advisory lock, precondition,
    seal mismatch, ...)."""


@dataclass(frozen=True)
class OfflineResult:
    candidate_id: uuid.UUID
    state: str
    already_active: bool = False
    published: bool = False
    cohort_count: int = 0
    heads_digest: str | None = None
    invalid_questions_created: int = 0


def _advisory_lock_name(scope: str) -> str:
    return f"noezema:offline_rules:{scope}"


async def _acquire_advisory_lock(db: AsyncSession, scope: str) -> bool:
    got = (
        await db.execute(
            text("SELECT pg_try_advisory_lock(hashtext(:name))"), {"name": _advisory_lock_name(scope)}
        )
    ).scalar_one()
    return bool(got)


async def _release_advisory_lock(db: AsyncSession, scope: str) -> None:
    await db.execute(
        text("SELECT pg_advisory_unlock(hashtext(:name))"), {"name": _advisory_lock_name(scope)}
    )


async def _preconditions_ok(db: AsyncSession) -> list[str]:
    """No active session, unresolved commit attempt or reconciling_commit."""
    problems: list[str] = []
    active_sessions = (
        await db.execute(
            text(
                "SELECT count(*) FROM sessions WHERE state IN "
                "('selecting_question','planning','exploring','verifying','consolidating','reporting','committing')"
            )
        )
    ).scalar_one()
    if int(active_sessions) > 0:
        problems.append(f"active sessions present: {active_sessions}")
    unresolved = (
        await db.execute(
            text("SELECT count(*) FROM commit_attempts WHERE status IN ('prepared','reconciling')")
        )
    ).scalar_one()
    if int(unresolved) > 0:
        problems.append(f"unresolved commit attempts: {unresolved}")
    reconciling = (
        await db.execute(text("SELECT count(*) FROM sessions WHERE state='reconciling_commit'"))
    ).scalar_one()
    if int(reconciling) > 0:
        problems.append(f"reconciling_commit sessions: {reconciling}")
    return problems


async def upsert_candidate(
    db: AsyncSession,
    audit: AuditService,
    *,
    base_snapshot: ORMConfigSnapshot,
    requested_payload: dict[str, Any],
) -> tuple[ORMConfigSnapshot, bool]:
    """Deterministic candidate upsert. Returns (candidate, already_active).

    If the effective snapshot already has the requested payload_sha256 the
    operation is already complete (idempotent success)."""
    payload_sha = canonical_sha256(requested_payload)
    effective = await ConfigService.get_effective(db)
    if effective.payload_sha256 == payload_sha:
        return effective, True

    # INSERT ... ON CONFLICT on the partial unique (base, payload) for
    # offline non-failed candidates -> always one unresolved candidate
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
                        wake_schedule
                    ) VALUES (
                        :id, :base, :payload_sha, :sha, 'offline', 'draft',
                        :model, :embeddings, :prompts, :policy, :curiosity,
                        :token_budgets, :session_limits, :activation_limits, :claim_type_rules,
                        :wake_schedule
                    )
                    ON CONFLICT (base_snapshot_id, payload_sha256)
                    WHERE activation_mode = 'offline' AND activation_state <> 'failed'
                    DO UPDATE SET activation_state = 'draft'
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
                    # T3.29: inherit the wake schedule from the base snapshot
                    # when the requested payload does not set it explicitly.
                    "wake_schedule": _json(
                        requested_payload.get("wake_schedule", base_snapshot.wake_schedule)
                    ),
                },
            )
        )
        .mappings()
        .one()
    )
    candidate = await db.get(ORMConfigSnapshot, uuid.UUID(str(row["id"])))
    assert candidate is not None
    await audit.record(
        AuditEventType.CONFIG_SNAPSHOT_CREATED,
        payload={
            "candidate_id": str(candidate.id),
            "base_snapshot_id": str(base_snapshot.id),
            "payload_sha256": payload_sha,
            "activation_mode": "offline",
        },
    )
    return candidate, False


def _json(value: Any) -> str:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


async def freeze_cohort(db: AsyncSession, *, candidate: ORMConfigSnapshot) -> int:
    """Freeze the cohort against the current knowledge revision and store
    the manifest hash + expected head count on the candidate."""
    cohort_revision = (
        await db.execute(text("SELECT revision FROM domain_revisions WHERE scope='knowledge'"))
    ).scalar_one()
    claim_ids = [r[0] for r in (await db.execute(text("SELECT id FROM claims ORDER BY id"))).all()]
    manifest = {"cohort_revision": int(cohort_revision), "claim_ids": [str(c) for c in claim_ids]}
    candidate.activation_state = "preparing_heads"
    candidate.activation_cohort_revision = int(cohort_revision)
    candidate.activation_manifest_hash = canonical_sha256(manifest)
    candidate.activation_expected_head_count = len(claim_ids)
    await db.flush()
    return len(claim_ids)


async def prepare_shadow_heads(db: AsyncSession, *, candidate: ORMConfigSnapshot) -> int:
    """Prepare one shadow head per cohort claim under the candidate, using
    the candidate's claim-type rules (the rules engine is the only
    grade/confidence producer). Returns the number of heads written."""
    rules = {
        str(ct): ClaimTypeRule.from_payload(str(ct), dict(payload))
        for ct, payload in candidate.claim_type_rules.items()
    }
    rules_hash = canonical_sha256(dict(candidate.claim_type_rules))
    claims = (await db.execute(select(ORMClaim).order_by(ORMClaim.id))).scalars().all()
    count = 0
    for claim in claims:
        rule = rules.get(claim.claim_type)
        evs = (
            (
                await db.execute(
                    text(
                        "SELECT identity_hash, evidence_kind, relation, scope FROM evidence "
                        "WHERE claim_id=:c ORDER BY id"
                    ),
                    {"c": str(claim.id)},
                )
            )
            .mappings()
            .all()
        )
        # the claim's scope is derived from its evidence (the claim row
        # does not persist a scope of its own)
        merged_scope: dict[str, Any] = {}
        for e in evs:
            for k, v in (e["scope"] or {}).items():
                merged_scope.setdefault(k, v)
        if rule is None:
            # unknown type under the new rules -> the claim cannot be
            # current under the candidate (invalid)
            state, grade, status, conf = AssessmentState.INVALID, None, None, None
        else:
            ev_list = [
                EvaluatedEvidence(
                    identity_hash=e["identity_hash"],
                    kind=e["evidence_kind"],
                    relation=e["relation"],
                    scope=dict(e["scope"] or {}),
                    independence_group="shadow",
                )
                for e in evs
            ]
            result = evaluate(
                claim.claim_type, rule, merged_scope, evidences=ev_list, has_as_of=claim.as_of is not None
            )
            if result.epistemic_status.value == "deferred":
                # requires_as_of but the claim has no as_of: it cannot be
                # assessed yet -> PENDING head (no current assessment)
                state = AssessmentState.PENDING
                grade, status, conf = None, None, None
            else:
                state = AssessmentState.CURRENT
                grade, status, conf = result.grade.value, result.epistemic_status.value, result.confidence

        head = (
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
        assessment_id = None
        if state is AssessmentState.CURRENT:
            assessment = ORMClaimAssessment(
                id=uuid.uuid4(),
                claim_id=claim.id,
                effective_grade=grade or "E0",
                epistemic_status=status or "hypothesis",
                rules_version="rules-v1",
                rules_hash=rules_hash,
                evidence_set_hash=canonical_sha256(
                    sorted(f"{e['evidence_kind']}:{e['relation']}:{e['identity_hash']}" for e in evs)
                ),
                assessed_scope={},
                confidence=conf or 0.0,
                valid=True,
            )
            db.add(assessment)
            await db.flush()
            assessment_id = assessment.id

        if head is None:
            db.add(
                ORMClaimAssessmentHead(
                    claim_id=claim.id,
                    config_snapshot_id=candidate.id,
                    assessment_state=state.value,
                    current_assessment_id=assessment_id,
                    epistemic_status=status,
                    prepared_by="rules_activation",
                )
            )
        else:
            head.assessment_state = state.value
            head.current_assessment_id = assessment_id
            head.epistemic_status = status
            head.prepared_by = "rules_activation"
        count += 1
    await db.flush()
    return count


async def verification_seal(db: AsyncSession, *, candidate: ORMConfigSnapshot) -> str:
    """Compute the canonical heads digest + counts over the complete
    cohort and persist the immutable seal + state=ready."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT claim_id, assessment_state, current_assessment_id, epistemic_status "
                    "FROM claim_assessment_heads WHERE config_snapshot_id=:c ORDER BY claim_id"
                ),
                {"c": str(candidate.id)},
            )
        )
        .mappings()
        .all()
    )
    digest_body = [
        {
            "claim_id": str(r["claim_id"]),
            "assessment_state": r["assessment_state"],
            "current_assessment_id": str(r["current_assessment_id"]) if r["current_assessment_id"] else None,
            "epistemic_status": r["epistemic_status"],
        }
        for r in rows
    ]
    digest = canonical_sha256(digest_body)
    if candidate.activation_expected_head_count is not None and len(rows) != int(
        candidate.activation_expected_head_count
    ):
        raise OfflineRulesError(
            f"incomplete cohort: {len(rows)} heads != expected {candidate.activation_expected_head_count}"
        )
    candidate.activation_state = "ready"
    candidate.activation_heads_sha256 = digest
    candidate.activation_verified_head_count = len(rows)
    candidate.activation_verified_at = None
    await db.flush()
    return digest


async def atomic_publish(
    db: AsyncSession,
    audit: AuditService,
    *,
    candidate: ORMConfigSnapshot,
    question_namespace: uuid.UUID,
) -> OfflineResult:
    """The atomic publish (runtime_config_head -> knowledge) in ONE
    transaction: verify the advisory lock + preconditions + seal, move the
    pointer, and insert all invalid-head questions with deterministic
    UUIDv5 IDs."""
    scope = GLOBAL_SCOPE
    if not await _acquire_advisory_lock(db, scope):
        raise OfflineRulesError("could not acquire the offline advisory lock at publish")
    try:
        problems = await _preconditions_ok(db)
        if problems:
            raise OfflineRulesError("publish preconditions failed: " + "; ".join(problems))

        head = (
            (
                await db.execute(
                    select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == scope).with_for_update()
                )
            )
            .scalars()
            .first()
        )
        if head is None:
            raise OfflineRulesError("global runtime head missing")
        if head.active_config_snapshot_id != candidate.base_snapshot_id:
            raise OfflineRulesError("active pointer is not the candidate base revision")
        if candidate.activation_state != "ready":
            raise OfflineRulesError("candidate is not ready (seal missing)")
        if not candidate.activation_heads_sha256:
            raise OfflineRulesError("verification seal missing")

        # invalid-head questions (deterministic UUIDv5)
        invalid_rows = (
            (
                await db.execute(
                    text(
                        "SELECT claim_id FROM claim_assessment_heads "
                        "WHERE config_snapshot_id=:c AND assessment_state='invalid' ORDER BY claim_id"
                    ),
                    {"c": str(candidate.id)},
                )
            )
            .scalars()
            .all()
        )
        created = 0
        for claim_id in invalid_rows:
            qid = uuid.uuid5(question_namespace, f"invalid-assessment:{candidate.id}:{claim_id}")
            exists = (
                (
                    await db.execute(text("SELECT 1 FROM questions WHERE id=:q"), {"q": str(qid)})
                )
                .scalar_one_or_none()
            )
            if exists is None:
                claim = await db.get(ORMClaim, claim_id)
                stmt = claim.statement if claim is not None else f"claim {claim_id}"
                db.add(
                    ORMQuestion(
                        id=qid,
                        text=(
                            f"Переоценить утверждение (инвалидная оценка при смене правил): "
                            f"{stmt[:1900]}"
                        )[:2000],
                        origin=QuestionOrigin.PREVIOUS_RESULT.value,
                    )
                )
                created += 1

        # the flip: pointer + candidate -> active, one transaction
        head.active_config_snapshot_id = candidate.id
        candidate.activation_state = "active"
        await db.flush()
        await audit.record(
            AuditEventType.CONFIG_ACTIVATED,
            payload={
                "candidate_id": str(candidate.id),
                "base_snapshot_id": str(candidate.base_snapshot_id),
                "invalid_questions_created": created,
            },
            public_summary=f"offline rules activated: {candidate.id}",
        )
        return OfflineResult(
            candidate_id=candidate.id,
            state="active",
            already_active=False,
            published=True,
            cohort_count=int(candidate.activation_expected_head_count or 0),
            heads_digest=candidate.activation_heads_sha256,
            invalid_questions_created=created,
        )
    finally:
        await _release_advisory_lock(db, scope)


async def run_offline_change(
    db: AsyncSession,
    audit: AuditService,
    *,
    requested_payload: dict[str, Any],
    question_namespace: uuid.UUID,
    scope: str = GLOBAL_SCOPE,
) -> OfflineResult:
    """The full offline change in the caller's transaction (advisory lock
    held across the run). Crash-idempotent: a repeated call continues the
    same candidate or confirms the already-active result."""
    if not await _acquire_advisory_lock(db, scope):
        raise OfflineRulesError("could not acquire the offline advisory lock")
    try:
        problems = await _preconditions_ok(db)
        if problems:
            raise OfflineRulesError("preconditions failed: " + "; ".join(problems))

        effective = await ConfigService.get_effective(db)
        candidate, already_active = await upsert_candidate(
            db, audit, base_snapshot=effective, requested_payload=requested_payload
        )
        if already_active:
            return OfflineResult(
                candidate_id=candidate.id, state="active", already_active=True
            )

        await freeze_cohort(db, candidate=candidate)
        await prepare_shadow_heads(db, candidate=candidate)
        digest = await verification_seal(db, candidate=candidate)
        if candidate.activation_state == "active":
            # a previous run already published this candidate
            return OfflineResult(
                candidate_id=candidate.id,
                state="active",
                already_active=True,
                cohort_count=int(candidate.activation_expected_head_count or 0),
                heads_digest=digest,
            )
        return await atomic_publish(db, audit, candidate=candidate, question_namespace=question_namespace)
    finally:
        await _release_advisory_lock(db, scope)
