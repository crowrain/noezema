"""Security: sandbox path traversal, capability boundaries."""

import pytest

from packages.tool_broker.sandbox import SandboxExecutor, SandboxError


def test_path_traversal_blocked():
    sandbox = SandboxExecutor(workspace_dir="/tmp/test-sandbox-isolation")

    # Attempt to read outside workspace
    with pytest.raises(SandboxError, match="path traversal blocked"):
        sandbox.read_file("../../etc/passwd")

    with pytest.raises(SandboxError, match="path traversal blocked"):
        sandbox.read_file("../secret")

    # Attempt to write outside workspace
    with pytest.raises(SandboxError, match="path traversal blocked"):
        sandbox.write_file("../../etc/crontab", "malicious")

    with pytest.raises(SandboxError, match="path traversal blocked"):
        sandbox.list_dir("..")


def test_normal_operations_allowed():
    sandbox = SandboxExecutor(workspace_dir="/tmp/test-sandbox-normal")

    # Normal write
    written = sandbox.write_file("test.txt", "hello")
    assert written == 5

    # Normal read
    content = sandbox.read_file("test.txt")
    assert content == "hello"

    # Normal list
    entries = sandbox.list_dir(".")
    assert "test.txt" in entries
