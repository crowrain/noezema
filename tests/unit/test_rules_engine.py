"""Unit: rules engine v1 — the ONLY grade/confidence producer (T3.3, T3.4,
§3.7, §8.7). Deterministic, versioned, counterevidence-aware."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.domain.models.enums import EffectiveGrade, EpistemicStatus
from packages.memory.evidence import rules_hash
from packages.memory.rules_engine import (
    ClaimTypeRule,
    EvaluatedEvidence,
    RuleValidationError,
    evaluate,
    reverify_after,
)


def _rule(**overrides) -> ClaimTypeRule:
    base = {
        "min_grade_for_supported": "E2",
        "allowed_kinds": ["computation"],
        "min_support_evidence": 1,
        "min_independence_groups": 1,
        "requires_scope": False,
        "requires_as_of": False,
        "volatility": "static",
    }
    base.update(overrides)
    return ClaimTypeRule.from_payload("computed_result", base)


def _ev(kind: str = "computation", relation: str = "supports", group: str = "g0") -> EvaluatedEvidence:
    return EvaluatedEvidence(
        identity_hash=f"hash-{kind}-{relation}-{group}",
        kind=kind,
        relation=relation,
        scope={},
        independence_group=group,
    )


def test_no_evidence_is_hypothesis_E0():
    r = evaluate("computed_result", _rule(), {}, [], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert r.grade is EffectiveGrade.E0
    assert 0.0 < r.confidence < 0.2


def test_counter_only_is_refuted():
    r = evaluate("computed_result", _rule(), {}, [_ev(relation="counters")], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.REFUTED
    assert r.grade is EffectiveGrade.E0


def test_single_support_meets_rule_is_supported_E2():
    r = evaluate("computed_result", _rule(), {}, [_ev()], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.SUPPORTED
    assert r.grade is EffectiveGrade.E2
    assert 0.5 <= r.confidence <= 0.6


def test_counterevidence_disputes_and_caps_E1():
    r = evaluate("computed_result", _rule(), {}, [_ev(), _ev(relation="counters")], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.DISPUTED
    assert r.grade is EffectiveGrade.E1
    # disputed confidence is damped below a clean E1
    assert r.confidence < 0.3


def test_insufficient_independence_stays_hypothesis():
    rule = _rule(min_independence_groups=2)
    r = evaluate("computed_result", rule, {}, [_ev(group="g0")], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert r.grade is EffectiveGrade.E1
    assert "insufficient_independence" in r.reasons


def test_extra_independent_groups_lift_grade():
    rule = _rule(min_independence_groups=1)
    r = evaluate(
        "computed_result", rule, {}, [_ev(group="g0"), _ev(group="g1")], has_as_of=False
    )
    assert r.epistemic_status is EpistemicStatus.SUPPORTED
    assert r.grade is EffectiveGrade.E3  # lifted one step from E2


def test_disallowed_support_kind_is_rejected():
    with pytest.raises(RuleValidationError):
        evaluate("computed_result", _rule(), {}, [_ev(kind="local_observation")], has_as_of=False)


def test_scope_not_covered_stays_hypothesis():
    rule = _rule(requires_scope=True)
    r = evaluate(
        "computed_result", rule, {"expr": "6*7"}, [_ev()], has_as_of=False  # evidence scope {}
    )
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert "scope_not_covered" in r.reasons


def test_scope_covered_is_supported():
    rule = _rule(requires_scope=True)
    r = evaluate(
        "computed_result",
        rule,
        {"expr": "6*7"},
        [EvaluatedEvidence("h", "computation", "supports", scope={"expr": "6*7"}, independence_group="g0")],
        has_as_of=False,
    )
    assert r.epistemic_status is EpistemicStatus.SUPPORTED


def test_requires_as_of_missing_is_deferred():
    rule = _rule(requires_as_of=True)
    r = evaluate("temporal_fact", rule, {}, [], has_as_of=False)
    assert r.epistemic_status is EpistemicStatus.DEFERRED


def test_confidence_is_bounded():
    for kind in ("supports", "counters"):
        for n in range(5):
            r = evaluate(
                "computed_result",
                _rule(),
                {},
                [_ev(relation=kind, group=f"g{i}") for i in range(n)],
                has_as_of=False,
            )
            assert 0.0 <= r.confidence <= 1.0


def test_rules_hash_is_stable_and_order_insensitive():
    a = rules_hash({"x": 1, "y": 2})
    b = rules_hash({"y": 2, "x": 1})
    assert a == b
    assert a != rules_hash({"x": 1, "y": 3})


def test_reverify_after_respects_volatility():
    rule_static = _rule(volatility="static")
    rule_config = _rule(volatility="configurable")
    now = datetime(2026, 9, 14, tzinfo=UTC)
    r_static = evaluate("computed_result", rule_static, {}, [_ev()], has_as_of=False)
    r_config = evaluate("computed_result", rule_config, {}, [_ev()], has_as_of=False)
    assert reverify_after(r_static, None, now).day == 13  # 90 days
    assert (reverify_after(r_static, None, now) - reverify_after(r_config, None, now)).days == 60


def test_attestation_is_not_an_input():
    """An operator attestation cannot raise the grade: the engine has no
    attestation input at all — a claim with no evidence stays E0."""
    r = evaluate("computed_result", _rule(), {}, [], has_as_of=False)
    assert r.grade is EffectiveGrade.E0
