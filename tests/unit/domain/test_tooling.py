"""Capability and typed Tool Broker observation contract tests."""

import hashlib
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import (
    ActionId,
    CapabilityPolicy,
    EvidenceKind,
    PythonExecuteArguments,
    SandboxProfile,
    ToolExecutionObservation,
    ToolName,
    WorkspaceListArguments,
    WorkspaceReadArguments,
    WorkspaceReadPayload,
    capability_policy_sha256,
    sandbox_mvp_capability_policy,
    sealed_mvp_capability_policy,
    workspace_read_as_source_observation,
)

NOW = datetime(2026, 8, 11, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize("path", ["../secret.txt", "/etc/passwd", "C:\\Windows\\win.ini"])
def test_workspace_argument_paths_must_be_relative_and_confined(path: str) -> None:
    with pytest.raises(ValidationError):
        WorkspaceReadArguments(path=path)


def test_workspace_paths_are_normalized_before_execution() -> None:
    arguments = WorkspaceListArguments(path="notes\\daily", recursive=True)

    assert arguments.path == "notes/daily"


def test_sealed_policy_exposes_only_read_only_mvp_tools() -> None:
    policy = sealed_mvp_capability_policy()

    assert set(policy.allowed_tools) == {
        ToolName.WORKSPACE_LIST,
        ToolName.WORKSPACE_READ,
        ToolName.MEMORY_SEARCH,
    }
    assert capability_policy_sha256(policy) == capability_policy_sha256(
        sealed_mvp_capability_policy()
    )


def test_sandbox_image_must_be_pinned_by_digest() -> None:
    with pytest.raises(ValidationError):
        SandboxProfile(image="python:latest")


def test_execution_tools_require_an_explicit_sandbox_profile() -> None:
    with pytest.raises(ValidationError, match="require a pinned sandbox"):
        CapabilityPolicy(
            version="invalid/v1",
            allowed_tools=(ToolName.SHELL_EXECUTE,),
        )


def test_sandbox_policy_enables_only_local_networkless_computation() -> None:
    sandbox = SandboxProfile(image=f"localhost/noezema-sandbox@sha256:{'a' * 64}")
    policy = sandbox_mvp_capability_policy(sandbox=sandbox)

    assert policy.sandbox == sandbox
    assert ToolName.SHELL_EXECUTE in policy.allowed_tools
    assert ToolName.PYTHON_EXECUTE in policy.allowed_tools
    assert ToolName.WEB_FETCH not in policy.allowed_tools


def test_python_source_whitespace_is_part_of_the_bound_program() -> None:
    code = "if True:\n    print('preserved')\n"

    assert PythonExecuteArguments(code=code).code == code


def test_workspace_read_payload_is_bound_to_exact_utf8_content() -> None:
    with pytest.raises(ValidationError, match="content_sha256"):
        WorkspaceReadPayload(
            path="facts.txt",
            size_bytes=5,
            content_sha256="0" * 64,
            content="facts",
        )


def test_exact_workspace_read_can_become_source_evidence() -> None:
    action_id = ActionId.new()
    payload = WorkspaceReadPayload(
        path="facts.txt",
        size_bytes=5,
        content_sha256=hashlib.sha256(b"facts").hexdigest(),
        content="facts",
    )
    observation = ToolExecutionObservation.build(
        action_id=action_id,
        tool=ToolName.WORKSPACE_READ,
        source="workspace:facts.txt",
        captured_at=NOW,
        payload=payload,
    )

    source = workspace_read_as_source_observation(observation)
    replayed = workspace_read_as_source_observation(observation)

    assert source.kind is EvidenceKind.SOURCE_ASSERTION
    assert source.id == observation.id
    assert source.provenance.action_id == action_id
    assert source.source_content_sha256 == payload.content_sha256
    assert source.source_id == replayed.source_id
    assert source.chunk_id == replayed.chunk_id
