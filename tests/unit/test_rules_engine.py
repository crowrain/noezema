"""Unit: rules engine v2 — the ONLY grade/confidence producer (T3.3, T3.4,
T7.17, §3.7, §8.7). Deterministic, versioned, counterevidence-aware.

``rules-v2`` (T7.17): the scope-coverage predicate is host-derived
(``packages.memory.scope``) — canonical (host-scope-v1) scopes are
checked over the host-derived dimensions; legacy (model free-form)
scopes keep the original key-by-key predicate."""

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
from packages.memory.scope import (
    derive_claim_scope,
    derive_evidence_scope,
    legacy_scope_covers,
)

#: the frozen external_fact rule (§8.7): E3 from >=2 source_assertion
#: in >=2 independent groups, requires_scope
EXTERNAL_FACT_RULE = ClaimTypeRule.from_payload(
    "external_fact",
    {
        "min_grade_for_supported": "E3",
        "allowed_kinds": ["source_assertion", "quote_integrity"],
        "min_support_evidence": 2,
        "min_independence_groups": 2,
        "requires_scope": True,
        "volatility": "configurable",
    },
)

QUESTION_TWO_SOURCES = (
    "По состоянию на 15 апреля 2026 года: сколько государств-членов "
    "входило в ООН? Ответь строго по этим двум источникам: "
    "https://un.org/en/about-us и "
    "https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН"
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


def _ev(
    kind: str = "computation",
    relation: str = "supports",
    group: str = "g0",
    resolved: bool = False,
) -> EvaluatedEvidence:
    return EvaluatedEvidence(
        identity_hash=f"hash-{kind}-{relation}-{group}-{resolved}",
        kind=kind,
        relation=relation,
        scope={},
        independence_group=group,
        resolved=resolved,
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


def test_resolved_counterevidence_does_not_cap():
    """§8.7.4: a counter with a VALID resolution does not count against
    the claim — supported, with the audit-visible reason."""
    r = evaluate(
        "computed_result",
        _rule(),
        {},
        [_ev(), _ev(relation="counters", resolved=True)],
        has_as_of=False,
    )
    assert r.epistemic_status is EpistemicStatus.SUPPORTED
    assert r.grade is EffectiveGrade.E2
    assert "counterevidence_resolved" in r.reasons


def test_resolved_counter_only_is_not_refuted():
    """All counters resolved + no support: nothing is refuted, nothing
    is supported — hypothesis (no_evidence), not refuted."""
    r = evaluate(
        "computed_result", _rule(), {}, [_ev(relation="counters", resolved=True)], has_as_of=False
    )
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert "no_evidence" in r.reasons


def test_one_unresolved_counter_among_resolved_still_disputes():
    r = evaluate(
        "computed_result",
        _rule(),
        {},
        [
            _ev(),
            _ev(relation="counters", resolved=True),
            _ev(relation="counters", group="g9"),
        ],
        has_as_of=False,
    )
    assert r.epistemic_status is EpistemicStatus.DISPUTED
    assert r.grade is EffectiveGrade.E1
    assert "counterevidence_unresolved" in r.reasons


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


# ── T7.17: host-derived (canonical) scope coverage ─────────────────────


def _canonical_evidence(group: str, *, source_domain: str, observed_at: datetime) -> EvaluatedEvidence:
    """A source_assertion support with a host-derived (host-scope-v1)
    evidence scope, as the trusted host writes it from provenance."""
    return EvaluatedEvidence(
        f"h-{group}",
        "source_assertion",
        "supports",
        scope=derive_evidence_scope(source_domain=source_domain, observed_at=observed_at),
        independence_group=group,
    )


def test_canonical_scope_same_subject_and_date_reaches_E3():
    """T7.17 required test 1: the claim scope (host-derived from the
    question) and the evidence scopes (host-derived from provenance)
    describe the SAME subject and date — the path to E3 is open,
    whatever free-form keys the model used in its (ignored) proposal."""
    claim_scope = derive_claim_scope(question=QUESTION_TWO_SOURCES, as_of=None)
    # retrieved after the reference date (2026-04-15), the two sources
    # named in the question → two independent groups, scope covered
    evs = [
        _canonical_evidence("g0", source_domain="un.org", observed_at=datetime(2026, 9, 17, 13, 0, tzinfo=UTC)),
        _canonical_evidence("g1", source_domain="wikipedia.org", observed_at=datetime(2026, 9, 17, 13, 5, tzinfo=UTC)),
    ]
    r = evaluate("external_fact", EXTERNAL_FACT_RULE, claim_scope, evs, has_as_of=True)
    assert r.epistemic_status is EpistemicStatus.SUPPORTED
    assert r.grade is EffectiveGrade.E3
    assert "requirements_met" in r.reasons


def test_canonical_scope_different_subject_stays_E1():
    """T7.17 required test 2 (fail-closed): one supporting source is
    NOT among the question's named sources (a different subject) —
    the scope does not cover, the grade is NOT lifted: E1 hypothesis
    with the scope_not_covered reason."""
    claim_scope = derive_claim_scope(question=QUESTION_TWO_SOURCES, as_of=None)
    evs = [
        _canonical_evidence("g0", source_domain="un.org", observed_at=datetime(2026, 9, 17, 13, 0, tzinfo=UTC)),
        _canonical_evidence("g1", source_domain="other.example", observed_at=datetime(2026, 9, 17, 13, 5, tzinfo=UTC)),
    ]
    r = evaluate("external_fact", EXTERNAL_FACT_RULE, claim_scope, evs, has_as_of=True)
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert r.grade is EffectiveGrade.E1
    assert "scope_not_covered" in r.reasons


def test_canonical_scope_retrieved_before_as_of_stays_E1():
    """T7.17 required test 2 (fail-closed): a source retrieved BEFORE
    the claim's reference date cannot speak about that date — E1,
    scope_not_covered, even from the two independent named sources."""
    claim_scope = derive_claim_scope(question=QUESTION_TWO_SOURCES, as_of=None)  # 2026-04-15
    evs = [
        _canonical_evidence("g0", source_domain="un.org", observed_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC)),
        _canonical_evidence("g1", source_domain="wikipedia.org", observed_at=datetime(2026, 4, 2, 12, 0, tzinfo=UTC)),
    ]
    r = evaluate("external_fact", EXTERNAL_FACT_RULE, claim_scope, evs, has_as_of=True)
    assert r.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert r.grade is EffectiveGrade.E1
    assert "scope_not_covered" in r.reasons


def test_canonical_scope_model_freeform_keys_do_not_block():
    """The model's free-form proposal (different key spellings for the
    same subject/date) is NOT the scope that is checked: with the
    host-derived scopes the same evaluation reaches E3 — the proposal
    dict does not enter the predicate at all."""
    claim_scope = derive_claim_scope(
        question=QUESTION_TWO_SOURCES,
        as_of=datetime(2026, 4, 15, tzinfo=UTC),
    )
    assert claim_scope["as_of"] == "2026-04-15"
    # the model proposed {"регион": "ООН", "на дату": "15.04.2026"} for
    # the claim and {"область": "ООН", "as_of": "2026-04-15"} for the
    # evidence — the legacy key-by-key predicate would have rejected
    # that pair (the T7.15 defect); the canonical one does not care
    model_claim = {"регион": "ООН", "на дату": "15.04.2026"}
    model_evidence = {"область": "ООН", "as_of": "2026-04-15"}
    assert not legacy_scope_covers(model_evidence, model_claim)  # the old defect
    evs = [
        _canonical_evidence("g0", source_domain="un.org", observed_at=datetime(2026, 9, 17, tzinfo=UTC)),
        _canonical_evidence("g1", source_domain="wikipedia.org", observed_at=datetime(2026, 9, 17, tzinfo=UTC)),
    ]
    r = evaluate("external_fact", EXTERNAL_FACT_RULE, claim_scope, evs, has_as_of=True)
    assert r.epistemic_status is EpistemicStatus.SUPPORTED
    assert r.grade is EffectiveGrade.E3


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
