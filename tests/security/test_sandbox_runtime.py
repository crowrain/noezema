"""Security invariants for the rootless OCI sandbox boundary."""

from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Sequence
from pathlib import Path

import pytest

from packages.domain import (
    ActionId,
    PythonExecuteArguments,
    SandboxProfile,
    ShellExecuteArguments,
    ToolName,
)
from packages.tool_broker import (
    CapturedCommand,
    CommandTransportTimeout,
    OciSandboxRunner,
    SubprocessCommandTransport,
    ToolExecutionError,
)


def _captured(
    *,
    stdout: str = "",
    stderr: str = "",
    exit_code: int = 0,
) -> CapturedCommand:
    stdout_bytes = stdout.encode()
    stderr_bytes = stderr.encode()
    return CapturedCommand(
        exit_code=exit_code,
        stdout=stdout,
        stderr=stderr,
        stdout_bytes=len(stdout_bytes),
        stderr_bytes=len(stderr_bytes),
        stdout_sha256=hashlib.sha256(stdout_bytes).hexdigest(),
        stderr_sha256=hashlib.sha256(stderr_bytes).hexdigest(),
        stdout_truncated=False,
        stderr_truncated=False,
        duration_ms=1,
    )


class _FakeTransport:
    def __init__(self, *, rootless: bool = True, timeout_run: bool = False) -> None:
        self.rootless = rootless
        self.timeout_run = timeout_run
        self.calls: list[tuple[str, ...]] = []

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        max_output_bytes: int,
    ) -> CapturedCommand:
        call = tuple(argv)
        self.calls.append(call)
        if len(call) > 1 and call[1] == "info":
            return _captured(
                stdout=json.dumps(
                    {
                        "host": {"security": {"rootless": self.rootless}},
                        "version": {"Version": "5.4.2"},
                    }
                )
            )
        if len(call) > 1 and call[1] == "rm":
            return _captured()
        if self.timeout_run:
            raise CommandTransportTimeout
        return _captured(stdout="sandbox output")


@pytest.fixture
def sandbox_profile() -> SandboxProfile:
    return SandboxProfile(
        image=f"localhost/noezema-sandbox@sha256:{'a' * 64}",
        cpu_millis=500,
        memory_bytes=134_217_728,
        pids_limit=32,
        tmpfs_bytes=8_388_608,
        workspace_max_bytes=16_777_216,
        max_output_bytes=4_096,
    )


def test_shell_command_is_only_an_inner_container_argument(
    tmp_path: Path,
    sandbox_profile: SandboxProfile,
) -> None:
    transport = _FakeTransport()
    runner = OciSandboxRunner(
        profile=sandbox_profile,
        workspace_root=tmp_path,
        transport=transport,
    )
    untrusted = "printf safe; uname -s"

    result = runner.execute(
        action_id=ActionId.new(),
        session_id="12345678-1234-1234-1234-123456789abc",
        tool=ToolName.SHELL_EXECUTE,
        arguments=ShellExecuteArguments(command=untrusted),
        timeout_ms=5_000,
    )

    assert result.captured.stdout == "sandbox output"
    argv = transport.calls[1]
    assert argv[0:2] == ("podman", "run")
    assert "--network=none" in argv
    assert "--ipc=none" in argv
    assert "--read-only" in argv
    assert "--cap-drop=ALL" in argv
    assert "--security-opt=no-new-privileges" in argv
    assert "--userns=keep-id:uid=65532,gid=65532" in argv
    assert "--pull=never" in argv
    assert any(item.startswith("--pids-limit=") for item in argv)
    assert any(item.startswith("--memory=") for item in argv)
    assert any(item.startswith("--mount=type=bind") for item in argv)
    assert any(item.endswith("target=/workspace,readonly") for item in argv)
    assert argv[-4:] == ("/bin/sh", "-eu", "-c", untrusted)
    assert argv.count(untrusted) == 1


def test_python_uses_isolated_mode_and_never_host_python(
    tmp_path: Path,
    sandbox_profile: SandboxProfile,
) -> None:
    runner = OciSandboxRunner(
        profile=sandbox_profile,
        workspace_root=tmp_path,
        transport=_FakeTransport(),
    )
    code = "print(6 * 7)"
    argv = runner.build_run_argv(
        container_name="noezema-test",
        session_id="session",
        tool=ToolName.PYTHON_EXECUTE,
        arguments=PythonExecuteArguments(code=code),
    )

    assert argv[-5:] == ("python3", "-I", "-B", "-c", code)
    assert sys.executable not in argv


def test_docker_profile_does_not_receive_podman_user_namespace_syntax(
    tmp_path: Path,
    sandbox_profile: SandboxProfile,
) -> None:
    docker_profile = sandbox_profile.model_copy(update={"runtime": "docker"})
    runner = OciSandboxRunner(
        profile=docker_profile,
        workspace_root=tmp_path,
        transport=_FakeTransport(),
    )

    argv = runner.build_run_argv(
        container_name="noezema-test",
        session_id="session",
        tool=ToolName.PYTHON_EXECUTE,
        arguments=PythonExecuteArguments(code="print('ok')"),
    )

    assert not any(item.startswith("--userns=keep-id") for item in argv)


def test_non_rootless_runtime_is_rejected_before_container_start(
    tmp_path: Path,
    sandbox_profile: SandboxProfile,
) -> None:
    transport = _FakeTransport(rootless=False)
    runner = OciSandboxRunner(
        profile=sandbox_profile,
        workspace_root=tmp_path,
        transport=transport,
    )

    with pytest.raises(ToolExecutionError) as raised:
        runner.execute(
            action_id=ActionId.new(),
            session_id="session",
            tool=ToolName.SHELL_EXECUTE,
            arguments=ShellExecuteArguments(command="true"),
            timeout_ms=5_000,
        )

    assert raised.value.code == "sandbox_runtime_not_rootless"
    assert len(transport.calls) == 1


def test_timeout_forces_container_removal_and_marks_outcome_unknown(
    tmp_path: Path,
    sandbox_profile: SandboxProfile,
) -> None:
    transport = _FakeTransport(timeout_run=True)
    runner = OciSandboxRunner(
        profile=sandbox_profile,
        workspace_root=tmp_path,
        transport=transport,
    )

    with pytest.raises(ToolExecutionError) as raised:
        runner.execute(
            action_id=ActionId.new(),
            session_id="session",
            tool=ToolName.SHELL_EXECUTE,
            arguments=ShellExecuteArguments(command="sleep 100"),
            timeout_ms=5,
        )

    assert raised.value.code == "sandbox_timeout"
    assert raised.value.outcome_known is False
    assert transport.calls[-1][0:3] == ("podman", "rm", "--force")


def test_subprocess_transport_hashes_full_output_but_returns_bounded_prefix() -> None:
    transport = SubprocessCommandTransport()

    result = transport.run(
        (sys.executable, "-c", "print('x' * 1000, end='')"),
        timeout_ms=5_000,
        max_output_bytes=32,
    )

    assert result.exit_code == 0
    assert result.stdout == "x" * 32
    assert result.stdout_bytes == 1000
    assert result.stdout_truncated is True
    assert result.stdout_sha256 == hashlib.sha256(b"x" * 1000).hexdigest()
