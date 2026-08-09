"""Tests for deterministic, version-bound epistemic assessment rules."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import (
    ActionId,
    ArtifactId,
    ArtifactReference,
    ChunkId,
    ClaimAssessmentInput,
    ClaimType,
    ClaimTypeRulesSnapshot,
    ComputationObservation,
    EnvironmentManifestId,
    EnvironmentManifestReference,
    EpistemicStatus,
    EvidenceGrade,
    EvidenceKind,
    EvidenceProposal,
    EvidenceUse,
    ExperimentObservation,
    ObservationId,
    ObservationProvenance,
    RuleEvidenceFacts,
    ScopeDimension,
    SourceId,
    SourceObservation,
    SourceRange,
    ToolFingerprint,
    ToolName,
    evidence_identity_sha256,
)
from packages.memory import AssessmentRuleInputError, assess_claim, mvp_claim_type_rules

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)
HEX = "123456789abcdef"


def _source_fact(
    seed: int,
    *,
    kind: EvidenceKind = EvidenceKind.SOURCE_ASSERTION,
    relation: EvidenceUse = EvidenceUse.SUPPORT,
    group: str | None = None,
    scope: tuple[ScopeDimension, ...] = (ScopeDimension.CLAIM,),
    integrity_checked: bool = True,
    resolved: bool = False,
) -> RuleEvidenceFacts:
    marker = HEX[seed]
    observation = SourceObservation(
        id=ObservationId.new(),
        kind=kind,
        payload_sha256=marker * 64,
        provenance=ObservationProvenance(
            action_id=ActionId.new(),
            tool=ToolName.WEB_FETCH,
            source=f"https://source-{seed}.example.test/document",
            captured_at=NOW,
        ),
        source_id=SourceId.new(),
        chunk_id=ChunkId.new(),
        source_content_sha256=marker * 64,
        chunk_sha256=HEX[seed + 1] * 64,
        normalized_range=SourceRange(
            unit="text",
            start=0,
            end=10,
            normalization_version="text/v1",
        ),
    )
    proposal = EvidenceProposal(
        claim_ref="claim_1",
        observation_id=observation.id,
        relation=relation,
        evidence_kind=observation.kind,
        identity_sha256=evidence_identity_sha256(observation),
        scope="Host-validated scope assertion.",
        observation=observation,
    )
    return RuleEvidenceFacts(
        proposal=proposal,
        covered_scope=scope,
        integrity_checked=integrity_checked,
        independence_group=group,
        counterevidence_resolved=resolved,
    )


def _formal_fact(*, relation: EvidenceUse = EvidenceUse.SUPPORT) -> RuleEvidenceFacts:
    return _computation_fact(
        kind=EvidenceKind.FORMAL_CHECK,
        relation=relation,
        scope=(ScopeDimension.AXIOMS, ScopeDimension.MODEL),
    )


def _computation_fact(
    *,
    kind: EvidenceKind = EvidenceKind.COMPUTATION,
    relation: EvidenceUse = EvidenceUse.SUPPORT,
    scope: tuple[ScopeDimension, ...] = (ScopeDimension.INPUTS, ScopeDimension.ALGORITHM),
) -> RuleEvidenceFacts:
    observation = ComputationObservation(
        id=ObservationId.new(),
        kind=kind,
        payload_sha256="1" * 64,
        provenance=ObservationProvenance(
            action_id=ActionId.new(),
            tool=ToolName.PYTHON_EXECUTE,
            source="sandbox://formal-check/result.json",
            captured_at=NOW,
        ),
        artifact=ArtifactReference(
            id=ArtifactId.new(),
            sha256="2" * 64,
            media_type="application/json",
            size=100,
        ),
        environment=EnvironmentManifestReference(
            id=EnvironmentManifestId.new(),
            sha256="3" * 64,
            schema_version="environment/v1",
        ),
        inputs_sha256="4" * 64,
        algorithm_sha256="5" * 64,
        tool_fingerprint=ToolFingerprint(
            tool=ToolName.PYTHON_EXECUTE,
            version="checker/v1",
            build_sha256="6" * 64,
        ),
    )
    proposal = EvidenceProposal(
        claim_ref="claim_1",
        observation_id=observation.id,
        relation=relation,
        evidence_kind=observation.kind,
        identity_sha256=evidence_identity_sha256(observation),
        scope="Exact computation or formal scope.",
        observation=observation,
    )
    return RuleEvidenceFacts(
        proposal=proposal,
        covered_scope=scope,
    )


def _experiment_fact(
    seed: int,
    *,
    kind: EvidenceKind = EvidenceKind.EXPERIMENT_RUN,
    group: str | None = None,
    scope: tuple[ScopeDimension, ...] = (ScopeDimension.CLAIM,),
    successful: bool | None = True,
) -> RuleEvidenceFacts:
    marker = HEX[seed]
    observation = ExperimentObservation(
        id=ObservationId.new(),
        kind=kind,
        payload_sha256=marker * 64,
        provenance=ObservationProvenance(
            action_id=ActionId.new(),
            tool=ToolName.PYTHON_EXECUTE,
            source=f"sandbox://experiment-{seed}/result.json",
            captured_at=NOW,
        ),
        artifact=ArtifactReference(
            id=ArtifactId.new(),
            sha256=HEX[seed + 1] * 64,
            media_type="application/json",
            size=100,
        ),
        environment=EnvironmentManifestReference(
            id=EnvironmentManifestId.new(),
            sha256=HEX[seed + 2] * 64,
            schema_version="environment/v1",
        ),
        protocol_output_sha256=HEX[seed + 3] * 64,
    )
    proposal = EvidenceProposal(
        claim_ref="claim_1",
        observation_id=observation.id,
        relation=EvidenceUse.SUPPORT,
        evidence_kind=observation.kind,
        identity_sha256=evidence_identity_sha256(observation),
        scope="Host-validated experimental scope.",
        observation=observation,
    )
    return RuleEvidenceFacts(
        proposal=proposal,
        covered_scope=scope,
        independence_group=group,
        successful=successful,
    )


def _assess(
    claim_type: ClaimType,
    *evidence: RuleEvidenceFacts,
    as_of: datetime | None = None,
):
    return assess_claim(
        ClaimAssessmentInput(
            claim_ref="claim_1",
            claim_type=claim_type,
            evidence=evidence,
            as_of=as_of,
        ),
        rules=mvp_claim_type_rules(),
    )


def test_rules_snapshot_is_complete_content_addressed_and_stable() -> None:
    first = mvp_claim_type_rules()
    second = mvp_claim_type_rules()

    assert {rule.claim_type for rule in first.rules} == set(ClaimType)
    assert first == second
    with pytest.raises(ValidationError, match="hash"):
        ClaimTypeRulesSnapshot.model_validate({**first.model_dump(), "sha256": "0" * 64})


def test_two_independent_sources_support_an_external_fact_at_e3() -> None:
    result = _assess(
        ClaimType.EXTERNAL_FACT,
        _source_fact(0, group="publisher-a"),
        _source_fact(2, group="publisher-b"),
    )

    assert result.effective_grade is EvidenceGrade.E3
    assert result.epistemic_status is EpistemicStatus.SUPPORTED
    assert result.confidence_basis_points == 8_000
    assert not result.unmet_requirements


def test_single_observation_and_computation_rules_reach_e2_only_in_exact_scope() -> None:
    local = _experiment_fact(
        0,
        kind=EvidenceKind.LOCAL_OBSERVATION,
        scope=(ScopeDimension.ENVIRONMENT, ScopeDimension.TIME),
        successful=None,
    )
    self_observation = _experiment_fact(
        4,
        kind=EvidenceKind.LOCAL_OBSERVATION,
        scope=(ScopeDimension.CONFIGURATION,),
        successful=None,
    )

    local_result = _assess(ClaimType.LOCAL_OBSERVATION, local)
    computed_result = _assess(ClaimType.COMPUTED_RESULT, _computation_fact())
    self_result = _assess(ClaimType.SELF_MODEL, self_observation)

    assert local_result.effective_grade is EvidenceGrade.E2
    assert computed_result.effective_grade is EvidenceGrade.E2
    assert self_result.effective_grade is EvidenceGrade.E2
    assert local_result.assessed_scope == (
        ScopeDimension.ENVIRONMENT,
        ScopeDimension.TIME,
    )


@pytest.mark.parametrize(
    "claim_type",
    (ClaimType.EMPIRICAL_CONJECTURE, ClaimType.PROCEDURAL),
)
def test_reproducibility_requires_two_successful_independent_environments(
    claim_type: ClaimType,
) -> None:
    first = _experiment_fact(0, group="environment-a")
    second = _experiment_fact(4, group="environment-b")
    failed = _experiment_fact(8, group="environment-c", successful=False)

    one_replication = _assess(claim_type, first, failed)
    replicated = _assess(claim_type, first, second)

    assert one_replication.effective_grade is EvidenceGrade.E2
    assert one_replication.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert replicated.effective_grade is EvidenceGrade.E3
    assert replicated.epistemic_status is EpistemicStatus.SUPPORTED


def test_same_source_group_is_single_method_support_not_corroboration() -> None:
    result = _assess(
        ClaimType.EXTERNAL_FACT,
        _source_fact(0, group="same-publisher"),
        _source_fact(2, group="same-publisher"),
    )

    assert result.effective_grade is EvidenceGrade.E2
    assert result.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert result.unmet_requirements == ("independence_groups:1/2",)


def test_quote_integrity_alone_is_capped_at_e1() -> None:
    result = _assess(
        ClaimType.EXTERNAL_FACT,
        _source_fact(0, kind=EvidenceKind.QUOTE_INTEGRITY),
    )

    assert result.effective_grade is EvidenceGrade.E1
    assert result.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert not result.supporting_evidence


def test_unresolved_in_scope_counterevidence_makes_supported_claim_disputed() -> None:
    support = (
        _source_fact(0, group="publisher-a"),
        _source_fact(2, group="publisher-b"),
    )
    counter = _source_fact(4, relation=EvidenceUse.COUNTER, group="publisher-c")

    result = _assess(ClaimType.EXTERNAL_FACT, *support, counter)
    resolved = _assess(
        ClaimType.EXTERNAL_FACT,
        *support,
        counter.model_copy(update={"counterevidence_resolved": True}),
    )

    assert result.epistemic_status is EpistemicStatus.DISPUTED
    assert result.confidence_basis_points == 5_000
    assert result.unresolved_counterevidence == (counter.proposal.identity_sha256,)
    assert resolved.epistemic_status is EpistemicStatus.SUPPORTED


def test_refutation_requires_the_rule_independence_threshold() -> None:
    one_counter = _source_fact(0, relation=EvidenceUse.COUNTER, group="publisher-a")
    second_counter = _source_fact(2, relation=EvidenceUse.COUNTER, group="publisher-b")

    weak = _assess(ClaimType.EXTERNAL_FACT, one_counter)
    sufficient = _assess(ClaimType.EXTERNAL_FACT, one_counter, second_counter)

    assert weak.epistemic_status is EpistemicStatus.DISPUTED
    assert sufficient.epistemic_status is EpistemicStatus.REFUTED
    assert sufficient.confidence_basis_points == 500


def test_integrity_and_scope_are_checked_before_counting_evidence() -> None:
    unchecked = _source_fact(0, group="publisher-a", integrity_checked=False)
    out_of_scope = _source_fact(2, group="publisher-b", scope=())

    result = _assess(ClaimType.EXTERNAL_FACT, unchecked, out_of_scope)

    assert result.effective_grade is EvidenceGrade.E1
    assert result.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert result.unmet_requirements == (
        "supporting_evidence:0/2",
        "independence_groups:0/2",
    )


def test_temporal_fact_requires_as_of_and_temporal_scope() -> None:
    first = _source_fact(
        0,
        group="publisher-a",
        scope=(ScopeDimension.CLAIM, ScopeDimension.TEMPORAL),
    )
    second = _source_fact(
        2,
        group="publisher-b",
        scope=(ScopeDimension.CLAIM, ScopeDimension.TEMPORAL),
    )

    missing_as_of = _assess(ClaimType.TEMPORAL_FACT, first, second)
    complete = _assess(ClaimType.TEMPORAL_FACT, first, second, as_of=NOW)

    assert missing_as_of.effective_grade is EvidenceGrade.E1
    assert missing_as_of.unmet_requirements == ("as_of",)
    assert complete.effective_grade is EvidenceGrade.E3
    assert complete.epistemic_status is EpistemicStatus.SUPPORTED


def test_only_formal_check_can_reach_e4_for_a_theorem() -> None:
    result = _assess(ClaimType.FORMAL_THEOREM, _formal_fact())

    assert result.effective_grade is EvidenceGrade.E4
    assert result.epistemic_status is EpistemicStatus.SUPPORTED
    with pytest.raises(AssessmentRuleInputError, match="cannot support formal_theorem"):
        _assess(
            ClaimType.FORMAL_THEOREM,
            _computation_fact(
                scope=(ScopeDimension.AXIOMS, ScopeDimension.MODEL),
            ),
        )


def test_assessment_is_order_independent_and_bound_to_evidence_set() -> None:
    first = _source_fact(0, group="publisher-a")
    second = _source_fact(2, group="publisher-b")

    forward = _assess(ClaimType.EXTERNAL_FACT, first, second)
    reverse = _assess(ClaimType.EXTERNAL_FACT, second, first)

    assert forward == reverse
    assert forward.evidence_set_sha256 == reverse.evidence_set_sha256
