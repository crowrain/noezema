"""Strict explorer and curator prompt protocols for the MVP cognitive loop."""

from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from packages.cognition.prompts import PromptBundle, PromptKind
from packages.domain import (
    ClaimReference,
    ComputationObservation,
    DecisionEnvelope,
    EvidenceKind,
    ExperimentObservation,
    ObservationId,
    ObservationProvenance,
    QuestionId,
    ToolDecision,
    ToolName,
    TypedObservation,
    canonical_json_sha256,
    evidence_identity_sha256,
)
from packages.domain._base import ContractModel, NonEmptyText, Sha256Hex, ShortReason
from packages.llm_gateway import (
    ChatMessage,
    ChatRole,
    GatewayRequest,
    ModelPhase,
    ModelRole,
)

ClaimTypeName = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=64),
]


class ProtocolQuestion(ContractModel):
    id: QuestionId
    text: NonEmptyText


class ProtocolObservation(ContractModel):
    """Read-only observation reference exposed to a model role."""

    id: ObservationId
    kind: EvidenceKind
    public_summary: NonEmptyText
    payload_sha256: Sha256Hex
    artifact_sha256: Sha256Hex | None = None
    identity_sha256: Sha256Hex
    provenance: ObservationProvenance

    @classmethod
    def from_typed(
        cls,
        observation: TypedObservation,
        *,
        public_summary: str,
    ) -> ProtocolObservation:
        """Expose a bounded model-facing view of one trusted observation."""

        artifact_sha256 = (
            observation.artifact.sha256
            if isinstance(observation, ExperimentObservation | ComputationObservation)
            else None
        )
        return cls(
            id=observation.id,
            kind=observation.kind,
            public_summary=public_summary,
            payload_sha256=observation.payload_sha256,
            artifact_sha256=artifact_sha256,
            identity_sha256=evidence_identity_sha256(observation),
            provenance=observation.provenance,
        )


class RelevantClaim(ContractModel):
    reference: ClaimReference
    statement: NonEmptyText


class ExplorerContext(ContractModel):
    """One question and a bounded, public context for one Explorer step."""

    question: ProtocolQuestion
    remaining_actions: int = Field(ge=0, le=100)
    allowed_tools: tuple[ToolName, ...] = Field(max_length=32)
    prior_summary: NonEmptyText | None = None
    relevant_claims: tuple[RelevantClaim, ...] = Field(default=(), max_length=64)
    contradictions: tuple[NonEmptyText, ...] = Field(default=(), max_length=32)
    new_messages: tuple[NonEmptyText, ...] = Field(default=(), max_length=32)
    recent_errors: tuple[NonEmptyText, ...] = Field(default=(), max_length=32)
    observations: tuple[ProtocolObservation, ...] = Field(default=(), max_length=64)

    @model_validator(mode="after")
    def require_unique_observations(self) -> ExplorerContext:
        ids = [observation.id.root for observation in self.observations]
        if len(ids) != len(set(ids)):
            raise ValueError("observation IDs must be unique")
        if len(self.allowed_tools) != len(set(self.allowed_tools)):
            raise ValueError("allowed tools must be unique")
        return self


class CuratorContext(ContractModel):
    """Evidence boundary and host budgets for one Curator proposal."""

    question: ProtocolQuestion
    prior_summary: NonEmptyText | None = None
    observations: tuple[ProtocolObservation, ...] = Field(max_length=128)
    allowed_claim_types: tuple[ClaimTypeName, ...] = Field(min_length=1, max_length=32)
    remaining_claim_budget: int = Field(ge=0, le=100)
    remaining_evidence_link_budget: int = Field(ge=0, le=1000)
    remaining_handoff_budget: int = Field(ge=0, le=100)

    @model_validator(mode="after")
    def require_unique_host_inputs(self) -> CuratorContext:
        observation_ids = [observation.id.root for observation in self.observations]
        if len(observation_ids) != len(set(observation_ids)):
            raise ValueError("observation IDs must be unique")
        if len(self.allowed_claim_types) != len(set(self.allowed_claim_types)):
            raise ValueError("allowed claim types must be unique")
        return self


class EvidenceRelation(StrEnum):
    SUPPORTS = "supports"
    COUNTERS = "counters"


class EvidenceReference(ContractModel):
    observation_id: ObservationId
    relation: EvidenceRelation
    scope: NonEmptyText


class CuratorClaimProposal(ContractModel):
    """A local staging reference; the host assigns durable identities later."""

    ref: ClaimReference
    statement: NonEmptyText
    claim_type: ClaimTypeName
    topic: ShortReason
    evidence: tuple[EvidenceReference, ...] = Field(min_length=1, max_length=16)


class HandoffProposal(ContractModel):
    question: NonEmptyText
    reason: ShortReason
    priority: int = Field(default=0, ge=-1000, le=1000)


