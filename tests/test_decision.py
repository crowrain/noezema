"""Tests for decision schemas."""

from packages.domain.models.decision import ModelResponse, Decision


def test_tool_decision():
    resp = ModelResponse(
        public_rationale="Проверить наличие спецификации",
        decision=Decision(
            kind="tool",
            tool="web.search",
            arguments={"query": "specification"},
        ),
    )
    assert not resp.decision.is_complete
    assert resp.decision.tool == "web.search"


def test_complete_decision():
    resp = ModelResponse(
        public_rationale="План выполнен",
        decision=Decision(kind="complete", reason="goal_reached"),
    )
    assert resp.decision.is_complete
