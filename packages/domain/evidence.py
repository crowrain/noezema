"""Trusted typed observations and evidence-staging contracts."""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, TypeAlias

from pydantic import AwareDatetime, Field, NonNegativeInt, StringConstraints, model_validator

from packages.domain._base import ContractModel, NonEmptyText, Sha256Hex, ShortReason
from packages.domain.actions import canonical_json_sha256
from packages.domain.enums import ToolName
from packages.domain.ids import (
    ActionId,
    ArtifactId,
    ChunkId,
    EnvironmentManifestId,
    ObservationId,
    SourceId,
)

ClaimReference = Annotated[
    str,
    StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$"),
]


class EvidenceKind(StrEnum):
    SOURCE_ASSERTION = "source_assertion"
    QUOTE_INTEGRITY = "quote_integrity"
    EXPERIMENT_RUN = "experiment_run"
    COMPUTATION = "computation"
    FORMAL_CHECK = "formal_check"
    LOCAL_OBSERVATION = "local_observation"


class EvidenceUse(StrEnum):
    SUPPORT = "support"
    COUNTER = "counter"


class ObservationProvenance(ContractModel):
    """Causal provenance established by the trusted Tool Broker."""

    action_id: ActionId
    tool: ToolName
    source: NonEmptyText
    captured_at: AwareDatetime


class SourceRange(ContractModel):
    """Normalized half-open range inside one immutable source representation."""

    unit: Literal["bytes", "text"]
    start: NonNegativeInt
    end: int = Field(gt=0)
    normalization_version: ShortReason

    @model_validator(mode="after")
    def require_nonempty_range(self) -> SourceRange:
        if self.end <= self.start:
            raise ValueError("source range end must be greater than start")
        return self


class ArtifactReference(ContractModel):
    id: ArtifactId
    sha256: Sha256Hex
    media_type: ShortReason
    size: NonNegativeInt


class EnvironmentManifestReference(ContractModel):
    id: EnvironmentManifestId
    sha256: Sha256Hex
    schema_version: ShortReason


class ToolFingerprint(ContractModel):
    tool: ToolName
    version: ShortReason
    build_sha256: Sha256Hex


class _ObservationBase(ContractModel):
    id: ObservationId
    payload_sha256: Sha256Hex
    provenance: ObservationProvenance


class SourceObservation(_ObservationBase):
    """Exact source chunk suitable for assertion or quote-integrity evidence."""

    kind: Literal[EvidenceKind.SOURCE_ASSERTION, EvidenceKind.QUOTE_INTEGRITY]
    source_id: SourceId
    chunk_id: ChunkId
    source_content_sha256: Sha256Hex
    chunk_sha256: Sha256Hex
    normalized_range: SourceRange


class ExperimentObservation(_ObservationBase):
    """Recorded experiment or local-system observation with its environment."""

    kind: Literal[EvidenceKind.EXPERIMENT_RUN, EvidenceKind.LOCAL_OBSERVATION]
    artifact: ArtifactReference
    environment: EnvironmentManifestReference
    protocol_output_sha256: Sha256Hex


class ComputationObservation(_ObservationBase):
    """Computation or formal check bound to exact inputs and implementation."""

    kind: Literal[EvidenceKind.COMPUTATION, EvidenceKind.FORMAL_CHECK]
    artifact: ArtifactReference
    environment: EnvironmentManifestReference
    inputs_sha256: Sha256Hex
    algorithm_sha256: Sha256Hex
    tool_fingerprint: ToolFingerprint

    @model_validator(mode="after")
    def bind_provenance_tool(self) -> ComputationObservation:
        if self.provenance.tool is not self.tool_fingerprint.tool:
            raise ValueError("tool fingerprint must match observation provenance")
        return self


TypedObservation: TypeAlias = Annotated[
    SourceObservation | ExperimentObservation | ComputationObservation,
    Field(discriminator="kind"),
]


def evidence_identity_sha256(observation: TypedObservation) -> str:
    """Compute the per-kind identity from canonical content and provenance."""

    if isinstance(observation, SourceObservation):
        material = {
            "schema": "source-evidence-identity/v1",
            "kind": observation.kind.value,
            "source_content_sha256": observation.source_content_sha256,
            "chunk_sha256": observation.chunk_sha256,
            "normalized_range": observation.normalized_range.model_dump(mode="json"),
        }
    elif isinstance(observation, ExperimentObservation):
        material = {
            "schema": "experiment-evidence-identity/v1",
            "kind": observation.kind.value,
            "observation_content_sha256": observation.payload_sha256,
            "environment_manifest_sha256": observation.environment.sha256,
            "protocol_output_sha256": observation.protocol_output_sha256,
        }
    else:
        material = {
            "schema": "computation-evidence-identity/v1",
            "kind": observation.kind.value,
            "result_artifact_sha256": observation.artifact.sha256,
            "inputs_sha256": observation.inputs_sha256,
            "algorithm_sha256": observation.algorithm_sha256,
            "environment_manifest_sha256": observation.environment.sha256,
            "tool_fingerprint": observation.tool_fingerprint.model_dump(mode="json"),
        }
    return canonical_json_sha256(material)


class EvidenceProposal(ContractModel):
    """Host-built staging proposal; it is not current knowledge or an assessment."""

    claim_ref: ClaimReference
    observation_id: ObservationId
    relation: EvidenceUse
    evidence_kind: EvidenceKind
    identity_sha256: Sha256Hex
    scope: NonEmptyText
    observation: TypedObservation

    @model_validator(mode="after")
    def verify_host_binding(self) -> EvidenceProposal:
        if self.observation_id != self.observation.id:
            raise ValueError("observation_id does not match the typed observation")
        if self.evidence_kind is not self.observation.kind:
            raise ValueError("evidence_kind does not match the typed observation")
        if self.identity_sha256 != evidence_identity_sha256(self.observation):
            raise ValueError("identity_sha256 does not match canonical evidence identity")
        return self


class DuplicateEvidenceReference(ContractModel):
    """Auditable duplicate removed before a staging command is accepted."""

    claim_ref: ClaimReference
    evidence_kind: EvidenceKind
    identity_sha256: Sha256Hex
    kept_observation_id: ObservationId
    duplicate_observation_id: ObservationId


class EvidenceAdapterResult(ContractModel):
    proposals: tuple[EvidenceProposal, ...]
    duplicates: tuple[DuplicateEvidenceReference, ...]


class EvidenceAdapterBudget(ContractModel):
    """Remaining staging capacity supplied from the immutable session limits."""

    remaining_evidence_items: int = Field(ge=0, le=1000)
