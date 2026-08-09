"""Tests for per-kind observation contracts and trusted evidence identity."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import (
    ActionId,
    ArtifactId,
    ArtifactReference,
    ChunkId,
    ComputationObservation,
    EnvironmentManifestId,
    EnvironmentManifestReference,
    EvidenceKind,
    EvidenceProposal,
    EvidenceUse,
    ObservationId,
    ObservationProvenance,
    SourceId,
    SourceObservation,
    SourceRange,
    ToolFingerprint,
    ToolName,
    evidence_identity_sha256,
)

NOW = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def _provenance(*, tool: ToolName = ToolName.WEB_FETCH) -> ObservationProvenance:
    return ObservationProvenance(
        action_id=ActionId.new(),
        tool=tool,
        source="https://example.test/source",
        captured_at=NOW,
    )


def _source_observation() -> SourceObservation:
    return SourceObservation(
        id=ObservationId.new(),
        kind=EvidenceKind.SOURCE_ASSERTION,
        payload_sha256="1" * 64,
        provenance=_provenance(),
        source_id=SourceId.new(),
        chunk_id=ChunkId.new(),
        source_content_sha256="2" * 64,
        chunk_sha256="3" * 64,
        normalized_range=SourceRange(
            unit="text",
            start=10,
            end=40,
            normalization_version="unicode-nfc/v1",
        ),
    )


def _artifact() -> ArtifactReference:
    return ArtifactReference(
        id=ArtifactId.new(),
        sha256="4" * 64,
        media_type="application/json",
        size=128,
    )


def _environment() -> EnvironmentManifestReference:
    return EnvironmentManifestReference(
        id=EnvironmentManifestId.new(),
        sha256="5" * 64,
        schema_version="environment/v1",
    )


def test_source_identity_uses_content_and_range_not_runtime_ids() -> None:
    first = _source_observation()
    retried = first.model_copy(
        update={
            "id": ObservationId.new(),
            "provenance": _provenance(),
            "source_id": SourceId.new(),
            "chunk_id": ChunkId.new(),
        }
    )
    changed_range = first.model_copy(
        update={
            "normalized_range": SourceRange(
                unit="text",
                start=10,
                end=41,
                normalization_version="unicode-nfc/v1",
            )
        }
    )

    assert evidence_identity_sha256(first) == evidence_identity_sha256(retried)
    assert evidence_identity_sha256(first) != evidence_identity_sha256(changed_range)


def test_computation_binds_tool_fingerprint_to_provenance() -> None:
    with pytest.raises(ValidationError, match="tool fingerprint"):
        ComputationObservation(
            id=ObservationId.new(),
            kind=EvidenceKind.COMPUTATION,
            payload_sha256="6" * 64,
            provenance=_provenance(tool=ToolName.PYTHON_EXECUTE),
            artifact=_artifact(),
            environment=_environment(),
            inputs_sha256="7" * 64,
            algorithm_sha256="8" * 64,
            tool_fingerprint=ToolFingerprint(
                tool=ToolName.SHELL_EXECUTE,
                version="1.0",
                build_sha256="9" * 64,
            ),
        )


def test_source_range_is_nonempty_and_half_open() -> None:
    with pytest.raises(ValidationError, match="greater than start"):
        SourceRange(unit="bytes", start=10, end=10, normalization_version="raw-bytes/v1")


def test_evidence_proposal_rejects_forged_identity_or_observation_id() -> None:
    observation = _source_observation()

    with pytest.raises(ValidationError, match="canonical evidence identity"):
        EvidenceProposal(
            claim_ref="claim_1",
            observation_id=observation.id,
            relation=EvidenceUse.SUPPORT,
            evidence_kind=observation.kind,
            identity_sha256="0" * 64,
            scope="Only the cited source range.",
            observation=observation,
        )

    with pytest.raises(ValidationError, match="does not match the typed observation"):
        EvidenceProposal(
            claim_ref="claim_1",
            observation_id=ObservationId.new(),
            relation=EvidenceUse.SUPPORT,
            evidence_kind=observation.kind,
            identity_sha256=evidence_identity_sha256(observation),
            scope="Only the cited source range.",
            observation=observation,
        )
