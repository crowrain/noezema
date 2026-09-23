"""Tests for the curator staging proposal schema (T1.13)."""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest
from pydantic import ValidationError

from packages.domain.models.enums import ClaimType, DependencyKind, EvidenceRelation, QuestionOrigin
from packages.domain.schemas.staging import (
    MAX_DEPENDENCIES_PER_CLAIM,
    ClaimDependencyProposal,
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


# ─── reverify of an existing claim (T7.34, ADR-0018) ────────────────────────


def test_reverify_field_defaults_none() -> None:
    c = ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL)
    assert c.existing_claim_id is None
    p = CuratorProposal(summary="s", claims=[c])
    assert p.validate_against(evidence_count=0) == []


def test_reverify_field_accepts_uuid_and_prefix() -> None:
    c = ClaimProposal(
        statement="факт снова проверен",
        claim_type=ClaimType.EXTERNAL_FACT,
        existing_claim_id=uuid.uuid4().hex[:16],
    )
    assert c.existing_claim_id is not None
    c2 = ClaimProposal(
        statement="факт снова проверен",
        claim_type=ClaimType.EXTERNAL_FACT,
        existing_claim_id=str(uuid.uuid4()),
    )
    assert c2.existing_claim_id is not None


def test_reverify_field_rejects_too_short() -> None:
    # 7 hex chars — too short to be a reference (the resolver's floor
    # is 8; the schema floor keeps garbage out before the host)
    with pytest.raises(ValidationError):
        ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL, existing_claim_id="6e70379")
    with pytest.raises(ValidationError):
        ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL, existing_claim_id="")


def test_reverify_field_rejects_too_long() -> None:
    with pytest.raises(ValidationError):
        ClaimProposal(
            statement="X",
            claim_type=ClaimType.SELF_MODEL,
            existing_claim_id="6e70379ee1f52284" * 3,
        )


def test_reverify_schema_is_halogen_compatible() -> None:
    """ADR-0012: the engine refuses `format`/`pattern`; the new field
    must not introduce them (anyOf string|null is accepted — ADR-0012 §1).
    The host validates the RESPONSE with the full pydantic model."""
    from packages.llm_gateway.schema_compat import strip_schema_keywords

    schema = CuratorProposal.model_json_schema()

    def keywords(node: object, path: str = "") -> list[str]:
        found: list[str] = []
        if isinstance(node, dict):
            for k, v in node.items():
                if k in ("format", "pattern"):
                    found.append(f"{path}/{k}")
                found.extend(keywords(v, f"{path}/{k}"))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                found.extend(keywords(v, f"{path}[{i}]"))
        return found

    stripped = strip_schema_keywords(schema, {"format", "pattern"})
    assert keywords(stripped) == []
    ecid = stripped["$defs"]["ClaimProposal"]["properties"]["existing_claim_id"]
    # string 8..36 or null — no uuid/format machinery
    assert ecid["anyOf"] == [
        {"type": "string", "minLength": 8, "maxLength": 36},
        {"type": "null"},
    ]


# ─── claim dependencies (T4.1) ──────────────────────────────────────────────


def test_dependencies_default_empty() -> None:
    c = ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL)
    assert c.dependencies == []


def test_dependency_valid_kinds() -> None:
    target = uuid.uuid4()
    c = ClaimProposal(
        statement="X",
        claim_type=ClaimType.SELF_MODEL,
        dependencies=[
            ClaimDependencyProposal(claim_id=target),  # evidential default
            ClaimDependencyProposal(claim_id=target, kind=DependencyKind.RESEARCH),
        ],
    )
    assert c.dependencies[0].kind is DependencyKind.EVIDENTIAL
    assert c.dependencies[1].kind is DependencyKind.RESEARCH
    p = CuratorProposal(summary="s", claims=[c])
    assert p.validate_against(evidence_count=0) == []


def test_dependency_closed_kind() -> None:
    with pytest.raises(ValidationError):
        ClaimDependencyProposal(claim_id=uuid.uuid4(), kind="friend")


def test_dependency_requires_uuid() -> None:
    with pytest.raises(ValidationError):
        ClaimDependencyProposal(claim_id="not-a-uuid")


def test_dependency_extra_field_rejected() -> None:
    with pytest.raises(ValidationError):
        ClaimDependencyProposal(claim_id=uuid.uuid4(), grade="E4")


def test_dependency_budget_enforced() -> None:
    deps = [ClaimDependencyProposal(claim_id=uuid.uuid4()) for _ in range(MAX_DEPENDENCIES_PER_CLAIM + 1)]
    with pytest.raises(ValidationError):
        ClaimProposal(statement="X", claim_type=ClaimType.SELF_MODEL, dependencies=deps)


def test_duplicate_dependencies_flagged() -> None:
    target = uuid.uuid4()
    c = ClaimProposal(
        statement="X",
        claim_type=ClaimType.SELF_MODEL,
        dependencies=[
            ClaimDependencyProposal(claim_id=target),
            ClaimDependencyProposal(claim_id=target),
        ],
    )
    p = CuratorProposal(summary="s", claims=[c])
    assert any("duplicate" in x for x in p.validate_against(evidence_count=0))
