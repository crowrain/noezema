"""Memory service: claim lifecycle, evidence management, assessment via rules engine."""

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.orm_claim import (
    ORMClaim,
    ORMEvidence,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
)
from packages.domain.models.enums import EvidenceRelation, EffectiveGrade
from packages.memory.rules_engine import RulesEngine


class MemoryService:
    """Manage claims, evidence, and assessments in the database."""

    def __init__(self, session: AsyncSession, rules_engine: RulesEngine):
        self.session = session
        self.rules = rules_engine

    async def create_claim(
        self,
        statement: str,
        claim_type: str,
        session_id: uuid.UUID,
        valid_for_days: int = 30,
    ) -> ORMClaim:
        """Create a new claim."""
        now = datetime.utcnow()
        claim = ORMClaim(
            statement=statement,
            claim_type=claim_type,
            freshness_status="fresh",
            valid_from=now,
            valid_to=now,
            reverify_after=now,
            created_in_session=session_id,
        )
        self.session.add(claim)
        await self.session.flush()
        return claim

    async def add_evidence(
        self,
        claim_id: uuid.UUID,
        relation: str,
        evidence_kind: str,
        identity_hash: str,
        session_id: uuid.UUID | None = None,
    ) -> ORMEvidence:
        """Attach evidence to a claim."""
        evidence = ORMEvidence(
            claim_id=claim_id,
            relation=relation,
            evidence_kind=evidence_kind,
            identity_hash=identity_hash,
            created_in_session=session_id,
        )
        self.session.add(evidence)
        await self.session.flush()
        return evidence

    async def assess_claim(
        self,
        claim_id: uuid.UUID,
        session_id: uuid.UUID | None = None,
    ) -> ORMClaimAssessment:
        """Run rules engine on a claim and persist the assessment."""
        claim = await self.session.get(ORMClaim, claim_id)
        if claim is None:
            raise ValueError(f"Claim {claim_id} not found")

        # Load evidence for this claim
        from sqlalchemy import select
        result = await self.session.execute(
            select(ORMEvidence).where(ORMEvidence.claim_id == claim_id)
        )
        evidence_list = result.scalars().all()

        # Build evidence dicts for rules engine
        evidence_dicts = []
        for ev in evidence_list:
            evidence_dicts.append({
                "id": str(ev.id),
                "kind": ev.evidence_kind,
                "relation": ev.relation,
                "hash": ev.identity_hash,
                "scope": ev.scope,
                "source_id": str(ev.source_id) if ev.source_id else None,
            })

        # Compute assessment via rules engine
        assessment = self.rules.assess_claim(
            claim_statement=claim.statement,
            evidence=evidence_dicts,
            claim_type=claim.claim_type,
        )

        # Persist
        orm_assessment = ORMClaimAssessment(
            claim_id=claim_id,
            effective_grade=assessment["grade"].value,
            epistemic_status=assessment.get("status"),
            rules_hash=assessment.get("rules_hash", ""),
            evidence_set_hash=assessment.get("evidence_set_hash", ""),
            confidence=assessment.get("confidence"),
            valid=True,
            created_in_session=session_id,
        )
        self.session.add(orm_assessment)

        # Update head
        head = ORMClaimAssessmentHead(
            claim_id=claim_id,
            config_snapshot_id=uuid.uuid4(),
            assessment_state="current",
            current_assessment_id=orm_assessment.id,
            epistemic_status=assessment.get("status"),
        )
        self.session.add(head)
        await self.session.flush()

        return orm_assessment

    async def assess_all_pending(self) -> list[ORMClaimAssessment]:
        """Assess all claims that don't have a current head yet."""
        from sqlalchemy import select

        result = await self.session.execute(select(ORMClaim))
        claims = result.scalars().all()
        assessments = []
        for claim in claims:
            # Check if head exists
            head_result = await self.session.execute(
                select(ORMClaimAssessmentHead).where(
                    ORMClaimAssessmentHead.claim_id == claim.id
                )
            )
            if head_result.scalar_one_or_none() is None:
                assessment = await self.assess_claim(claim.id)
                assessments.append(assessment)
        return assessments
