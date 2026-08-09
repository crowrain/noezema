"""Pure deterministic assessment of staged evidence against versioned rules."""

from __future__ import annotations

from packages.domain import (
    ClaimAssessment,
    ClaimAssessmentInput,
    ClaimType,
    ClaimTypeRule,
    ClaimTypeRulesSnapshot,
    ConfidencePolicy,
    EpistemicStatus,
    EvidenceGrade,
    EvidenceKind,
    EvidenceUse,
    RuleEvidenceFacts,
    ScopeDimension,
    canonical_json_sha256,
)

_GRADE_INDEX = {
    EvidenceGrade.E0: 0,
    EvidenceGrade.E1: 1,
    EvidenceGrade.E2: 2,
    EvidenceGrade.E3: 3,
    EvidenceGrade.E4: 4,
}
_SEMANTIC_COUNTER_KINDS = tuple(
    kind for kind in EvidenceKind if kind is not EvidenceKind.QUOTE_INTEGRITY
)


class AssessmentRuleInputError(ValueError):
    """Evidence facts cannot be evaluated by the selected claim-type rule."""

    code = "assessment_rule_input_error"


def mvp_claim_type_rules() -> ClaimTypeRulesSnapshot:
    """Return the explicit baseline rules intended for the first local MVP."""

    rules = (
        ClaimTypeRule(
            claim_type=ClaimType.LOCAL_OBSERVATION,
            support_kinds=(EvidenceKind.LOCAL_OBSERVATION,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E2,
            minimum_supporting_evidence=1,
            minimum_independence_groups=0,
            required_scope_all=(ScopeDimension.ENVIRONMENT, ScopeDimension.TIME),
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="runtime_state",
            reverify_after_seconds=86_400,
        ),
        ClaimTypeRule(
            claim_type=ClaimType.COMPUTED_RESULT,
            support_kinds=(EvidenceKind.COMPUTATION,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E2,
            minimum_supporting_evidence=1,
            minimum_independence_groups=0,
            required_scope_all=(ScopeDimension.INPUTS, ScopeDimension.ALGORITHM),
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="input_bound",
        ),
        ClaimTypeRule(
            claim_type=ClaimType.FORMAL_THEOREM,
            support_kinds=(EvidenceKind.FORMAL_CHECK,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E4,
            minimum_supporting_evidence=1,
            minimum_independence_groups=0,
            required_scope_all=(ScopeDimension.AXIOMS, ScopeDimension.MODEL),
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="immutable_assumptions",
        ),
        ClaimTypeRule(
            claim_type=ClaimType.EMPIRICAL_CONJECTURE,
            support_kinds=(EvidenceKind.EXPERIMENT_RUN,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E3,
            minimum_supporting_evidence=2,
            minimum_independence_groups=2,
            required_scope_all=(ScopeDimension.CLAIM,),
            require_success=True,
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="environment_sensitive",
            reverify_after_seconds=2_592_000,
        ),
        ClaimTypeRule(
            claim_type=ClaimType.PROCEDURAL,
            support_kinds=(EvidenceKind.EXPERIMENT_RUN,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E3,
            minimum_supporting_evidence=2,
            minimum_independence_groups=2,
            required_scope_all=(ScopeDimension.CLAIM,),
            require_success=True,
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="environment_sensitive",
            reverify_after_seconds=2_592_000,
        ),
        ClaimTypeRule(
            claim_type=ClaimType.EXTERNAL_FACT,
            support_kinds=(EvidenceKind.SOURCE_ASSERTION,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E3,
            minimum_supporting_evidence=2,
            minimum_independence_groups=2,
            required_scope_all=(ScopeDimension.CLAIM,),
            require_integrity=True,
            max_grade_when_required_fields_absent=EvidenceGrade.E1,
            volatility="source_dependent",
            reverify_after_seconds=2_592_000,
        ),
        ClaimTypeRule(
            claim_type=ClaimType.TEMPORAL_FACT,
            support_kinds=(EvidenceKind.SOURCE_ASSERTION,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E3,
            minimum_supporting_evidence=2,
            minimum_independence_groups=2,
            required_scope_all=(ScopeDimension.CLAIM, ScopeDimension.TEMPORAL),
            require_integrity=True,
            require_as_of=True,
            max_grade_when_required_fields_absent=EvidenceGrade.E1,
            volatility="time_sensitive",
            reverify_after_seconds=86_400,
        ),
        ClaimTypeRule(
            claim_type=ClaimType.SELF_MODEL,
            support_kinds=(EvidenceKind.LOCAL_OBSERVATION,),
            counter_kinds=_SEMANTIC_COUNTER_KINDS,
            target_grade=EvidenceGrade.E2,
            minimum_supporting_evidence=1,
            minimum_independence_groups=0,
            required_scope_any=(ScopeDimension.CONFIGURATION, ScopeDimension.IDENTITY_STATE),
            max_grade_when_required_fields_absent=EvidenceGrade.E0,
            volatility="runtime_state",
            reverify_after_seconds=86_400,
        ),
    )
    confidence = ConfidencePolicy(
        by_grade_basis_points=(500, 1_500, 5_500, 8_000, 9_500),
        disputed_cap_basis_points=5_000,
        refuted_basis_points=500,
    )
    return ClaimTypeRulesSnapshot.build(
        version="mvp-claim-type-rules/v1",
        rules=rules,
        confidence=confidence,
    )


def assess_claim(
    assessment_input: ClaimAssessmentInput,
    *,
    rules: ClaimTypeRulesSnapshot,
) -> ClaimAssessment:
    """Evaluate one staged claim without model calls, I/O, clocks, or mutable state."""

    rule = next(item for item in rules.rules if item.claim_type is assessment_input.claim_type)
    _validate_allowed_kinds(assessment_input, rule)

    support = tuple(
        item
        for item in assessment_input.evidence
        if item.proposal.relation is EvidenceUse.SUPPORT and _qualifies_as_support(item, rule)
    )
    counters = tuple(
        item
        for item in assessment_input.evidence
        if item.proposal.relation is EvidenceUse.COUNTER and _qualifies_as_counter(item, rule)
    )

    grade = _effective_grade(assessment_input, rule, support)
    unmet = _unmet_requirements(assessment_input, rule, support)
    if counters:
        counter_groups = {
            item.independence_group for item in counters if item.independence_group is not None
        }
        counter_threshold_met = (
            len(counters) >= rule.minimum_supporting_evidence
            and len(counter_groups) >= rule.minimum_independence_groups
        )
        status = (
            EpistemicStatus.REFUTED
            if not support and counter_threshold_met
            else EpistemicStatus.DISPUTED
        )
    elif _GRADE_INDEX[grade] >= _GRADE_INDEX[rule.target_grade]:
        status = EpistemicStatus.SUPPORTED
    else:
        status = EpistemicStatus.HYPOTHESIS

    confidence = rules.confidence.by_grade_basis_points[_GRADE_INDEX[grade]]
    if status is EpistemicStatus.DISPUTED:
        confidence = min(confidence, rules.confidence.disputed_cap_basis_points)
    elif status is EpistemicStatus.REFUTED:
        confidence = rules.confidence.refuted_basis_points

    evidence_set_sha256 = canonical_json_sha256(
        {
            "schema": "assessment-evidence-set/v1",
            "claim_ref": assessment_input.claim_ref,
            "items": sorted(
                (item.model_dump(mode="json") for item in assessment_input.evidence),
                key=lambda item: (
                    item["proposal"]["evidence_kind"],
                    item["proposal"]["identity_sha256"],
                ),
            ),
        }
    )
    assessed_scope = _common_scope(support)
    support_groups = tuple(
        sorted({item.independence_group for item in support if item.independence_group is not None})
    )
    counter_groups = tuple(
        sorted(
            {item.independence_group for item in counters if item.independence_group is not None}
        )
    )
    return ClaimAssessment(
        claim_ref=assessment_input.claim_ref,
        claim_type=assessment_input.claim_type,
        effective_grade=grade,
        epistemic_status=status,
        confidence_basis_points=confidence,
        rules_version=rules.version,
        rules_sha256=rules.sha256,
        evidence_set_sha256=evidence_set_sha256,
        as_of=assessment_input.as_of,
        assessed_scope=assessed_scope,
        supporting_evidence=tuple(sorted(item.proposal.identity_sha256 for item in support)),
        unresolved_counterevidence=tuple(
            sorted(item.proposal.identity_sha256 for item in counters)
        ),
        support_independence_groups=support_groups,
        counter_independence_groups=counter_groups,
        unmet_requirements=unmet,
    )


def _validate_allowed_kinds(
    assessment_input: ClaimAssessmentInput,
    rule: ClaimTypeRule,
) -> None:
    for item in assessment_input.evidence:
        kind = item.proposal.evidence_kind
        if item.proposal.relation is EvidenceUse.SUPPORT:
            if kind is EvidenceKind.QUOTE_INTEGRITY:
                continue
            if kind not in rule.support_kinds:
                raise AssessmentRuleInputError(
                    f"{kind.value} cannot support {rule.claim_type.value}"
                )
        elif kind not in rule.counter_kinds:
            raise AssessmentRuleInputError(f"{kind.value} cannot counter {rule.claim_type.value}")


def _scope_matches(item: RuleEvidenceFacts, rule: ClaimTypeRule) -> bool:
    covered = set(item.covered_scope)
    return set(rule.required_scope_all).issubset(covered) and (
        not rule.required_scope_any or bool(set(rule.required_scope_any) & covered)
    )


def _qualifies_as_support(item: RuleEvidenceFacts, rule: ClaimTypeRule) -> bool:
    if item.proposal.evidence_kind not in rule.support_kinds:
        return False
    if not _scope_matches(item, rule):
        return False
    if rule.require_integrity and not item.integrity_checked:
        return False
    return not rule.require_success or item.successful is True


def _qualifies_as_counter(item: RuleEvidenceFacts, rule: ClaimTypeRule) -> bool:
    if item.counterevidence_resolved or item.proposal.evidence_kind not in rule.counter_kinds:
        return False
    if not _scope_matches(item, rule):
        return False
    return not (
        item.proposal.evidence_kind is EvidenceKind.SOURCE_ASSERTION and not item.integrity_checked
    )


def _effective_grade(
    assessment_input: ClaimAssessmentInput,
    rule: ClaimTypeRule,
    support: tuple[RuleEvidenceFacts, ...],
) -> EvidenceGrade:
    integrity_only = any(
        item.proposal.relation is EvidenceUse.SUPPORT and item.integrity_checked
        for item in assessment_input.evidence
    )
    grade = EvidenceGrade.E1 if integrity_only else EvidenceGrade.E0
    if not support or (rule.require_as_of and assessment_input.as_of is None):
        return min(
            grade,
            rule.max_grade_when_required_fields_absent,
            key=_GRADE_INDEX.__getitem__,
        )

    if rule.target_grade is EvidenceGrade.E4:
        return EvidenceGrade.E4 if len(support) >= rule.minimum_supporting_evidence else grade

    grade = EvidenceGrade.E2
    groups = {item.independence_group for item in support if item.independence_group is not None}
    if (
        len(support) >= rule.minimum_supporting_evidence
        and len(groups) >= rule.minimum_independence_groups
    ):
        return rule.target_grade
    return grade


def _common_scope(support: tuple[RuleEvidenceFacts, ...]) -> tuple[ScopeDimension, ...]:
    if not support:
        return ()
    common = set(support[0].covered_scope)
    for item in support[1:]:
        common.intersection_update(item.covered_scope)
    return tuple(dimension for dimension in ScopeDimension if dimension in common)


def _unmet_requirements(
    assessment_input: ClaimAssessmentInput,
    rule: ClaimTypeRule,
    support: tuple[RuleEvidenceFacts, ...],
) -> tuple[str, ...]:
    unmet: list[str] = []
    if rule.require_as_of and assessment_input.as_of is None:
        unmet.append("as_of")
    if len(support) < rule.minimum_supporting_evidence:
        unmet.append(f"supporting_evidence:{len(support)}/{rule.minimum_supporting_evidence}")
    groups = {item.independence_group for item in support if item.independence_group is not None}
    if len(groups) < rule.minimum_independence_groups:
        unmet.append(f"independence_groups:{len(groups)}/{rule.minimum_independence_groups}")
    return tuple(unmet)
