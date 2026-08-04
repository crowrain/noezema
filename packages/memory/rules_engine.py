"""Deterministic grade computation from evidence set (§3.7, §6.4).

NOT the LLM. Not the verifier. Grade = f(evidence_set, claim_type_rules, scope).
"""

import hashlib
import json

from packages.domain.models.enums import (
    EffectiveGrade, EpistemicStatus, EvidenceRelation, ClaimType,
)
from packages.domain.models.claim import Claim, Evidence, ClaimAssessment


class RulesEngine:
    CLAIM_TYPE_RULES = {
        ClaimType.LOCAL_OBSERVATION: {"min_grade": EffectiveGrade.E2, "min_evidence": 1},
        ClaimType.COMPUTED_RESULT: {"min_grade": EffectiveGrade.E2, "min_evidence": 1},
        ClaimType.FORMAL_THEOREM: {"min_grade": EffectiveGrade.E4, "min_evidence": 1},
        ClaimType.EMPIRICAL_CONJECTURE: {"min_grade": EffectiveGrade.E3, "min_evidence": 2},
        ClaimType.PROCEDURAL: {"min_grade": EffectiveGrade.E3, "min_evidence": 2},
        ClaimType.EXTERNAL_FACT: {"min_grade": EffectiveGrade.E3, "min_evidence": 2},
        ClaimType.TEMPORAL_FACT: {"min_grade": EffectiveGrade.E3, "min_evidence": 2},
        ClaimType.SELF_MODEL: {"min_grade": EffectiveGrade.E2, "min_evidence": 1},
    }

    @classmethod
    def assess(cls, claim: Claim, evidence: list[Evidence]) -> ClaimAssessment:
        supports = [e for e in evidence if e.relation == EvidenceRelation.SUPPORTS]
        counters = [e for e in evidence if e.relation == EvidenceRelation.COUNTERS]

        evidence_set_hash = cls._compute_evidence_hash(evidence)
        grade = cls._compute_grade(supports, counters, claim.claim_type)
        epistemic = cls._compute_epistemic_status(grade, counters)
        confidence = cls._compute_confidence(grade)

        return ClaimAssessment(
            claim_id=claim.id,
            effective_grade=grade,
            epistemic_status=epistemic,
            rules_version=1,
            rules_hash=cls._rules_hash(),
            evidence_set_hash=evidence_set_hash,
            confidence=confidence,
            created_in_session=claim.created_in_session,
        )

    @classmethod
    def _compute_grade(
        cls, supports: list[Evidence], counters: list[Evidence], claim_type: ClaimType
    ) -> EffectiveGrade:
        if not supports:
            return EffectiveGrade.E0

        distinct = {e.identity_hash for e in supports}
        kinds = {e.evidence_kind for e in supports}

        if len(distinct) >= 2 and len(kinds) >= 2:
            grade = EffectiveGrade.E3
        elif len(distinct) >= 1:
            grade = EffectiveGrade.E2
        else:
            grade = EffectiveGrade.E0

        # Counters don't lower grade — they affect epistemic status
        return grade

    @classmethod
    def _compute_epistemic_status(
        cls, grade: EffectiveGrade, counters: list[Evidence]
    ) -> EpistemicStatus:
        if not counters and grade.level >= 2:
            return EpistemicStatus.SUPPORTED
        elif counters and grade.level >= 2:
            return EpistemicStatus.DISPUTED
        elif grade.level >= 2:
            return EpistemicStatus.SUPPORTED
        else:
            return EpistemicStatus.HYPOTHESIS

    @classmethod
    def _compute_confidence(cls, grade: EffectiveGrade) -> float:
        return {
            EffectiveGrade.E0: 0.0,
            EffectiveGrade.E1: 0.3,
            EffectiveGrade.E2: 0.6,
            EffectiveGrade.E3: 0.85,
            EffectiveGrade.E4: 0.99,
        }.get(grade, 0.0)

    @classmethod
    def _compute_evidence_hash(cls, evidence: list[Evidence]) -> str:
        items = sorted(
            [(e.identity_hash, e.evidence_kind.value, e.relation.value) for e in evidence]
        )
        return hashlib.sha256(json.dumps(items).encode()).hexdigest()

    @classmethod
    def _rules_hash(cls) -> str:
        return hashlib.sha256(
            json.dumps(cls.CLAIM_TYPE_RULES, default=str, sort_keys=True).encode()
        ).hexdigest()
