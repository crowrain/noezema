"""Pure assembly of Curator output into an assessed knowledge commit batch."""

from __future__ import annotations

from collections import defaultdict

from packages.cognition import CuratorProposal
from packages.domain import (
    ClaimAssessmentId,
    ClaimAssessmentInput,
    ClaimId,
    ClaimTypeRulesSnapshot,
    EvidenceAdapterBudget,
    EvidenceId,
    KnowledgeCommitBatch,
    RuleEvidenceFacts,
    SessionState,
    StagedClaimWrite,
    StagedEvidenceWrite,
    TypedObservation,
    VerifiedEvidenceMetadata,
)
from packages.memory.evidence_adapter import adapt_evidence_proposals
from packages.memory.rules import assess_claim


class StagingAssemblyError(ValueError):
    """Trusted evidence metadata cannot be bound to an adapted proposal."""

    code = "staging_assembly_error"


class DuplicateEvidenceMetadataError(StagingAssemblyError):
    code = "duplicate_evidence_metadata"


class MissingEvidenceMetadataError(StagingAssemblyError):
    code = "missing_evidence_metadata"


def build_knowledge_commit_batch(
    proposal: CuratorProposal,
    *,
    observations: tuple[TypedObservation, ...],
    metadata: tuple[VerifiedEvidenceMetadata, ...],
    evidence_budget: EvidenceAdapterBudget,
    rules: ClaimTypeRulesSnapshot,
    validated_knowledge_revision: int,
    validated_dependency_graph_revision: int,
    terminal_state: SessionState = SessionState.SUCCEEDED,
) -> KnowledgeCommitBatch:
    """Adapt, annotate and assess Curator claims without persistence or model calls."""

    if terminal_state not in {SessionState.SUCCEEDED, SessionState.SUCCEEDED_PARTIAL}:
        raise ValueError("knowledge commit must end in a successful terminal state")

    metadata_by_id: dict[object, VerifiedEvidenceMetadata] = {}
    for item in metadata:
        key = item.observation_id.root
        if key in metadata_by_id:
            raise DuplicateEvidenceMetadataError(
                f"duplicate verified metadata for observation: {item.observation_id}"
            )
        metadata_by_id[key] = item

    adapted = adapt_evidence_proposals(
        proposal,
        observations=observations,
        budget=evidence_budget,
    )
    facts_by_ref: dict[str, list[RuleEvidenceFacts]] = defaultdict(list)
    for evidence in adapted.proposals:
        annotation = metadata_by_id.get(evidence.observation_id.root)
        if annotation is None:
            raise MissingEvidenceMetadataError(
                f"verified metadata is missing for observation: {evidence.observation_id}"
            )
        facts_by_ref[evidence.claim_ref].append(
            RuleEvidenceFacts(
                proposal=evidence,
                covered_scope=annotation.covered_scope,
                integrity_checked=annotation.integrity_checked,
                independence_group=annotation.independence_group,
                successful=annotation.successful,
                counterevidence_resolved=annotation.counterevidence_resolved,
            )
        )

    staged_claims: list[StagedClaimWrite] = []
    for claim in proposal.claims:
        facts = tuple(facts_by_ref[claim.ref])
        assessment = assess_claim(
            ClaimAssessmentInput(
                claim_ref=claim.ref,
                claim_type=claim.claim_type,
                evidence=facts,
            ),
            rules=rules,
        )
        staged_claims.append(
            StagedClaimWrite(
                id=ClaimId.new(),
                statement=claim.statement,
                claim_type=claim.claim_type,
                topic=claim.topic,
                assessment_id=ClaimAssessmentId.new(),
                assessment=assessment,
                evidence=tuple(
                    StagedEvidenceWrite(id=EvidenceId.new(), facts=item) for item in facts
                ),
            )
        )

    return KnowledgeCommitBatch(
        rules_version=rules.version,
        rules_sha256=rules.sha256,
        validated_knowledge_revision=validated_knowledge_revision,
        validated_dependency_graph_revision=validated_dependency_graph_revision,
        terminal_state=terminal_state,
        claims=tuple(staged_claims),
    )
