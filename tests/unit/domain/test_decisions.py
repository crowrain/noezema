"""Tests for the untrusted LLM decision boundary."""

import json

import pytest
from pydantic import ValidationError

from packages.domain import CompleteDecision, DecisionEnvelope, ToolDecision, ToolName


def _tool_payload() -> dict[str, object]:
    return {
        "public_rationale": "Проверить локальный индекс",
        "expected_information": "Найти первичный источник",
        "decision": {
            "kind": "tool",
            "tool": "web.search",
            "arguments": {"query": "NOEZEMA"},
        },
    }


def test_tool_decision_parses_from_strict_json() -> None:
    envelope = DecisionEnvelope.model_validate_json(json.dumps(_tool_payload()))

    assert isinstance(envelope.decision, ToolDecision)
    assert envelope.decision.tool is ToolName.WEB_SEARCH


def test_complete_decision_does_not_require_expected_information() -> None:
    envelope = DecisionEnvelope.model_validate_json(
        json.dumps(
            {
                "public_rationale": "Исследование завершено",
                "decision": {"kind": "complete", "reason": "goal_reached"},
            }
        )
    )

    assert isinstance(envelope.decision, CompleteDecision)


def test_tool_decision_requires_expected_information() -> None:
    payload = _tool_payload()
    del payload["expected_information"]

    with pytest.raises(ValidationError, match="expected_information is required"):
        DecisionEnvelope.model_validate_json(json.dumps(payload))


@pytest.mark.parametrize("forbidden_field", ["action_id", "idempotency_key", "model_run_id"])
def test_llm_cannot_supply_host_fields(forbidden_field: str) -> None:
    payload = _tool_payload()
    decision = payload["decision"]
    assert isinstance(decision, dict)
    decision[forbidden_field] = "00000000-0000-0000-0000-000000000000"

    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        DecisionEnvelope.model_validate_json(json.dumps(payload))


def test_unknown_tool_is_rejected() -> None:
    payload = _tool_payload()
    decision = payload["decision"]
    assert isinstance(decision, dict)
    decision["tool"] = "host.execute"

    with pytest.raises(ValidationError):
        DecisionEnvelope.model_validate_json(json.dumps(payload))


def test_decision_json_schema_does_not_expose_host_fields() -> None:
    schema = json.dumps(DecisionEnvelope.model_json_schema(), sort_keys=True)

    assert "action_id" not in schema
    assert "idempotency_key" not in schema
    assert "model_run_id" not in schema