class CuratorOutcome(StrEnum):
    PROGRESS = "progress"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    COMPLETED = "completed"


class CuratorProposal(ContractModel):
    """Untrusted staging proposal; deliberately has no grade, status, or confidence."""

    public_summary: NonEmptyText
    outcome: CuratorOutcome
    claims: tuple[CuratorClaimProposal, ...] = Field(default=(), max_length=32)
    handoffs: tuple[HandoffProposal, ...] = Field(default=(), max_length=16)

    @model_validator(mode="after")
    def require_unique_local_claim_refs(self) -> CuratorProposal:
        refs = [claim.ref for claim in self.claims]
        if len(refs) != len(set(refs)):
            raise ValueError("claim refs must be unique within a proposal")
        return self


class CuratorProposalValidationError(ValueError):
    """The proposal crossed a host-owned evidence or budget boundary."""


class ExplorerDecisionValidationError(ValueError):
    """The Explorer decision crossed its host-owned action boundary."""


def validate_explorer_decision(
    decision: DecisionEnvelope,
    *,
    context: ExplorerContext,
) -> None:
    """Enforce the action budget and allowlist outside the untrusted model."""

    if not isinstance(decision.decision, ToolDecision):
        return
    if context.remaining_actions == 0:
        raise ExplorerDecisionValidationError("tool decision exceeds the action budget")
    if decision.decision.tool not in context.allowed_tools:
        raise ExplorerDecisionValidationError("tool decision is outside the context allowlist")


def validate_curator_proposal(
    proposal: CuratorProposal,
    *,
    context: CuratorContext,
) -> None:
    """Apply checks that require trusted context and therefore cannot be model schema rules."""

    errors: list[str] = []
    if len(proposal.claims) > context.remaining_claim_budget:
        errors.append("claim budget exceeded")
    if len(proposal.handoffs) > context.remaining_handoff_budget:
        errors.append("handoff budget exceeded")

    allowed_types = set(context.allowed_claim_types)
    available_observations = {observation.id.root for observation in context.observations}
    evidence_links = 0
    for claim in proposal.claims:
        if claim.claim_type not in allowed_types:
            errors.append(f"claim {claim.ref} uses a disallowed claim type")
        seen_links: set[tuple[object, EvidenceRelation]] = set()
        for evidence in claim.evidence:
            evidence_links += 1
            if evidence.observation_id.root not in available_observations:
                errors.append(f"claim {claim.ref} references an unknown observation")
            link = (evidence.observation_id.root, evidence.relation)
            if link in seen_links:
                errors.append(f"claim {claim.ref} repeats an evidence link")
            seen_links.add(link)

    if evidence_links > context.remaining_evidence_link_budget:
        errors.append("evidence-link budget exceeded")
    if errors:
        raise CuratorProposalValidationError("; ".join(errors))


def build_explorer_request(
    context: ExplorerContext,
    *,
    prompts: PromptBundle,
    policy_version: str,
) -> GatewayRequest:
    """Build one reproducible Explorer request that expects DecisionEnvelope output."""

    _require_role(prompts, PromptKind.EXPLORER)
    return _build_request(
        protocol="explorer-input/v2",
        context=context,
        prompts=prompts,
        policy_version=policy_version,
        role=ModelRole.EXPLORER,
        phase=ModelPhase.EXPLORATION,
    )


def build_curator_request(
    context: CuratorContext,
    *,
    prompts: PromptBundle,
    policy_version: str,
) -> GatewayRequest:
    """Build one reproducible Curator request that expects CuratorProposal output."""

    _require_role(prompts, PromptKind.CURATOR)
    return _build_request(
        protocol="curator-input/v2",
        context=context,
        prompts=prompts,
        policy_version=policy_version,
        role=ModelRole.CURATOR,
        phase=ModelPhase.CONSOLIDATION,
    )


def _require_role(prompts: PromptBundle, expected: PromptKind) -> None:
    if prompts.role.kind is not expected:
        raise ValueError(f"expected a {expected.value} prompt bundle")


def _build_request(
    *,
    protocol: str,
    context: ExplorerContext | CuratorContext,
    prompts: PromptBundle,
    policy_version: str,
    role: ModelRole,
    phase: ModelPhase,
) -> GatewayRequest:
    payload = {
        "protocol": protocol,
        "context": context.model_dump(mode="json"),
    }
    user_content = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return GatewayRequest(
        messages=(
            ChatMessage(role=ChatRole.SYSTEM, content=prompts.system_prompt),
            ChatMessage(role=ChatRole.USER, content=user_content),
        ),
        role=role,
        phase=phase,
        prompt_version=prompts.version,
        prompt_sha256=prompts.sha256,
        context_manifest_sha256=canonical_json_sha256(payload),
        policy_version=policy_version,
    )
