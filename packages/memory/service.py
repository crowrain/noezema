"""Memory service (T3.6, T3.7, §8.2, §14.1).

Applies the session's claim/evidence staging at the commit boundary
(synchronously, inside the fenced final transaction) and resolves the
claim views: the lifecycle is read from ``claim_assessment_heads`` via
the effective config snapshot (``runtime_config_heads``), never from the
mutable claims row.

Only the rules engine assigns grade/confidence (§3.7). pending/invalid
heads are not current evidence and not dependencies.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.canonical import canonical_sha256
from packages.domain.models.artifacts import ORMArtifact, ORMStagingOp
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.models.enums import (
    AssessmentState,
    AuditEventType,
    EffectiveGrade,
    EpistemicStatus,
    FreshnessStatus,
)
from packages.domain.models.memory import (
    ORMAssessmentEvidence,
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMEnvironmentManifest,
    ORMEvidence,
)
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.memory.evidence import (
    RULES_ENGINE_VERSION,
    computation_identity,
    environment_manifest_hash,
    local_observation_identity,
    observation_artifact_hash,
    rules_hash,
)
from packages.memory.rules_engine import (
    ClaimTypeRule,
    EvaluatedEvidence,
    RuleValidationError,
    evaluate,
    reverify_after,
)

GLOBAL_SCOPE = "global"


@dataclass(frozen=True)
class MemoryApplyResult:
    claims_created: int = 0
    claims_reused: int = 0
    evidence_added: int = 0
    evidence_deduped: int = 0
    assessments: int = 0
    problems: tuple[str, ...] = ()


@dataclass(frozen=True)
class ClaimViewEvidence:
    evidence_id: uuid.UUID
    kind: str
    relation: str
    role: str
    identity_hash: str


@dataclass(frozen=True)
class ClaimView:
    """The resolved view of one claim under the effective config."""

    claim_id: uuid.UUID
    statement: str
    claim_type: str
    assessment_state: AssessmentState
    epistemic_status: EpistemicStatus | None
    grade: EffectiveGrade | None
    confidence: float | None
    freshness: FreshnessStatus
    valid_from: datetime | None
    valid_to: datetime | None
    as_of: datetime | None
    reverify_after: datetime | None
    evidence: list[ClaimViewEvidence] = field(default_factory=list)
    counterevidence: list[ClaimViewEvidence] = field(default_factory=list)


class MemoryService:
    def __init__(self, snapshot: ORMConfigSnapshot) -> None:
        self.snapshot = snapshot
        self._rules: dict[str, ClaimTypeRule] = {
            str(claim_type): ClaimTypeRule.from_payload(str(claim_type), dict(payload))
            for claim_type, payload in snapshot.claim_type_rules.items()
        }
        self._rules_hash = rules_hash(dict(snapshot.claim_type_rules))

    # ── helpers ───────────────────────────────────────────────────────────

    def rule(self, claim_type: str) -> ClaimTypeRule:
        try:
            return self._rules[claim_type]
        except KeyError as exc:
            raise RuleValidationError(f"unknown claim_type {claim_type!r}") from exc

    def freshness_status(self, claim: ORMClaim, now: datetime) -> FreshnessStatus:
        """Expiry changes ONLY the freshness status (T3.7, §8.6) — never
        the grade or confidence. valid_to=NULL means an open end."""
        if claim.reverify_after is None:
            return FreshnessStatus.UNKNOWN
        if now < claim.reverify_after:
            return FreshnessStatus.FRESH
        return FreshnessStatus.DUE

    # ── commit-boundary apply ─────────────────────────────────────────────

    async def apply_claim_staging(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        evidence_records: list[Any],
        *,
        model_fingerprint: JsonDict | None = None,
        tool_schema_hash: str | None = None,
    ) -> MemoryApplyResult:
        """Apply the session's recorded claim/evidence staging ops inside
        the caller's (fenced) transaction. ``evidence_records`` are the
        in-memory observations of the session, referenced by index from
        the evidence ops — the trusted host recomputes every identity
        here; the M1 in-memory hash is not trusted."""
        rows = (
            (
                await db.execute(
                    select(ORMStagingOp)
                    .where(
                        ORMStagingOp.session_id == session.id,
                        ORMStagingOp.op.in_(("claim", "evidence")),
                        ORMStagingOp.state == "recorded",
                    )
                    .order_by(ORMStagingOp.created_at, ORMStagingOp.id)
                )
            )
            .scalars()
            .all()
        )
        claim_ops = [r for r in rows if r.op == "claim"]
        evidence_ops = [r for r in rows if r.op == "evidence"]
        if not claim_ops:
            return MemoryApplyResult()

        now = datetime.now(UTC)
        problems: list[str] = []
        counters = {"created": 0, "reused": 0, "added": 0, "deduped": 0, "assessments": 0}

        env_hash = environment_manifest_hash(
            str(session.id),
            model_fingerprint if model_fingerprint is not None else {},
            tool_schema_hash or "",
        )
        env = await self._environment_manifest(db, env_hash)

        # 1. claims (exact statement+type dedup against the corpus)
        claims: list[ORMClaim] = []
        claim_scopes: dict[uuid.UUID, JsonDict] = {}
        for row in claim_ops:
            payload = dict(row.payload)
            statement = str(payload.get("statement", ""))[:2000]
            claim_type = str(payload.get("claim_type", ""))
            if not statement or claim_type not in self._rules:
                problems.append(f"claim staging rejected: bad statement/type ({row.id})")
                continue
            scope = dict(payload.get("scope") or {})
            as_of_raw = payload.get("as_of")
            existing_claim = (
                (
                    await db.execute(
                        select(ORMClaim).where(
                            ORMClaim.statement == statement, ORMClaim.claim_type == claim_type
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_claim is not None:
                claims.append(existing_claim)
                claim_scopes[existing_claim.id] = scope
                counters["reused"] += 1
            else:
                claim = ORMClaim(
                    id=uuid.uuid4(),
                    statement=statement,
                    claim_type=claim_type,
                    as_of=datetime.fromisoformat(as_of_raw) if as_of_raw else None,
                    observed_at=now,
                    created_in_session=session.id,
                )
                db.add(claim)
                await db.flush()
                claims.append(claim)
                claim_scopes[claim.id] = scope
                counters["created"] += 1
                await audit.record(
                    AuditEventType.CLAIM_CREATED,
                    session_id=session.id,
                    payload={
                        "claim_id": str(claim.id),
                        "claim_type": claim_type,
                        "statement": statement[:500],
                        "scope": scope,
                    },
                    public_summary=f"claim: {statement[:120]}",
                )

        # 2. evidence (identity recomputed by the trusted host)
        linked: dict[uuid.UUID, list[ORMEvidence]] = {c.id: [] for c in claims}
        for row in evidence_ops:
            payload = dict(row.payload)
            index = int(payload.get("evidence_index", -1))
            claim_index = int(payload.get("claim_index", -1))
            relation = str(payload.get("relation", "supports"))
            if index < 0 or index >= len(evidence_records):
                problems.append(f"evidence staging rejected: index {index} out of range ({row.id})")
                continue
            if claim_index < 0 or claim_index >= len(claims):
                problems.append(f"evidence staging rejected: claim {claim_index} out of range ({row.id})")
                continue
            if relation not in ("supports", "counters"):
                problems.append(f"evidence staging rejected: bad relation {relation!r} ({row.id})")
                continue
            record = evidence_records[index]
            claim = claims[claim_index]
            kind = str(record.kind.value)
            identity, artifact_id = await self._identity_for(db, record, kind, env_hash)
            if artifact_id is None and kind in (
                "computation",
                "formal_check",
                "experiment_run",
                "local_observation",
            ):
                problems.append(f"evidence staging rejected: no artifact for {kind} ({row.id})")
                continue
            existing_ev = (
                (
                    await db.execute(
                        select(ORMEvidence).where(
                            ORMEvidence.claim_id == claim.id,
                            ORMEvidence.evidence_kind == kind,
                            ORMEvidence.identity_hash == identity,
                        )
                    )
                )
                .scalars()
                .first()
            )
            if existing_ev is not None:
                ev: ORMEvidence = existing_ev
                counters["deduped"] += 1
            else:
                ev = ORMEvidence(
                    id=uuid.uuid4(),
                    claim_id=claim.id,
                    relation=relation,
                    evidence_kind=kind,
                    identity_hash=identity,
                    scope=claim_scopes.get(claim.id, {}),
                    observation_artifact_id=artifact_id,
                    environment_manifest_id=env.id
                    if kind in ("experiment_run", "local_observation")
                    else None,
                    created_in_session=session.id,
                )
                db.add(ev)
                counters["added"] += 1
            if ev not in linked[claim.id]:
                linked[claim.id].append(ev)

        # 3. assessment per claim (rules engine is the only producer, §3.7)
        for claim in claims:
            all_evidence = list(
                (
                    await db.execute(select(ORMEvidence).where(ORMEvidence.claim_id == claim.id))
                )
                .scalars()
                .all()
            )
            try:
                ok = await self._assess(
                    db, audit, session, claim, claim_scopes.get(claim.id, {}), all_evidence, env_hash, now
                )
            except RuleValidationError as exc:
                problems.append(f"assessment rejected for {claim.id}: {exc}")
                continue
            if ok:
                counters["assessments"] += 1

        return MemoryApplyResult(
            claims_created=counters["created"],
            claims_reused=counters["reused"],
            evidence_added=counters["added"],
            evidence_deduped=counters["deduped"],
            assessments=counters["assessments"],
            problems=tuple(problems),
        )

    async def _identity_for(
        self, db: AsyncSession, record: Any, kind: str, env_hash: str
    ) -> tuple[str, uuid.UUID | None]:
        """Recompute (identity, artifact_id) for one observation record.
        The identity hash is content/provenance (§14.3) — for computation
        it folds in the artifact's sha256, the exact input and the tool
        fingerprint."""
        payload = dict(record.payload or {})
        artifact = await self._ensure_artifact(db, payload)
        if kind == "computation":
            result_text = str(payload.get("stdout", ""))
            inputs = str(payload.get("code", ""))
            identity = computation_identity(result_text, inputs, artifact.sha256)
        elif kind in ("local_observation", "experiment_run"):
            content = str(payload.get("content", payload.get("entries", "")))
            identity = local_observation_identity(content, env_hash)
        else:  # source kinds are not produced by session tools in M3
            identity = observation_artifact_hash(payload)
        return identity, artifact.id

    async def _ensure_artifact(self, db: AsyncSession, payload: JsonDict) -> ORMArtifact:
        """The observation artifact (content-addressed registry row)."""
        sha = observation_artifact_hash(payload)
        existing = (
            (await db.execute(select(ORMArtifact).where(ORMArtifact.sha256 == sha)))
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        artifact = ORMArtifact(
            id=uuid.uuid4(),
            sha256=sha,
            size=len(canonical_sha256(payload)),
            mime="application/noezema+json",
            origin="session_workspace",
            trust_class="session_workspace",
        )
        db.add(artifact)
        await db.flush()
        return artifact

    async def _environment_manifest(self, db: AsyncSession, env_hash: str) -> ORMEnvironmentManifest:
        existing = (
            (
                await db.execute(
                    select(ORMEnvironmentManifest).where(
                        ORMEnvironmentManifest.protocol_hash == env_hash
                    )
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            return existing
        env = ORMEnvironmentManifest(id=uuid.uuid4(), protocol_hash=env_hash)
        db.add(env)
        await db.flush()
        return env

    async def _assess(
        self,
        db: AsyncSession,
        audit: AuditService,
        session: ORMSession,
        claim: ORMClaim,
        claim_scope: JsonDict,
        all_evidence: list[ORMEvidence],
        env_hash: str,
        now: datetime,
    ) -> bool:
        """One deterministic assessment + head upsert (same transaction)."""
        rule = self.rule(claim.claim_type)
        evs = [
            EvaluatedEvidence(
                identity_hash=e.identity_hash,
                kind=e.evidence_kind,
                relation=e.relation,
                scope=dict(e.scope or {}),
                # conservative: session-generated evidence from the same
                # environment is ONE independence group (no false
                # independence); source evidence uses the snapshot groups
                independence_group=f"env:{env_hash[:16]}",
            )
            for e in all_evidence
        ]
        result = evaluate(
            claim.claim_type,
            rule,
            claim_scope=claim_scope,
            evidences=evs,
            has_as_of=claim.as_of is not None,
        )

        head = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == claim.id,
                        ORMClaimAssessmentHead.config_snapshot_id == self.snapshot.id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if head is not None and head.current_assessment_id is not None:
            prev = await db.get(ORMClaimAssessment, head.current_assessment_id)
            if prev is not None and prev.valid:
                prev.valid = False
                prev.invalidation_reason = "superseded"

        assessment = ORMClaimAssessment(
            id=uuid.uuid4(),
            claim_id=claim.id,
            effective_grade=result.grade.value,
            epistemic_status=result.epistemic_status.value,
            rules_version=RULES_ENGINE_VERSION,
            rules_hash=self._rules_hash,
            evidence_set_hash=_evidence_set_hash(all_evidence),
            assessed_scope=dict(claim_scope),
            confidence=result.confidence,
            valid=True,
            created_in_session=session.id,
        )
        db.add(assessment)
        await db.flush()

        for e in all_evidence:
            db.add(
                ORMAssessmentEvidence(
                    assessment_id=assessment.id,
                    evidence_id=e.id,
                    role="counter" if e.relation == "counters" else "support",
                )
            )

        if head is None:
            head = ORMClaimAssessmentHead(
                claim_id=claim.id,
                config_snapshot_id=self.snapshot.id,
                assessment_state=AssessmentState.CURRENT.value,
                current_assessment_id=assessment.id,
                epistemic_status=result.epistemic_status.value,
                prepared_by="session",
            )
            db.add(head)
        else:
            head.assessment_state = AssessmentState.CURRENT.value
            head.current_assessment_id = assessment.id
            head.epistemic_status = result.epistemic_status.value
            head.prepared_by = "session"

        # freshness: reverify_after is derived, not a mutable clock
        claim.reverify_after = reverify_after(result, claim.as_of, now)
        claim.freshness_status = FreshnessStatus.FRESH.value

        await db.flush()
        await audit.record(
            AuditEventType.CLAIM_ASSESSED,
            session_id=session.id,
            payload={
                "claim_id": str(claim.id),
                "assessment_id": str(assessment.id),
                "grade": result.grade.value,
                "epistemic_status": result.epistemic_status.value,
                "confidence": result.confidence,
                "reasons": list(result.reasons),
                "rules_hash": self._rules_hash,
            },
            public_summary=f"claim assessed: {result.grade.value}/{result.epistemic_status.value}",
        )
        return True

    # ── query path (§14.1: pointer equality, not activation_state) ───────

    async def claim_view(self, db: AsyncSession, claim_id: uuid.UUID) -> ClaimView | None:
        """Resolve one claim through the effective config snapshot."""
        claim = await db.get(ORMClaim, claim_id)
        if claim is None:
            return None
        head_row = (
            (
                await db.execute(
                    select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == GLOBAL_SCOPE)
                )
            )
            .scalars()
            .first()
        )
        if head_row is None:
            return None
        head = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead).where(
                        ORMClaimAssessmentHead.claim_id == claim_id,
                        ORMClaimAssessmentHead.config_snapshot_id == head_row.active_config_snapshot_id,
                    )
                )
            )
            .scalars()
            .first()
        )
        if head is None:
            return None
        assessment = None
        if head.current_assessment_id is not None:
            assessment = await db.get(ORMClaimAssessment, head.current_assessment_id)
        evs: list[ORMEvidence] = []
        roles: dict[uuid.UUID, str] = {}
        if assessment is not None:
            links = (
                (
                    await db.execute(
                        select(ORMAssessmentEvidence).where(
                            ORMAssessmentEvidence.assessment_id == assessment.id
                        )
                    )
                )
                .scalars()
                .all()
            )
            for link in links:
                ev = await db.get(ORMEvidence, link.evidence_id)
                if ev is not None:
                    evs.append(ev)
                    roles[ev.id] = link.role
        evidence = [
            ClaimViewEvidence(e.id, e.evidence_kind, e.relation, roles.get(e.id, "support"), e.identity_hash)
            for e in evs
            if e.relation == "supports"
        ]
        counterevidence = [
            ClaimViewEvidence(e.id, e.evidence_kind, e.relation, roles.get(e.id, "counter"), e.identity_hash)
            for e in evs
            if e.relation == "counters"
        ]
        return ClaimView(
            claim_id=claim.id,
            statement=claim.statement,
            claim_type=claim.claim_type,
            assessment_state=AssessmentState(head.assessment_state),
            epistemic_status=EpistemicStatus(head.epistemic_status) if head.epistemic_status else None,
            grade=EffectiveGrade(assessment.effective_grade) if assessment else None,
            confidence=assessment.confidence if assessment else None,
            freshness=self.freshness_status(claim, datetime.now(UTC)),
            valid_from=claim.valid_from,
            valid_to=claim.valid_to,
            as_of=claim.as_of,
            reverify_after=claim.reverify_after,
            evidence=evidence,
            counterevidence=counterevidence,
        )

    async def pending_invalid_claims(self, db: AsyncSession, limit: int = 100) -> list[ORMClaim]:
        """Claims whose effective head is pending/invalid — never current
        evidence, never a dependency (§8.6)."""
        head_row = (
            (
                await db.execute(
                    select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == GLOBAL_SCOPE)
                )
            )
            .scalars()
            .first()
        )
        if head_row is None:
            return []
        rows = (
            (
                await db.execute(
                    select(ORMClaimAssessmentHead)
                    .where(
                        ORMClaimAssessmentHead.config_snapshot_id == head_row.active_config_snapshot_id,
                        ORMClaimAssessmentHead.assessment_state.in_(
                            (AssessmentState.PENDING.value, AssessmentState.INVALID.value)
                        ),
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
        claims = []
        for head in rows:
            claim = await db.get(ORMClaim, head.claim_id)
            if claim is not None:
                claims.append(claim)
        return claims


def _evidence_set_hash(evidence: list[ORMEvidence]) -> str:
    return canonical_sha256(
        sorted(f"{e.evidence_kind}:{e.relation}:{e.identity_hash}" for e in evidence)
    )
