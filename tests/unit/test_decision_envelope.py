"""Tests for the structured decision protocol (§7, T1.2)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.models.enums import CompleteReason, DecisionKind
from packages.domain.schemas.decision import Decision, ModelResponse


@pytest.mark.unit
def test_valid_tool_decision() -> None:
    d = Decision(kind=DecisionKind.TOOL, tool="web.search", arguments={"query": "spec"})
    assert not d.is_complete
    assert d.arguments == {"query": "spec"}


@pytest.mark.unit
def test_valid_complete_decision() -> None:
    d = Decision(kind=DecisionKind.COMPLETE, reason="goal_reached")
    assert d.is_complete
    assert d.normalized_reason is CompleteReason.GOAL_REACHED


@pytest.mark.unit
def test_complete_with_unknown_reason_stays_open() -> None:
    d = Decision(kind=DecisionKind.COMPLETE, reason="custom_host_reason")
    assert d.normalized_reason is None


@pytest.mark.unit
def test_normalized_reason_delegates_to_token_rule() -> None:
    """T7.49 (ADR-0022): the property recognizes an explicit token at the
    start of the reason (token + separator + suffix) and stays None on
    free text — the single host rule, no exact-match relic."""
    assert (
        Decision(kind=DecisionKind.COMPLETE, reason="goal_reached — утверждение: Go 1.27.1").normalized_reason
        is CompleteReason.GOAL_REACHED
    )
    assert Decision(kind=DecisionKind.COMPLETE, reason="GOAL_REACHED").normalized_reason is CompleteReason.GOAL_REACHED
    # free text with the token only inside: still None
    assert (
        Decision(kind=DecisionKind.COMPLETE, reason="Вопрос отвечен и подтверждён (goal_reached)").normalized_reason
        is None
    )
    # kind=tool never normalizes
    assert (
        Decision(kind=DecisionKind.TOOL, tool="web.search", reason=None).normalized_reason is None
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "payload",
    [
        {"kind": "tool"},  # missing tool
        {"kind": "tool", "tool": "web.search", "reason": "goal_reached"},  # tool + reason
        {"kind": "complete"},  # missing reason
        {"kind": "complete", "tool": "web.search", "reason": "goal_reached"},  # complete + tool
        {"kind": "complete", "reason": ""},  # empty reason
        {"kind": "bogus", "tool": "web.search"},  # unknown kind
        {"kind": "tool", "tool": "web search"},  # bad tool name (space)
        {"kind": "tool", "tool": "WebSearch"},  # bad tool name (case)
        {"kind": "tool", "tool": "noNamespace"},  # bad tool name (no dot)
        {"kind": "tool", "tool": "web.search", "arguments": "not-a-dict"},  # arguments not dict
    ],
)
def test_invalid_decisions_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Decision(**payload)  # type: ignore[arg-type]


@pytest.mark.unit
def test_envelope_requires_rationale() -> None:
    with pytest.raises(ValidationError):
        ModelResponse(public_rationale="", decision=Decision(kind=DecisionKind.COMPLETE, reason="goal_reached"))


@pytest.mark.unit
def test_envelope_full_example_from_spec() -> None:
    resp = ModelResponse.model_validate(
        {
            "public_rationale": "Проверить наличие официальной спецификации",
            "expected_information": "Первичный источник либо подтверждённое отсутствие",
            "decision": {
                "kind": "tool",
                "tool": "web.search",
                "arguments": {"query": "название технологии official specification"},
            },
        }
    )
    assert not resp.is_complete
    assert resp.decision.tool == "web.search"


@pytest.mark.unit
def test_envelope_rejects_host_owned_ids() -> None:
    """The LLM must not be able to supply dedup/idempotency ids (§20.10)."""
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            {
                "public_rationale": "x",
                "decision": {"kind": "complete", "reason": "goal_reached"},
                "idempotency_key": "llm-chosen",
            }
        )
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            {
                "public_rationale": "x",
                "decision": {
                    "kind": "tool",
                    "tool": "web.search",
                    "arguments": {},
                    "action_id": "llm-chosen",
                },
            }
        )


@pytest.mark.unit
def test_envelope_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ModelResponse.model_validate(
            {
                "public_rationale": "x",
                "decision": {"kind": "complete", "reason": "goal_reached"},
                "surprise": 1,
            }
        )


@pytest.mark.unit
def test_envelope_rationale_length_bounded() -> None:
    with pytest.raises(ValidationError):
        ModelResponse(
            public_rationale="a" * 4001,
            decision=Decision(kind=DecisionKind.COMPLETE, reason="goal_reached"),
        )
