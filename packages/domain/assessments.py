"""Closed claim taxonomy and deterministic assessment contracts."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, Field, model_validator

from packages.domain._base import ContractModel, Sha256Hex, ShortReason
from packages.domain.actions import canonical_json_sha256
from packages.domain.evidence import ClaimReference, EvidenceKind, EvidenceProposal, EvidenceUse


class ClaimType(StrEnum):
    LOCAL_OBSERVATION = "local_observation"
    COMPUTED_RESULT = "computed_result"
    FORMAL_THEOREM = "formal_theorem"
    EMPIRICAL_CONJECTURE = "empirical_conjecture"
    PROCEDURAL = "procedural"
    EXTERNAL_FACT = "external_fact"
    TEMPORAL_FACT = "temporal_fact"
    SELF_MODEL = "self_model"


class EvidenceGrade(StrEnum):
    E0 = "E0"
    E1 = "E1"
    E2 = "E2"
    E3 = "E3"
    E4 = "E4"


class EpistemicStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    SUPPORTED = "supported"
    DISPUTED = "disputed"
    REFUTED = "refuted"
    DEFERRED = "deferred"


class ScopeDimension(StrEnum):
    """Scope predicates established by trusted code, never inferred by this engine."""

    CLAIM = "claim"
    ENVIRONMENT = "environment"
    TIME = "time"
    INPUTS = "inputs"
    ALGORITHM = "algorithm"
    AXIOMS = "axioms"
    MODEL = "model"
    CONFIGURATION = "configuration"
    IDENTITY_STATE = "identity_state"
    TEMPORAL = "temporal"


class RuleEvidenceFacts(ContractModel):
    """Host-verified facts consumed by a claim-type rule."""

    proposal: EvidenceProposal
    covered_scope: tuple[ScopeDimension, ...] = ()
    integrity_checked: bool = False
    independence_group: ShortReason | None = None
    successful: bool | None = None
    counterevidence_resolved: bool = False

    @model_validator(mode="after")
    def require_consistent_facts(self) -> RuleEvidenceFacts:
        if len(self.covered_scope) != len(set(self.covered_scope)):
            raise ValueError("covered scope dimensions must be unique")
        if self.proposal.relation is EvidenceUse.SUPPORT and self.counterevidence_resolved:
            raise ValueError("supporting evidence cannot resolve counterevidence")
        return self


class ClaimAssessmentInput(ContractModel):
    claim_ref: ClaimReference
    claim_type: ClaimType
    evidence: tuple[RuleEvidenceFacts, ...] = Field(max_length=128)
    as_of: AwareDatetime | None = None

    @model_validator(mode="after")
    def bind_evidence_to_claim(self) -> ClaimAssessmentInput:
        identities: set[tuple[EvidenceKind, str]] = set()
        for item in self.evidence:
            if item.proposal.claim_ref != self.claim_ref:
                raise ValueError("assessment evidence belongs to a different claim")
            identity = (item.proposal.evidence_kind, item.proposal.identity_sha256)
            if identity in identities:
                raise ValueError("assessment evidence identities must be unique")
            identities.add(identity)
        return self


class ClaimTypeRule(ContractModel):
    claim_type: ClaimType
    support_kinds: tuple[EvidenceKind, ...] = Field(min_length=1)
    counter_kinds: tuple[EvidenceKind, ...] = Field(min_length=1)
    target_grade: EvidenceGrade
    minimum_supporting_evidence: int = Field(ge=1, le=128)
    minimum_independence_groups: int = Field(ge=0, le=128)
    required_scope_all: tuple[ScopeDimension, ...] = ()
    required_scope_any: tuple[ScopeDimension, ...] = ()
    require_integrity: bool = False
    require_success: bool = False
    require_as_of: bool = False
    max_grade_when_required_fields_absent: EvidenceGrade
    volatility: ShortReason
    reverify_after_seconds: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def require_unique_rule_sets(self) -> ClaimTypeRule:
        for values, label in (
            (self.support_kinds, "support kinds"),
            (self.counter_kinds, "counter kinds"),
            (self.required_scope_all, "required scope dimensions"),
            (self.required_scope_any, "alternative scope dimensions"),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"{label} must be unique")
        if self.minimum_independence_groups > self.minimum_supporting_evidence:
            raise ValueError("independence groups cannot exceed supporting evidence count")
        grade_order = tuple(EvidenceGrade)
        if grade_order.index(self.max_grade_when_required_fields_absent) > grade_order.index(
            self.target_grade
        ):
            raise ValueError("missing-field grade cap cannot exceed the target grade")
        return self


class ConfidencePolicy(ContractModel):
    """Versioned fixed-point policy for confidence in the claim being true."""

    by_grade_basis_points: tuple[int, int, int, int, int]
    disputed_cap_basis_points: int = Field(ge=0, le=10_000)
    refuted_basis_points: int = Field(ge=0, le=10_000)

    @model_validator(mode="after")
    def require_valid_curve(self) -> ConfidencePolicy:
        if any(value < 0 or value > 10_000 for value in self.by_grade_basis_points):
            raise ValueError("grade confidence must be between 0 and 10000 basis points")
        if tuple(sorted(self.by_grade_basis_points)) != self.by_grade_basis_points:
            raise ValueError("grade confidence must be monotonic")
        return self


class ClaimTypeRulesSnapshot(ContractModel):
    version: ShortReason
    rules: tuple[ClaimTypeRule, ...]
    confidence: ConfidencePolicy
    sha256: Sha256Hex

    @classmethod
    def build(
        cls,
        *,
        version: str,
        rules: tuple[ClaimTypeRule, ...],
        confidence: ConfidencePolicy,
    ) -> ClaimTypeRulesSnapshot:
        material = {
            "schema": "claim-type-rules/v1",
            "version": version,
            "rules": [rule.model_dump(mode="json") for rule in rules],
            "confidence": confidence.model_dump(mode="json"),
        }
        return cls(
            version=version,
            rules=rules,
            confidence=confidence,
            sha256=canonical_json_sha256(material),
        )

    @model_validator(mode="after")
    def verify_complete_snapshot(self) -> ClaimTypeRulesSnapshot:
        claim_types = [rule.claim_type for rule in self.rules]
        if len(claim_types) != len(set(claim_types)):
            raise ValueError("claim type rules must be unique")
        if set(claim_types) != set(ClaimType):
            raise ValueError("rules snapshot must define every registered claim type")
        material = {
            "schema": "claim-type-rules/v1",
            "version": self.version,
            "rules": [rule.model_dump(mode="json") for rule in self.rules],
            "confidence": self.confidence.model_dump(mode="json"),
        }
        if self.sha256 != canonical_json_sha256(material):
            raise ValueError("rules snapshot hash does not match its canonical content")
        return self


class ClaimAssessment(ContractModel):
    """Pure assessment result; persistence and current-head activation happen later."""

    claim_ref: ClaimReference
    claim_type: ClaimType
    effective_grade: EvidenceGrade
    epistemic_status: EpistemicStatus
    confidence_basis_points: int = Field(ge=0, le=10_000)
    rules_version: ShortReason
    rules_sha256: Sha256Hex
    evidence_set_sha256: Sha256Hex
    as_of: AwareDatetime | None
    assessed_scope: tuple[ScopeDimension, ...]
    supporting_evidence: tuple[Sha256Hex, ...]
    unresolved_counterevidence: tuple[Sha256Hex, ...]
    support_independence_groups: tuple[ShortReason, ...]
    counter_independence_groups: tuple[ShortReason, ...]
    unmet_requirements: tuple[ShortReason, ...]
