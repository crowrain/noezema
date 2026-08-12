"""Opt-in execution against a real rootless OCI runtime."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from packages.domain import ActionId, PythonExecuteArguments, SandboxProfile, ToolName
from packages.tool_broker import OciSandboxRunner

RUNTIME = os.environ.get("NOEZEMA_TEST_SANDBOX_RUNTIME")
IMAGE = os.environ.get("NOEZEMA_TEST_SANDBOX_IMAGE")

pytestmark = pytest.mark.sandbox_integration


@pytest.mark.skipif(
    not RUNTIME or not IMAGE,
    reason="rootless sandbox profile is not configured",
)
def test_pinned_image_runs_without_network_or_workspace_write(tmp_path: Path) -> None:
    assert RUNTIME in {"podman", "docker"}
    assert IMAGE is not None
    input_path = tmp_path / "input.txt"
    input_path.write_text("local input", encoding="utf-8")
    runner = OciSandboxRunner(
        profile=SandboxProfile(runtime=RUNTIME, image=IMAGE),
        workspace_root=tmp_path,
    )

    result = runner.execute(
        action_id=ActionId.new(),
        session_id="sandbox-integration",
        tool=ToolName.PYTHON_EXECUTE,
        arguments=PythonExecuteArguments(
            code=(
                "from pathlib import Path\n"
                "print(Path('/workspace/input.txt').read_text())\n"
                "try:\n"
                "    Path('/workspace/forbidden.txt').write_text('no')\n"
                "except OSError:\n"
                "    print('workspace-read-only')\n"
            )
        ),
        timeout_ms=10_000,
    )

    assert result.captured.exit_code == 0
    assert result.captured.stdout == "local input\nworkspace-read-only\n"
    assert not (tmp_path / "forbidden.txt").exists()
    assert result.environment.rootless is True
    assert result.environment.network == "none"
