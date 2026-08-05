"""Tests for deterministic rules engine E0-E4."""

import uuid

from packages.domain.models.claim import Claim, Evidence
from packages.domain.models.enums import (
    ClaimType, EvidenceKind, EvidenceRelation, EffectiveGrade, EpistemicStatus,
)
from packages.memory.rules_engine import RulesEngine


def test_e0_no_evidence():
    claim = Claim(statement="2+2=4", claim_type=ClaimType.EXTERNAL_FACT)
    assessment = RulesEngine.assess(claim, [])
    assert assessment.effective_grade == EffectiveGrade.E0
    assert assessment.epistemic_status == EpistemicStatus.HYPOTHESIS
    assert assessment.confidence == 0.0


def test_e2_single_evidence():
    claim = Claim(
        statement="Python 3.11 exists",
        claim_type=ClaimType.EXTERNAL_FACT,
        created_in_session=uuid.uuid4(),
    )
    evidence = [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash="hash1",
        )
    ]
    assessment = RulesEngine.assess(claim, evidence)
    assert assessment.effective_grade == EffectiveGrade.E2
    assert assessment.confidence == 0.6


def test_e3_multiple_kinds():
    claim = Claim(
        statement="2+2=4",
        claim_type=ClaimType.COMPUTED_RESULT,
        created_in_session=uuid.uuid4(),
    )
    evidence = [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.COMPUTATION,
            identity_hash="comp1",
        ),
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.FORMAL_CHECK,
            identity_hash="formal1",
        ),
    ]
    assessment = RulesEngine.assess(claim, evidence)
    assert assessment.effective_grade == EffectiveGrade.E3
    assert assessment.confidence == 0.85


def test_disputed_with_counters():
    claim = Claim(
        statement="X is true",
        claim_type=ClaimType.EXTERNAL_FACT,
        created_in_session=uuid.uuid4(),
    )
    evidence = [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.SOURCE_ASSERTION,
            identity_hash="s1",
        ),
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash="s2",
        ),
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.COUNTERS,
            evidence_kind=EvidenceKind.SOURCE_ASSERTION,
            identity_hash="c1",
        ),
    ]
    assessment = RulesEngine.assess(claim, evidence)
    assert assessment.epistemic_status == EpistemicStatus.DISPUTED


def test_deterministic_hash():
    """Same evidence → same hash."""
    e1 = [
        Evidence(
            claim_id=uuid.uuid4(),
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.COMPUTATION,
            identity_hash="h1",
        )
    ]
    e2 = [
        Evidence(
            claim_id=uuid.uuid4(),
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.COMPUTATION,
            identity_hash="h1",
        )
    ]
    assert RulesEngine._compute_evidence_hash(e1) == RulesEngine._compute_evidence_hash(e2)
