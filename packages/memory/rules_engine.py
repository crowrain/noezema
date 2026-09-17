"""Rules engine v2 — the ONLY producer of grade and confidence (T3.3, T3.4,
T7.17, §3.7, §8.7).

A rule is an EXECUTABLE structure over the evidence set (not a text hint):
allowed kinds, minimum support count, minimum independence groups, scope
coverage, as_of requirement and volatility. The function is
deterministic and versioned (``rules-v2``); the LLM never proposes a
number, and an operator attestation is not an input — it cannot raise
the grade.

``rules-v2`` (T7.17): the scope-coverage predicate is host-derived
(``packages.memory.scope``) — the scope checked is derived by the
trusted host from the question and the evidence provenance, not the
model's free-form dict; legacy (model-derived) scopes keep the original
key-by-key predicate. Everything else (kinds, counts, independence,
as_of, counterevidence, confidence) is unchanged from ``rules-v1``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import EffectiveGrade, EpistemicStatus
from packages.memory.scope import scope_covers

RULES_ENGINE_VERSION = "rules-v2"

VOLATILITY_REVERIFY_DAYS = {"static": 90, "configurable": 30, "temporal": 30}

GRADE_CONFIDENCE_BASE = {
    EffectiveGrade.E0: 0.10,
    EffectiveGrade.E1: 0.30,
    EffectiveGrade.E2: 0.55,
    EffectiveGrade.E3: 0.75,
    EffectiveGrade.E4: 0.95,
}


#: §8.7.3 relation strength for ``required_independence`` (the snapshot
#: relations from packages.memory.env_independence).
REQUIRED_RELATIONS = ("repeatability", "reproducibility", "independent_replication")
_RELATION_RANK = {
    "none": 0,
    "untracked": 1,
    "variation": 1,  # different groups, same method: no independence either
    "repeatability": 2,
    "reproducibility": 3,
    "independent_replication": 4,
}


@dataclass(frozen=True)
class ClaimTypeRule:
    """One executable claim-type rule (from config_snapshots.claim_type_rules)."""

    min_grade_for_supported: EffectiveGrade
    allowed_kinds: frozenset[str]
    min_support_evidence: int
    min_independence_groups: int
    requires_scope: bool
    requires_as_of: bool
    volatility: str
    # §8.7.3 (T4.6): the required RELATION between independent
    # environments — scope-dependent. Group counts alone are not
    # enough: two runs of the same implementation are one group, and
    # only ``independent_replication`` may lift a grade to E3.
    required_independence: str | None = None

    @classmethod
    def from_payload(cls, claim_type: str, payload: JsonDict) -> ClaimTypeRule:
        if not payload:
            raise ValueError(f"no rule for claim_type {claim_type!r}")
        required = payload.get("required_independence")
        if required is not None and required not in REQUIRED_RELATIONS:
            raise ValueError(
                f"claim_type {claim_type!r}: unknown required_independence {required!r}"
            )
        return cls(
            min_grade_for_supported=EffectiveGrade(payload["min_grade_for_supported"]),
            allowed_kinds=frozenset(payload.get("allowed_kinds", [])),
            min_support_evidence=int(payload.get("min_support_evidence", 1)),
            min_independence_groups=int(payload.get("min_independence_groups", 1)),
            requires_scope=bool(payload.get("requires_scope", False)),
            requires_as_of=bool(payload.get("requires_as_of", False)),
            volatility=str(payload.get("volatility", "static")),
            required_independence=required,
        )

    @property
    def reverify_days(self) -> int:
        return VOLATILITY_REVERIFY_DAYS.get(self.volatility, 90)


class RuleValidationError(ValueError):
    """The rule cannot evaluate this evidence set (role/relation
    inconsistency, unknown claim type, ...)."""


@dataclass(frozen=True)
class EvaluatedEvidence:
    """One evidence row as the rules engine sees it."""

    identity_hash: str
    kind: str
    relation: str  # supports | counters
    scope: JsonDict = field(default_factory=dict)
    independence_group: str = "unknown"
    # §8.7.3 (T4.6): the environment-independence relation this evidence
    # has with the rest of the set (env_independence snapshot member);
    # "none" when the evidence has no tracked environment
    env_relation: str = "none"
    # §8.7.4 (T4.8): the counters evidence has a VALID
    # counterevidence_resolution — it does not cap the grade
    resolved: bool = False


def _relation_met(support: list[EvaluatedEvidence], required: str) -> bool:
    """§8.7.3: the required relation must hold BETWEEN distinct
    environments — ``independent_replication`` needs two distinct groups
    whose members replicate independently; the weaker relations need two
    members that actually have (at least) that relation."""
    if required == "independent_replication":
        return len(
            {
                e.independence_group
                for e in support
                if e.env_relation == "independent_replication"
            }
        ) >= 2
    threshold = _RELATION_RANK[required]
    return sum(1 for e in support if _RELATION_RANK.get(e.env_relation, 0) >= threshold) >= 2


@dataclass(frozen=True)
class AssessmentResult:
    grade: EffectiveGrade
    epistemic_status: EpistemicStatus
    confidence: float
    reverify_after_days: int
    reasons: tuple[str, ...]


def evaluate(
    claim_type: str,
    rule: ClaimTypeRule,
    claim_scope: JsonDict,
    evidences: list[EvaluatedEvidence],
    *,
    has_as_of: bool,
) -> AssessmentResult:
    """Deterministic assessment of an evidence set against one rule.

    Raises RuleValidationError on role/relation inconsistency (a kind not
    in the allowed set used as support) so the caller rejects the staging
    op instead of silently mis-weighing it.
    """
    support = [e for e in evidences if e.relation == "supports"]
    # §8.7.4: only UNRESOLVED counterevidence counts against the claim —
    # a counter with a valid resolution (verifiable basis) is out
    # (counterevidence_unresolved == false)
    counter = [e for e in evidences if e.relation == "counters" and not e.resolved]
    resolved_counters = sum(1 for e in evidences if e.relation == "counters" and e.resolved)

    # role/relation inconsistency: a support evidence of a kind the rule
    # does not allow is rejected, not silently dropped (§14.3)
    for e in support:
        if e.kind not in rule.allowed_kinds:
            raise RuleValidationError(
                f"support evidence kind {e.kind!r} not allowed for {claim_type}"
            )

    groups = sorted({e.independence_group for e in support})
    # T7.17: the coverage predicate is keyed on the claim scope's
    # origin (host-derived canonical vs legacy model dict) — see
    # packages.memory.scope; a scope that truly does not cover still
    # fails closed (scope_not_covered, no grade lift)
    scope_ok = (not rule.requires_scope) or (bool(claim_scope) and all(
        scope_covers(e.scope, claim_scope) for e in support
    )) if support else (not rule.requires_scope or not claim_scope)
    as_of_ok = (not rule.requires_as_of) or has_as_of

    reverify = rule.reverify_days
    if not support and not counter:
        status = EpistemicStatus.DEFERRED if (rule.requires_as_of and not has_as_of) else EpistemicStatus.HYPOTHESIS
        return AssessmentResult(
            grade=EffectiveGrade.E0,
            epistemic_status=status,
            confidence=0.05,
            reverify_after_days=reverify,
            reasons=("no_evidence",),
        )

    if not support:
        # counter only: the claim is refuted, nothing is supported
        return AssessmentResult(
            grade=EffectiveGrade.E0,
            epistemic_status=EpistemicStatus.REFUTED,
            confidence=0.5,
            reverify_after_days=reverify,
            reasons=("counterevidence_only",),
        )

    relation_ok = (rule.required_independence is None) or _relation_met(
        support, rule.required_independence
    )
    meets = (
        len(support) >= rule.min_support_evidence
        and len(groups) >= rule.min_independence_groups
        and relation_ok
        and scope_ok
        and as_of_ok
    )

    if counter:
        # unresolved counterevidence: disputed, capped at E1 (§3.7)
        grade = EffectiveGrade.E1
        status = EpistemicStatus.DISPUTED
        reasons: tuple[str, ...] = ("counterevidence_unresolved",)
    elif meets:
        grade = rule.min_grade_for_supported
        # extra independent groups lift the grade by one step (never past E4)
        if len(groups) >= 2 * rule.min_independence_groups and grade.level < 4:
            grade = EffectiveGrade(grade.value[0] + str(grade.level + 1))
        status = EpistemicStatus.SUPPORTED
        reasons = ("requirements_met",)
        if resolved_counters:
            reasons = (*reasons, "counterevidence_resolved")
    else:
        # some support, requirements not met: hypothesis (integrity checked)
        grade = EffectiveGrade.E1
        status = EpistemicStatus.HYPOTHESIS
        if len(support) < rule.min_support_evidence:
            reasons = ("insufficient_evidence",)
        elif len(groups) < rule.min_independence_groups:
            reasons = ("insufficient_independence",)
        elif not relation_ok:
            # §8.7.3: the groups exist but the required relation between
            # environments is missing (e.g. the same implementation
            # repeated — repeatability, not independent replication)
            reasons = (f"independence_{rule.required_independence}_not_met",)
        elif not scope_ok:
            reasons = ("scope_not_covered",)
        else:
            reasons = ("as_of_missing",)

    confidence = GRADE_CONFIDENCE_BASE[grade]
    if counter:
        confidence *= 0.6
    if rule.min_independence_groups:
        confidence *= min(1.0, len(groups) / rule.min_independence_groups)

    return AssessmentResult(
        grade=grade,
        epistemic_status=status,
        confidence=round(min(1.0, max(0.0, confidence)), 4),
        reverify_after_days=reverify,
        reasons=reasons,
    )


def reverify_after(result: AssessmentResult, as_of: datetime | None, now: datetime) -> datetime:
    """reverify_after is derived from claim type/volatility/as_of (T3.7).
    Only the freshness status may change with time — never the grade."""
    base = as_of if (as_of is not None and result.reverify_after_days == 30) else now
    return base + timedelta(days=result.reverify_after_days)
