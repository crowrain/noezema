"""Tests for deterministic trusted adaptation of Curator evidence references."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from packages.cognition import (
    CuratorClaimProposal,
    CuratorOutcome,
    CuratorProposal,
    EvidenceReference,
    EvidenceRelation,
)
from packages.domain import (
    ActionId,
    ArtifactId,
    ArtifactReference,
    EnvironmentManifestId,
    EnvironmentManifestReference,
    EvidenceAdapterBudget,
    EvidenceKind,
    EvidenceProposal,
    EvidenceUse,
    ExperimentObservation,
    ObservationId,
    ObservationProvenance,
    ToolName,
    evidence_identity_sha256,
)
from packages.memory import (
    DuplicateObservationIdError,
    EvidenceIdentityConflictError,
    StagingBudgetExceededError,
    UnknownObservationError,
    adapt_evidence_proposals,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _observation(*, payload_sha256: str = "1" * 64) -> ExperimentObservation:
    return ExperimentObservation(
        id=ObservationId.new(),
        kind=EvidenceKind.LOCAL_OBSERVATION,
        payload_sha256=payload_sha256,
        provenance=ObservationProvenance(
            action_id=ActionId.new(),
            tool=ToolName.PYTHON_EXECUTE,
            source="sandbox://session/result.json",
            captured_at=NOW,
        ),
        artifact=ArtifactReference(
            id=ArtifactId.new(),
            sha256="2" * 64,
            media_type="application/json",
            size=64,
        ),
        environment=EnvironmentManifestReference(
            id=EnvironmentManifestId.new(),
            sha256="3" * 64,
            schema_version="environment/v1",
        ),
        protocol_output_sha256="4" * 64,
    )


def _proposal(*references: EvidenceReference) -> CuratorProposal:
    return CuratorProposal(
        public_summary="Evidence was proposed for deterministic staging.",
        outcome=CuratorOutcome.PROGRESS,
        claims=(
            CuratorClaimProposal(
                ref="claim_1",
                statement="The observed behavior is reproducible in this environment.",
                claim_type="local_observation",
                topic="runtime",
                evidence=references,
            ),
        ),
    )


def _reference(
    observation: ExperimentObservation,
    *,
    relation: EvidenceRelation = EvidenceRelation.SUPPORTS,
    scope: str = "The recorded environment and capture time only.",
) -> EvidenceReference:
    return EvidenceReference(
        observation_id=observation.id,
        relation=relation,
        scope=scope,
    )


def _budget(remaining: int = 10) -> EvidenceAdapterBudget:
    return EvidenceAdapterBudget(remaining_evidence_items=remaining)


def test_adapter_binds_reference_to_exact_observation_and_provenance() -> None:
    observation = _observation()

    result = adapt_evidence_proposals(
        _proposal(_reference(observation)),
        observations=(observation,),
        budget=_budget(),
    )

    assert len(result.proposals) == 1
    staged = result.proposals[0]
    assert staged.observation is observation
    assert staged.observation.provenance.action_id == observation.provenance.action_id
    assert staged.relation is EvidenceUse.SUPPORT
    assert staged.identity_sha256 == evidence_identity_sha256(observation)
    assert not result.duplicates
    assert result.model_validate_json(result.model_dump_json()) == result


def test_staging_schema_contains_no_assessment_fields() -> None:
    properties = EvidenceProposal.model_json_schema()["properties"]

    assert {"grade", "status", "confidence"}.isdisjoint(properties)


def test_model_summary_without_references_never_becomes_evidence() -> None:
    proposal = CuratorProposal(
        public_summary="A persuasive model-only conclusion.",
        outcome=CuratorOutcome.COMPLETED,
    )

    result = adapt_evidence_proposals(proposal, observations=(), budget=_budget())

    assert not result.proposals
    assert not result.duplicates


def test_unknown_observation_is_rejected_before_staging() -> None:
    available = _observation()
    unknown = _observation()

    with pytest.raises(UnknownObservationError) as error:
        adapt_evidence_proposals(
            _proposal(_reference(unknown)),
            observations=(available,),
            budget=_budget(),
        )

    assert error.value.code == "unknown_observation"
    assert error.value.observation_ids == (unknown.id,)


def test_duplicate_identity_is_collapsed_deterministically_and_audited() -> None:
    first = _observation()
    second = first.model_copy(
        update={
            "id": ObservationId.new(),
            "provenance": first.provenance.model_copy(update={"action_id": ActionId.new()}),
            "artifact": first.artifact.model_copy(update={"id": ArtifactId.new()}),
        }
    )
    references = (_reference(first), _reference(second))

    forward = adapt_evidence_proposals(
        _proposal(*references),
        observations=(first, second),
        budget=_budget(),
    )
    reverse = adapt_evidence_proposals(
        _proposal(*reversed(references)),
        observations=(second, first),
        budget=_budget(),
    )

    assert forward == reverse
    assert len(forward.proposals) == 1
    assert len(forward.duplicates) == 1
    expected_kept = min((first.id, second.id), key=str)
    assert forward.proposals[0].observation_id == expected_kept
    assert forward.duplicates[0].kept_observation_id == expected_kept


def test_same_identity_with_conflicting_semantics_is_rejected() -> None:
    first = _observation()
    second = first.model_copy(update={"id": ObservationId.new()})

    with pytest.raises(EvidenceIdentityConflictError):
        adapt_evidence_proposals(
            _proposal(
                _reference(first, relation=EvidenceRelation.SUPPORTS),
                _reference(second, relation=EvidenceRelation.COUNTERS),
            ),
            observations=(first, second),
            budget=_budget(),
        )


def test_unique_proposals_are_checked_against_remaining_budget() -> None:
    first = _observation(payload_sha256="5" * 64)
    second = _observation(payload_sha256="6" * 64)

    with pytest.raises(StagingBudgetExceededError) as error:
        adapt_evidence_proposals(
            _proposal(_reference(first), _reference(second)),
            observations=(first, second),
            budget=_budget(remaining=1),
        )

    assert error.value.code == "staging_budget_exceeded"
    assert error.value.requested == 2
    assert error.value.remaining == 1


def test_trusted_input_cannot_repeat_an_observation_id() -> None:
    first = _observation()
    conflicting = _observation().model_copy(update={"id": first.id})

    with pytest.raises(DuplicateObservationIdError) as error:
        adapt_evidence_proposals(
            _proposal(_reference(first)),
            observations=(first, conflicting),
            budget=_budget(),
        )

    assert error.value.observation_id == first.id
