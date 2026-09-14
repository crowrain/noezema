"""Tests for the tool registry (T2.6, §5.7)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from packages.domain.models.enums import IdempotencyClass
from packages.policy.profiles import load_profile
from packages.policy.tools import all_tools, get_tool, model_tools_schema


@pytest.mark.unit
def test_registry_classes_match_spec() -> None:
    expected = {
        "workspace.read": IdempotencyClass.PURE,
        "workspace.list": IdempotencyClass.PURE,
        "workspace.write": IdempotencyClass.IDEMPOTENT,
        "python.execute": IdempotencyClass.NON_IDEMPOTENT,
        "shell.execute": IdempotencyClass.NON_IDEMPOTENT,
        "memory.search": IdempotencyClass.PURE,
        "question.create": IdempotencyClass.IDEMPOTENT,
        "message.reply": IdempotencyClass.IDEMPOTENT,
    }
    for name, klass in expected.items():
        spec = get_tool(name)
        assert spec is not None, name
        assert spec.idempotency_class is klass, name


@pytest.mark.unit
def test_unknown_tool_is_none() -> None:
    assert get_tool("web.fetch") is None
    assert get_tool("net.fetch") is None


@pytest.mark.unit
def test_model_schema_only_profile_tools() -> None:
    profile = load_profile("sealed")
    schema = model_tools_schema(profile)
    names = {t["name"] for t in schema["tools"]}
    # everything in the schema is allowed by the profile
    assert names <= set(profile.tools)
    # and only allowed tools with a registry spec appear
    assert "shell.execute" in names
    assert "web.fetch" not in names
    for t in schema["tools"]:
        assert "properties" in t["arguments"] or t["arguments"]["properties"] == {}


@pytest.mark.unit
def test_argument_schemas_forbid_extras() -> None:
    spec = get_tool("workspace.write")
    assert spec is not None
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({"path": "x", "content": "y", "injected": 1})
    ok = spec.args_model.model_validate({"path": "x", "content": "y"})
    assert ok.path == "x"


@pytest.mark.unit
def test_all_tools_sorted_and_complete() -> None:
    names = [t.name for t in all_tools()]
    assert names == sorted(names)
    assert len(names) == len(set(names))
