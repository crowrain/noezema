"""Tests for RulesEngine.assess_from_dicts() — assessment from raw dicts."""

from packages.domain.models.enums import EffectiveGrade, EpistemicStatus, ClaimType
from packages.memory.rules_engine import RulesEngine


def test_assess_from_dicts_empty_evidence():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Nothing to see here",
        claim_type=ClaimType.EXTERNAL_FACT.value,
        evidence_dicts=[],
    )
    assert result["grade"] == EffectiveGrade.E0
    assert result["status"] == EpistemicStatus.HYPOTHESIS
    assert result["confidence"] == 0.0


def test_assess_from_dicts_single_support():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Something supported",
        claim_type=ClaimType.EXTERNAL_FACT.value,
        evidence_dicts=[
            {"hash": "abc123", "kind": "source_assertion", "relation": "supports"},
        ],
    )
    assert result["grade"] == EffectiveGrade.E2
    assert result["status"] == EpistemicStatus.SUPPORTED
    assert result["confidence"] == 0.6


def test_assess_from_dicts_multiple_supports_same_kind():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Two same-kind supports",
        claim_type=ClaimType.EXTERNAL_FACT.value,
        evidence_dicts=[
            {"hash": "aaa", "kind": "source_assertion", "relation": "supports"},
            {"hash": "bbb", "kind": "source_assertion", "relation": "supports"},
        ],
    )
    # Two distinct hashes but one kind → E2
    assert result["grade"] == EffectiveGrade.E2


def test_assess_from_dicts_two_kinds_two_sources():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Corroborated claim",
        claim_type=ClaimType.EXTERNAL_FACT.value,
        evidence_dicts=[
            {"hash": "h1", "kind": "source_assertion", "relation": "supports"},
            {"hash": "h2", "kind": "computation", "relation": "supports"},
        ],
    )
    # 2 distinct hashes + 2 kinds → E3
    assert result["grade"] == EffectiveGrade.E3
    assert result["status"] == EpistemicStatus.SUPPORTED
    assert result["confidence"] == 0.85


def test_assess_from_dicts_with_counters():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Disputed claim",
        claim_type=ClaimType.EXTERNAL_FACT.value,
        evidence_dicts=[
            {"hash": "h1", "kind": "source_assertion", "relation": "supports"},
            {"hash": "h2", "kind": "computation", "relation": "supports"},
            {"hash": "h3", "kind": "formal_check", "relation": "counters"},
        ],
    )
    assert result["grade"] == EffectiveGrade.E3  # grade not lowered by counters
    assert result["status"] == EpistemicStatus.DISPUTED  # but status reflects dispute


def test_assess_from_dicts_unknown_claim_type_defaults():
    result = RulesEngine.assess_from_dicts(
        claim_statement="Unknown type",
        claim_type="totally_fake_type",  # not a valid ClaimType
        evidence_dicts=[
            {"hash": "x", "kind": "source_assertion", "relation": "supports"},
        ],
    )
    # Should default to EXTERNAL_FACT behavior
    assert result["grade"] in (EffectiveGrade.E2, EffectiveGrade.E0)


def test_assess_from_dicts_hashes_deterministic():
    ev_dicts = [
        {"hash": "h1", "kind": "a", "relation": "supports"},
        {"hash": "h2", "kind": "b", "relation": "supports"},
    ]
    r1 = RulesEngine.assess_from_dicts("claim", "external_fact", ev_dicts)
    r2 = RulesEngine.assess_from_dicts("claim", "external_fact", ev_dicts)
    assert r1["evidence_set_hash"] == r2["evidence_set_hash"]
    assert r1["rules_hash"] == r2["rules_hash"]
