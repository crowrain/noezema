"""Tests for the curator staging proposal schema (T1.13)."""

from __future__ import annotations

from datetime import datetime

import pytest
from pydantic import ValidationError

from packages.domain.models.enums import ClaimType, EvidenceRelation, QuestionOrigin
from packages.domain.schemas.staging import (
    ClaimProposal,
    CuratorProposal,
    EvidenceLink,
    QuestionProposal,
)


def _link(evidence_index: int, claim_index: int) -> EvidenceLink:
    return EvidenceLink(evidence_index=evidence_index, claim_index=claim_index, relation=EvidenceRelation.SUPPORTS)


def test_valid_proposal_passes() -> None:
    p = CuratorProposal(
        summary="ok",
        claims=[
            ClaimProposal(statement="X", claim_type=ClaimType.EXTERNAL_FACT, scope={"obj": "x"}),
        ],
        evidence_links=[_link(0, 0)],
        new_questions=[QuestionProposal(text="почему?", origin=QuestionOrigin.CONFLICT)],
    )
    assert p.validate_against(evidence_count=2) == []


def test_out_of_range_evidence_index_flagged() -> None:
    p = CuratorProposal(
        summary="s",
        claims=[ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL)],
        evidence_links=[_link(5, 0)],
    )
    problems = p.validate_against(evidence_count=2)
    assert any("evidence_link" in x for x in problems)


def test_out_of_range_claim_index_flagged() -> None:
    p = CuratorProposal(
        summary="s",
        claims=[ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL)],
        evidence_links=[_link(0, 7)],
    )
    assert any("claim index" in x for x in p.validate_against(evidence_count=1))


def test_temporal_fact_requires_as_of() -> None:
    p = CuratorProposal(
        summary="s",
        claims=[ClaimProposal(statement="курс сегодня", claim_type=ClaimType.TEMPORAL_FACT)],
    )
    assert any("temporal_fact requires as_of" in x for x in p.validate_against(evidence_count=0))


def test_temporal_fact_with_as_of_ok() -> None:
    p2 = CuratorProposal(
        summary="s",
        claims=[
            ClaimProposal(
                statement="курс",
                claim_type=ClaimType.TEMPORAL_FACT,
                as_of=datetime(2026, 9, 14, 12, 0, 0),
            )
        ],
    )
    assert p2.validate_against(evidence_count=0) == []


def test_question_budget_enforced() -> None:
    qs = [QuestionProposal(text=f"q{i}", origin=QuestionOrigin.MODEL_PROPOSAL) for i in range(5)]
    p = CuratorProposal(summary="s", new_questions=qs)
    assert any("new_questions" in x for x in p.validate_against(evidence_count=0, questions_max=4))


def test_extra_fields_rejected() -> None:
    with pytest.raises(ValidationError):
        CuratorProposal.model_validate({"summary": "s", "grade": "E4"})
    with pytest.raises(ValidationError):
        ClaimProposal(statement="x", claim_type=ClaimType.EXTERNAL_FACT, confidence=0.9)


def test_relations_closed() -> None:
    with pytest.raises(ValidationError):
        EvidenceLink(evidence_index=0, claim_index=0, relation="neutral")
