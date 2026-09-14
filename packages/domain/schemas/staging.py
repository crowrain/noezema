"""Curator staging proposal schema (T1.13).

The curator proposes; the trusted host validates and records these as
session_staging operations (in M1 — in memory; the durable table arrives
with M2). Grade/epistemic status are NOT proposed here: only the
deterministic rules engine assigns them (§3.7).
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import ClaimType, EvidenceRelation, QuestionOrigin


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2000)
    claim_type: ClaimType
    scope: JsonDict = Field(default_factory=dict)
    as_of: datetime | None = None


class EvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_index: int = Field(ge=0)
    claim_index: int = Field(ge=0)
    relation: EvidenceRelation
    note: str | None = Field(default=None, max_length=500)


class QuestionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    origin: QuestionOrigin
    rationale: str | None = Field(default=None, max_length=1000)


class CuratorProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    claims: list[ClaimProposal] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    new_questions: list[QuestionProposal] = Field(default_factory=list)

    def validate_against(self, evidence_count: int, questions_max: int = 4) -> list[str]:
        """Host-side validation of references and budgets. Returns problem
        list (empty = valid)."""
        problems: list[str] = []
        for i, link in enumerate(self.evidence_links):
            if link.evidence_index >= evidence_count:
                problems.append(f"evidence_link[{i}]: index {link.evidence_index} out of range")
            if link.claim_index >= len(self.claims):
                problems.append(f"evidence_link[{i}]: claim index {link.claim_index} out of range")
        for i, claim in enumerate(self.claims):
            if claim.claim_type is ClaimType.TEMPORAL_FACT and claim.as_of is None:
                problems.append(f"claim[{i}]: temporal_fact requires as_of")
        if len(self.new_questions) > questions_max:
            problems.append(f"new_questions: {len(self.new_questions)} > {questions_max}")
        return problems
