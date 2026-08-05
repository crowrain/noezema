"""Curator service: claim generation, reconciliation, and curtailment.

Responsibilities:
- Generate structured claims from raw evidence gathered during exploration
- Reconcile claims: detect duplicates, contradictions, and merge when possible
- Curtail outdated claims based on freshness status
- Manage claim assessment heads
"""

import uuid
import hashlib
import json
from datetime import datetime, timedelta

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.orm_claim import (
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMConfigSnapshot,
    ORMEvidence,
)
from packages.domain.models.enums import (
    EffectiveGrade,
    EpistemicStatus,
    ClaimType,
    EvidenceRelation,
)
from packages.memory.rules_engine import RulesEngine


class CuratorService:
    """Manage the full claim lifecycle: generate → reconcile → curtail → assess."""

    def __init__(self, session: AsyncSession, rules_engine: RulesEngine):
        self.session = session
        self.rules = rules_engine

    # ------------------------------------------------------------------
    # Claim Generation
    # ------------------------------------------------------------------

    async def generate_claims_from_evidence(
        self,
        session_id: uuid.UUID,
        evidence_list: list[dict],
        claim_type: str = ClaimType.EMPIRICAL_CONJECTURE.value,
    ) -> list[ORMClaim]:
        """Generate claims grouped by tool/evidence kind, attach evidence, assess."""
        if not evidence_list:
            return []

        # Group evidence by kind
        grouped: dict[str, list[dict]] = {}
        for ev in evidence_list:
            kind = ev.get("tool", ev.get("kind", "unknown"))
            grouped.setdefault(kind, []).append(ev)

        claims = []
        for kind, evs in grouped.items():
            # Build claim statement
            tools_used = ", ".join(set(e.get("tool", "unknown") for e in evs))
            statement = (
                f"Evidence gathered via {tools_used}: "
                f"{len(evs)} data points collected during session"
            )

            claim = ORMClaim(
                statement=statement,
                claim_type=claim_type,
                freshness_status="fresh",
                valid_from=datetime.utcnow(),
                created_in_session=session_id,
            )
            self.session.add(claim)
            await self.session.flush()

            # Attach evidence
            for ev in evs:
                identity_hash = hashlib.sha256(
                    f"{ev.get('tool', '')}:{json.dumps(ev.get('result', {}))}".encode()
                ).hexdigest()
                evidence = ORMEvidence(
                    claim_id=claim.id,
                    relation=EvidenceRelation.SUPPORTS,
                    evidence_kind=kind,
                    identity_hash=identity_hash,
                    created_in_session=session_id,
                )
                self.session.add(evidence)

            claims.append(claim)

        await self.session.flush()
        return claims

    # ------------------------------------------------------------------
    # Reconciliation: detect duplicates and contradictions
    # ------------------------------------------------------------------

    async def reconcile_claims(self, session_id: uuid.UUID) -> dict:
        """Detect duplicate claims and contradictory evidence.

        Returns reconciliation report with stats.
        """
        # Load all claims from this session
        result = await self.session.execute(
            select(ORMClaim).where(ORMClaim.created_in_session == session_id)
        )
        claims = result.scalars().all()

        report = {
            "total_claims": len(claims),
            "duplicates_merged": 0,
            "contradictions_found": 0,
            "claims_remaining": 0,
        }

        # Deduplicate by statement similarity (hash-based for MVP)
        seen_hashes: dict[str, ORMClaim] = {}
        for claim in claims:
            stmt_hash = hashlib.sha256(claim.statement.encode()).hexdigest()[:16]
            if stmt_hash in seen_hashes:
                # Merge: delete duplicate, keep original
                existing = seen_hashes[stmt_hash]
                # Move evidence from duplicate to existing
                ev_result = await self.session.execute(
                    select(ORMEvidence).where(ORMEvidence.claim_id == claim.id)
                )
                for ev in ev_result.scalars().all():
                    ev.claim_id = existing.id
                await self.session.delete(claim)
                report["duplicates_merged"] += 1
            else:
                seen_hashes[stmt_hash] = claim

        # Check for contradictions: claims with both supports and counters
        for claim in seen_hashes.values():
            ev_result = await self.session.execute(
                select(ORMEvidence).where(ORMEvidence.claim_id == claim.id)
            )
            evidences = ev_result.scalars().all()

            has_supports = any(
                e.relation == EvidenceRelation.SUPPORTS for e in evidences
            )
            has_counters = any(
                e.relation == EvidenceRelation.COUNTERS for e in evidences
            )

            if has_supports and has_counters:
                report["contradictions_found"] += 1

        report["claims_remaining"] = len(seen_hashes)
        await self.session.flush()
        return report

    # ------------------------------------------------------------------
    # Curtailment: mark stale claims
    # ------------------------------------------------------------------

    async def curtail_stale_claims(
        self,
        max_age_days: int = 30,
        scope: str = "default",
    ) -> int:
        """Mark claims older than max_age_days as stale for re-verification.

        Returns number of claims curtailed.
        """
        cutoff = datetime.utcnow() - timedelta(days=max_age_days)

        result = await self.session.execute(
            select(ORMClaim)
            .where(
                ORMClaim.valid_from < cutoff,
                ORMClaim.freshness_status == "fresh",
            )
        )
        stale_claims = result.scalars().all()

        for claim in stale_claims:
            claim.freshness_status = "stale"
            claim.reverify_after = datetime.utcnow()

        # Update assessment heads for stale claims
        if stale_claims:
            stale_ids = [c.id for c in stale_claims]
            head_result = await self.session.execute(
                select(ORMClaimAssessmentHead).where(
                    ORMClaimAssessmentHead.claim_id.in_(stale_ids)
                )
            )
            for head in head_result.scalars().all():
                head.assessment_state = "pending"
                head.epistemic_status = EpistemicStatus.DEFERRED.value

        await self.session.flush()
        return len(stale_claims)

    # ------------------------------------------------------------------
    # Assess all claims in a session
    # ------------------------------------------------------------------

    async def assess_session_claims(self, session_id: uuid.UUID) -> list[dict]:
        """Assess all claims from a session using the rules engine.

        Returns list of assessment summaries.
        """
        result = await self.session.execute(
            select(ORMClaim).where(ORMClaim.created_in_session == session_id)
        )
        claims = result.scalars().all()

        assessments = []
        for claim in claims:
            # Load evidence
            ev_result = await self.session.execute(
                select(ORMEvidence).where(ORMEvidence.claim_id == claim.id)
            )
            evidences = ev_result.scalars().all()

            evidence_dicts = [
                {
                    "hash": e.identity_hash,
                    "kind": e.evidence_kind,
                    "relation": e.relation,
                }
                for e in evidences
            ]

            # Assess via rules engine
            assessment_data = self.rules.assess_from_dicts(
                claim_statement=claim.statement,
                claim_type=claim.claim_type,
                evidence_dicts=evidence_dicts,
            )

            # Persist assessment
            orm_assessment = ORMClaimAssessment(
                claim_id=claim.id,
                effective_grade=assessment_data["grade"].value,
                epistemic_status=assessment_data["status"].value,
                rules_hash=assessment_data["rules_hash"],
                evidence_set_hash=assessment_data["evidence_set_hash"],
                confidence=assessment_data["confidence"],
                valid=True,
                created_in_session=session_id,
            )
            self.session.add(orm_assessment)

            # Update head
            existing_head = await self.session.execute(
                select(ORMClaimAssessmentHead).where(
                    ORMClaimAssessmentHead.claim_id == claim.id
                )
            )
            head_row = existing_head.scalar_one_or_none()
            if head_row is None:
                head = ORMClaimAssessmentHead(
                    claim_id=claim.id,
                    config_snapshot_id=uuid.uuid4(),
                    assessment_state="current",
                    current_assessment_id=orm_assessment.id,
                    epistemic_status=assessment_data["status"].value,
                    prepared_by="session",
                )
                self.session.add(head)
            else:
                head_row.current_assessment_id = orm_assessment.id
                head_row.assessment_state = "current"
                head_row.epistemic_status = assessment_data["status"].value

            assessments.append({
                "claim_id": str(claim.id),
                "grade": assessment_data["grade"].value,
                "status": assessment_data["status"].value,
                "confidence": assessment_data["confidence"],
            })

        await self.session.flush()
        return assessments
