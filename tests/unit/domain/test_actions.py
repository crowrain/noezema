"""Tests for trusted action binding and retry semantics."""

import pytest
from pydantic import ValidationError

from packages.domain import (
    BoundAction,
    IdempotencyClass,
    ModelRunId,
    SessionId,
    ToolDecision,
    ToolName,
    bind_action,
    canonical_json_sha256,
)


def _decision(tool: ToolName, arguments: dict[str, object] | None = None) -> ToolDecision:
    return ToolDecision(kind="tool", tool=tool, arguments=arguments or {})


def test_canonical_hash_is_independent_of_object_key_order() -> None:
    assert canonical_json_sha256({"b": 2, "a": 1}) == canonical_json_sha256({"a": 1, "b": 2})


@pytest.mark.parametrize("tool", list(ToolName))
def test_every_tool_has_trusted_retry_semantics(tool: ToolName) -> None:
    action = bind_action(
        session_id=SessionId.new(),
        model_run_id=ModelRunId.new(),
        decision=_decision(tool),
    )

    assert isinstance(action.idempotency_class, IdempotencyClass)


@pytest.mark.parametrize(
    ("tool", "expected_class"),
    [
        (ToolName.WORKSPACE_READ, IdempotencyClass.PURE),
        (ToolName.WORKSPACE_WRITE, IdempotencyClass.IDEMPOTENT),
        (ToolName.WEB_SEARCH, IdempotencyClass.OBSERVATION),
        (ToolName.SHELL_EXECUTE, IdempotencyClass.NON_IDEMPOTENT),
    ],
)
def test_broker_assigns_retry_semantics(tool: ToolName, expected_class: IdempotencyClass) -> None:
    action = bind_action(
        session_id=SessionId.new(),
        model_run_id=ModelRunId.new(),
        decision=_decision(tool, {"path": "notes.txt"}),
    )

    assert action.idempotency_class is expected_class
    assert action.arguments_sha256 == canonical_json_sha256(action.arguments)


def test_action_and_idempotency_ids_are_generated_per_binding() -> None:
    session_id = SessionId.new()
    model_run_id = ModelRunId.new()
    decision = _decision(ToolName.MEMORY_SEARCH, {"query": "test"})

    first = bind_action(session_id=session_id, model_run_id=model_run_id, decision=decision)
    second = bind_action(session_id=session_id, model_run_id=model_run_id, decision=decision)

    assert first.action_id != second.action_id
    assert first.idempotency_key != second.idempotency_key


def test_bound_arguments_cannot_be_mutated_through_a_returned_view() -> None:
    action = bind_action(
        session_id=SessionId.new(),
        model_run_id=ModelRunId.new(),
        decision=_decision(ToolName.WORKSPACE_WRITE, {"path": "notes.txt"}),
    )

    arguments_view = action.arguments
    arguments_view["path"] = "other.txt"

    assert action.arguments == {"path": "notes.txt"}
    assert action.arguments_sha256 == canonical_json_sha256(action.arguments)


def test_bound_action_rejects_argument_hash_mismatch() -> None:
    action = bind_action(
        session_id=SessionId.new(),
        model_run_id=ModelRunId.new(),
        decision=_decision(ToolName.QUESTION_CREATE, {"text": "why?"}),
    )

    invalid_payload = action.model_dump()
    invalid_payload["arguments_sha256"] = "0" * 64

    with pytest.raises(ValidationError, match="arguments_sha256 does not match"):
        BoundAction.model_validate(invalid_payload)
