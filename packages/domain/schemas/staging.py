"""Curator staging proposal schema (T1.13; T7.34 reverify — ADR-0018).

The curator proposes; the trusted host validates and records these as
session_staging operations (in M1 — in memory; the durable table arrives
with M2). Grade/epistemic status are NOT proposed here: only the
deterministic rules engine assigns them (§3.7).

A claim op is one of two: a NEW claim (statement + type), or a REVERIFY
of an existing one (`existing_claim_id` — the host-issued id, full or a
unique prefix). Reverify changes no claim row: it re-derives the
reference date/scope and produces a fresh assessment (the reverify
record, bound to the session) — the gate-5 "reverified" path.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import (
    ClaimType,
    DependencyKind,
    EvidenceRelation,
    QuestionOrigin,
)

#: hard cap on declared dependencies per claim (host-side budget)
MAX_DEPENDENCIES_PER_CLAIM = 10


class ClaimDependencyProposal(BaseModel):
    """A dependency of the new claim on an EXISTING corpus claim (T4.1).

    The target must be a claim ID the model saw in the knowledge
    context; the host re-validates existence at the commit boundary.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: uuid.UUID
    kind: DependencyKind = DependencyKind.EVIDENTIAL


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2000)
    # T7.34 (ADR-0018): reverify of an EXISTING claim — the host-issued
    # id the model REFERENCEs (never mints), as seen in the knowledge
    # context line `[c:<uuid>]`. String (not UUID) on purpose: the
    # engine schema profile (ADR-0012) strips `format`, and the model
    # has truncated UUIDs (EVAL-4d pack 7) — the host resolves a full
    # UUID or a UNIQUE prefix among the session-visible claims,
    # fail-closed. When set, the op attaches to that claim: no new
    # claim row, the statement is a restatement (audit-only), and the
    # reverify record is the fresh assessment row (the session's
    # verification moment).
    existing_claim_id: str | None = Field(default=None, min_length=8, max_length=36)
    # ADR-0006 rev (cross-lingual search): the model renders the
    # statement in the other corpus language (MVP: English) so that
    # retrieval matches queries in either language. Search index only —
    # the knowledge text is always ``statement``.
    search_statements: list[str] = Field(
        default_factory=list,
        max_length=2,
        description="Англоязычные варианты формулировки (1–2), ТОЛЬКО для поиска",
    )
    claim_type: ClaimType
    scope: JsonDict = Field(default_factory=dict)
    as_of: datetime | None = None
    dependencies: list[ClaimDependencyProposal] = Field(
        default_factory=list, max_length=MAX_DEPENDENCIES_PER_CLAIM
    )


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
            for k, alt in enumerate(claim.search_statements):
                if not alt.strip():
                    problems.append(f"claim[{i}].search_statements[{k}]: empty")
                elif len(alt) > 300:
                    problems.append(f"claim[{i}].search_statements[{k}]: > 300 chars")
            seen: set[tuple[str, str]] = set()
            for j, dep in enumerate(claim.dependencies):
                key = (str(dep.claim_id), dep.kind.value)
                if key in seen:
                    problems.append(f"claim[{i}].dependencies[{j}]: duplicate {key[0][:8]}")
                seen.add(key)
        if len(self.new_questions) > questions_max:
            problems.append(f"new_questions: {len(self.new_questions)} > {questions_max}")
        return problems
