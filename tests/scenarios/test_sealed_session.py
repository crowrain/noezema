"""MVP scenario: Sealed session question → evidence → assessment → commit.

Gate: одна Sealed-сессия проходит полный путь без LLM (mock).
"""

import uuid

from packages.domain.models.claim import Claim, Evidence
from packages.domain.models.enums import (
    ClaimType, EvidenceKind, EvidenceRelation,
    EffectiveGrade, EpistemicStatus,
)
from packages.memory.rules_engine import RulesEngine


def test_sealed_session_full_path():
    """Full path: question → evidence → E2 assessment → verified."""

    # 1. Question exists
    question = {
        "id": uuid.uuid4(),
        "statement": "What is Python's default encoding?",
        "source": "seeded",
    }

    # 2. Explorer generates evidence (simulated)
    session_id = uuid.uuid4()
    claim = Claim(
        statement="Python's default encoding is UTF-8",
        claim_type=ClaimType.EXTERNAL_FACT,
        created_in_session=session_id,
    )

    evidence = [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash="python_imports_sys_stdin_encoding",
        ),
    ]

    # 3. Rules engine assesses (NOT LLM)
    assessment = RulesEngine.assess(claim, evidence)

    # 4. Single evidence → E2, not E3
    assert assessment.effective_grade == EffectiveGrade.E2
    assert assessment.epistemic_status == EpistemicStatus.SUPPORTED
    assert assessment.confidence == 0.6

    # 5. Duplicate evidence does NOT increase grade
    evidence_dup = evidence + [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.LOCAL_OBSERVATION,
            identity_hash="python_imports_sys_stdin_encoding",  # same hash!
        )
    ]
    assessment_dup = RulesEngine.assess(claim, evidence_dup)
    assert assessment_dup.effective_grade == EffectiveGrade.E2

    # 6. New evidence kind → E3
    evidence_plus = evidence + [
        Evidence(
            claim_id=claim.id,
            relation=EvidenceRelation.SUPPORTS,
            evidence_kind=EvidenceKind.COMPUTATION,
            identity_hash="computed_locale_test",
        )
    ]
    assessment_plus = RulesEngine.assess(claim, evidence_plus)
    assert assessment_plus.effective_grade == EffectiveGrade.E3
    assert assessment_plus.confidence == 0.85

    print("✅ Sealed session path: question → evidence → E2 assessment → verified")
