"""Host-owned contracts for one validated knowledge commit batch."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, NonNegativeInt, model_validator

from packages.domain._base import ContractModel, NonEmptyText, Sha256Hex, ShortReason
from packages.domain.actions import canonical_json_sha256
from packages.domain.assessments import (
    ClaimAssessment,
    ClaimType,
    ClaimTypeRulesSnapshot,
    RuleEvidenceFacts,
    ScopeDimension,
)
from packages.domain.enums import SessionState
from packages.domain.evidence import EvidenceUse
from packages.domain.ids import ClaimAssessmentId, ClaimId, EvidenceId, ObservationId


class VerifiedEvidenceMetadata(ContractModel):
    """Trusted annotations produced outside the Curator and rules engine."""

    observation_id: ObservationId
    covered_scope: tuple[ScopeDimension, ...] = ()
    integrity_checked: bool = False
    independence_group: ShortReason | None = None
    successful: bool | None = None
    counterevidence_resolved: bool = False

    @model_validator(mode="after")
    def require_unique_scope(self) -> VerifiedEvidenceMetadata:
        if len(self.covered_scope) != len(set(self.covered_scope)):
            raise ValueError("covered scope dimensions must be unique")
        return self


class StagedEvidenceWrite(ContractModel):
    id: EvidenceId
    facts: RuleEvidenceFacts


class StagedClaimWrite(ContractModel):
    id: ClaimId
    statement: NonEmptyText
    claim_type: ClaimType
    topic: ShortReason
    assessment_id: ClaimAssessmentId
    assessment: ClaimAssessment
    evidence: tuple[StagedEvidenceWrite, ...] = Field(max_length=128)

    @model_validator(mode="after")
    def bind_assessment_and_evidence(self) -> StagedClaimWrite:
        if self.assessment.claim_type is not self.claim_type:
            raise ValueError("assessment claim type does not match staged claim")
        evidence_ids = [item.id for item in self.evidence]
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("staged evidence IDs must be unique")
        identities: set[str] = set()
        for item in self.evidence:
            if item.facts.proposal.claim_ref != self.assessment.claim_ref:
                raise ValueError("staged evidence belongs to a different local claim")
            identities.add(item.facts.proposal.identity_sha256)
        assessed_identities = set(self.assessment.supporting_evidence) | set(
            self.assessment.unresolved_counterevidence
        )
        if not assessed_identities.issubset(identities):
            raise ValueError("assessment references evidence outside the staged claim")
        return self


class KnowledgeCommitBatch(ContractModel):
    """Immutable result of evidence adaptation and deterministic assessment."""

    schema_version: Literal["knowledge-commit/v1"] = "knowledge-commit/v1"
    rules_version: ShortReason
    rules_sha256: Sha256Hex
    validated_knowledge_revision: NonNegativeInt
    validated_dependency_graph_revision: NonNegativeInt
    terminal_state: Literal[SessionState.SUCCEEDED, SessionState.SUCCEEDED_PARTIAL]
    claims: tuple[StagedClaimWrite, ...] = Field(default=(), max_length=32)

    @classmethod
    def empty(
        cls,
        *,
        rules: ClaimTypeRulesSnapshot,
        validated_knowledge_revision: int,
        validated_dependency_graph_revision: int,
    ) -> KnowledgeCommitBatch:
        return cls(
            rules_version=rules.version,
            rules_sha256=rules.sha256,
            validated_knowledge_revision=validated_knowledge_revision,
            validated_dependency_graph_revision=validated_dependency_graph_revision,
            terminal_state=SessionState.SUCCEEDED_PARTIAL,
        )

    @model_validator(mode="after")
    def require_unique_and_rule_bound_claims(self) -> KnowledgeCommitBatch:
        claim_ids = [claim.id for claim in self.claims]
        claim_refs = [claim.assessment.claim_ref for claim in self.claims]
        assessment_ids = [claim.assessment_id for claim in self.claims]
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("staged claim IDs must be unique")
        if len(claim_refs) != len(set(claim_refs)):
            raise ValueError("local claim references must be unique")
        if len(assessment_ids) != len(set(assessment_ids)):
            raise ValueError("staged assessment IDs must be unique")
        for claim in self.claims:
            if claim.assessment.rules_version != self.rules_version:
                raise ValueError("assessment rules version does not match batch")
            if claim.assessment.rules_sha256 != self.rules_sha256:
                raise ValueError("assessment rules hash does not match batch")
        return self


def knowledge_commit_sha256(batch: KnowledgeCommitBatch) -> str:
    """Content-address the complete batch, including host-assigned durable identities."""

    return canonical_json_sha256(batch.model_dump(mode="json"))


def assessment_role(item: StagedEvidenceWrite, assessment: ClaimAssessment) -> str:
    """Map staged evidence to the exact role captured by an assessment."""

    identity = item.facts.proposal.identity_sha256
    if identity in assessment.supporting_evidence:
        return "support"
    if identity in assessment.unresolved_counterevidence:
        return "counter"
    if item.facts.proposal.relation is EvidenceUse.COUNTER:
        return "context"
    return "context"
