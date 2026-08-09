"""Tests for bounded Explorer and evidence-only Curator protocols."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.cognition import (
    CuratorClaimProposal,
    CuratorContext,
    CuratorOutcome,
    CuratorProposal,
    CuratorProposalValidationError,
    EvidenceReference,
    EvidenceRelation,
    ExplorerContext,
    ExplorerDecisionValidationError,
    ObservationProvenance,
    PromptBundle,
    PromptKind,
    PromptSnapshot,
    ProtocolObservation,
    ProtocolQuestion,
    build_curator_request,
    build_explorer_request,
    validate_curator_proposal,
    validate_explorer_decision,
)
from packages.domain import DecisionEnvelope, ObservationId, QuestionId, ToolName
from packages.llm_gateway import ChatRole, ModelPhase, ModelRole

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _prompt(kind: PromptKind) -> PromptSnapshot:
    path = PROJECT_ROOT / "prompts" / f"{kind.value}.md"
    return PromptSnapshot.load(
        path,
        kind=kind,
        version=f"{kind.value}/v1",
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )


def _bundle(kind: PromptKind) -> PromptBundle:
    return PromptBundle(identity=_prompt(PromptKind.IDENTITY), role=_prompt(kind))


def _question() -> ProtocolQuestion:
    return ProtocolQuestion(id=QuestionId.new(), text="What is NOEZEMA?")


def _observation() -> ProtocolObservation:
    return ProtocolObservation(
        id=ObservationId.new(),
        kind="web_document",
        public_summary="The project documentation describes a local-first thinker.",
        payload_sha256="a" * 64,
        provenance=ObservationProvenance(
            tool=ToolName.WEB_FETCH,
            source="https://example.test/noezema",
            captured_at=datetime(2026, 8, 5, tzinfo=UTC),
        ),
    )


def test_explorer_request_has_one_canonical_context_and_exact_prompt_hash() -> None:
    observation = _observation()
    context = ExplorerContext(
        question=_question(),
        remaining_actions=3,
        allowed_tools=(ToolName.WEB_SEARCH, ToolName.WEB_FETCH),
        observations=(observation,),
    )
    bundle = _bundle(PromptKind.EXPLORER)

    request = build_explorer_request(
        context,
        prompts=bundle,
        policy_version="policy/v1",
    )

    assert request.role is ModelRole.EXPLORER
    assert request.phase is ModelPhase.EXPLORATION
    assert request.prompt_sha256 == bundle.sha256
    assert [message.role for message in request.messages] == [ChatRole.SYSTEM, ChatRole.USER]
    payload = json.loads(request.messages[1].content)
    assert payload["protocol"] == "explorer-input/v1"
    assert payload["context"]["question"]["id"] == str(context.question.id)
    assert request.messages[1].content == json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def test_request_builder_rejects_the_wrong_role_prompt() -> None:
    context = ExplorerContext(
        question=_question(),
        remaining_actions=0,
        allowed_tools=(),
    )

    with pytest.raises(ValueError, match="explorer prompt"):
        build_explorer_request(
            context,
            prompts=_bundle(PromptKind.CURATOR),
            policy_version="policy/v1",
        )


def test_explorer_tool_decision_is_fenced_by_budget_and_allowlist() -> None:
    decision = DecisionEnvelope.model_validate_json(
        json.dumps(
            {
                "public_rationale": "Search the durable memory first.",
                "expected_information": "Previously retained facts.",
                "decision": {
                    "kind": "tool",
                    "tool": "memory.search",
                    "arguments": {"query": "NOEZEMA"},
                },
            }
        )
    )
    exhausted = ExplorerContext(
        question=_question(),
        remaining_actions=0,
        allowed_tools=(ToolName.MEMORY_SEARCH,),
    )
    disallowed = ExplorerContext(
        question=exhausted.question,
        remaining_actions=1,
        allowed_tools=(ToolName.WEB_FETCH,),
    )

    with pytest.raises(ExplorerDecisionValidationError, match="action budget"):
        validate_explorer_decision(decision, context=exhausted)
    with pytest.raises(ExplorerDecisionValidationError, match="allowlist"):
        validate_explorer_decision(decision, context=disallowed)

    allowed = disallowed.model_copy(update={"allowed_tools": (ToolName.MEMORY_SEARCH,)})
    assert validate_explorer_decision(decision, context=allowed) is None


def test_curator_schema_cannot_assign_host_assessment_fields() -> None:
    properties = CuratorProposal.model_json_schema()["properties"]

    assert {"grade", "status", "confidence"}.isdisjoint(properties)
    with pytest.raises(ValidationError, match="confidence"):
        CuratorProposal.model_validate(
            {
                "public_summary": "No supported claim yet.",
                "outcome": "insufficient_evidence",
                "claims": [],
                "handoffs": [],
                "confidence": 0.5,
            }
        )


def test_curator_proposal_must_reference_available_evidence_and_allowed_types() -> None:
    available = _observation()
    context = CuratorContext(
        question=_question(),
        observations=(available,),
        allowed_claim_types=("fact",),
        remaining_claim_budget=2,
        remaining_evidence_link_budget=2,
        remaining_handoff_budget=1,
    )
    proposal = CuratorProposal(
        public_summary="One claim proposed.",
        outcome=CuratorOutcome.PROGRESS,
        claims=(
            CuratorClaimProposal(
                ref="claim_1",
                statement="NOEZEMA is local-first.",
                claim_type="hypothesis",
                topic="architecture",
                evidence=(
                    EvidenceReference(
                        observation_id=ObservationId.new(),
                        relation=EvidenceRelation.SUPPORTS,
                        scope="The overview sentence.",
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(CuratorProposalValidationError) as error:
        validate_curator_proposal(proposal, context=context)

    assert "disallowed claim type" in str(error.value)
    assert "unknown observation" in str(error.value)


def test_curator_proposal_respects_host_budgets() -> None:
    observation = _observation()
    context = CuratorContext(
        question=_question(),
        observations=(observation,),
        allowed_claim_types=("fact",),
        remaining_claim_budget=0,
        remaining_evidence_link_budget=0,
        remaining_handoff_budget=0,
    )
    proposal = CuratorProposal(
        public_summary="A proposal that exceeds the fenced budget.",
        outcome=CuratorOutcome.PROGRESS,
        claims=(
            CuratorClaimProposal(
                ref="claim_1",
                statement="NOEZEMA is local-first.",
                claim_type="fact",
                topic="architecture",
                evidence=(
                    EvidenceReference(
                        observation_id=observation.id,
                        relation=EvidenceRelation.SUPPORTS,
                        scope="The overview sentence.",
                    ),
                ),
            ),
        ),
    )

    with pytest.raises(CuratorProposalValidationError) as error:
        validate_curator_proposal(proposal, context=context)

    assert "claim budget exceeded" in str(error.value)
    assert "evidence-link budget exceeded" in str(error.value)


def test_curator_request_uses_consolidation_role() -> None:
    context = CuratorContext(
        question=_question(),
        observations=(),
        allowed_claim_types=("fact",),
        remaining_claim_budget=0,
        remaining_evidence_link_budget=0,
        remaining_handoff_budget=1,
    )

    request = build_curator_request(
        context,
        prompts=_bundle(PromptKind.CURATOR),
        policy_version="policy/v1",
    )

    assert request.role is ModelRole.CURATOR
    assert request.phase is ModelPhase.CONSOLIDATION
    assert json.loads(request.messages[1].content)["protocol"] == "curator-input/v1"
