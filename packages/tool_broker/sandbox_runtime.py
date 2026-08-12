"""Rootless, networkless OCI runtime boundary for untrusted local computation."""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from packages.domain import (
    ActionId,
    PythonExecuteArguments,
    SandboxEnvironmentManifest,
    SandboxProfile,
    ShellExecuteArguments,
    ToolName,
)
from packages.tool_broker.errors import ToolExecutionError

SandboxArguments = ShellExecuteArguments | PythonExecuteArguments


@dataclass(frozen=True, slots=True)
class CapturedCommand:
    exit_code: int
    stdout: str
    stderr: str
    stdout_bytes: int
    stderr_bytes: int
    stdout_sha256: str
    stderr_sha256: str
    stdout_truncated: bool
    stderr_truncated: bool
    duration_ms: int


class CommandTransportTimeout(TimeoutError):
    """The OCI CLI did not finish before the trusted host deadline."""


class CommandTransport(Protocol):
    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        max_output_bytes: int,
    ) -> CapturedCommand: ...


class SubprocessCommandTransport:
    """Invoke an OCI CLI by argv only; never parse a command through a host shell."""

    def run(
        self,
        argv: Sequence[str],
        *,
        timeout_ms: int,
        max_output_bytes: int,
    ) -> CapturedCommand:
        started = time.monotonic()
        try:
            process = subprocess.Popen(  # noqa: S603 - closed executable and argv boundary
                list(argv),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                shell=False,
                close_fds=True,
            )
        except OSError as exc:
            raise ToolExecutionError(
                "sandbox_runtime_unavailable",
                "The configured OCI runtime could not be started.",
                retryable=False,
            ) from exc
        assert process.stdout is not None
        assert process.stderr is not None
        stdout_collector = _StreamCollector(max_output_bytes)
        stderr_collector = _StreamCollector(max_output_bytes)
        stdout_thread = threading.Thread(
            target=stdout_collector.drain,
            args=(process.stdout,),
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=stderr_collector.drain,
            args=(process.stderr,),
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            exit_code = process.wait(timeout=timeout_ms / 1000)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=5)
            stdout_thread.join(timeout=5)
            stderr_thread.join(timeout=5)
            raise CommandTransportTimeout from exc
        stdout_thread.join(timeout=5)
        stderr_thread.join(timeout=5)
        if stdout_thread.is_alive() or stderr_thread.is_alive():
            raise ToolExecutionError(
                "sandbox_output_capture_failed",
                "The OCI runtime did not close its output streams.",
                retryable=False,
                outcome_known=False,
            )
        duration_ms = max(0, round((time.monotonic() - started) * 1000))
        stdout = stdout_collector.result()
        stderr = stderr_collector.result()
        return CapturedCommand(
            exit_code=exit_code,
            stdout=stdout.text,
            stderr=stderr.text,
            stdout_bytes=stdout.total_bytes,
            stderr_bytes=stderr.total_bytes,
            stdout_sha256=stdout.sha256,
            stderr_sha256=stderr.sha256,
            stdout_truncated=stdout.truncated,
            stderr_truncated=stderr.truncated,
            duration_ms=duration_ms,
        )


@dataclass(frozen=True, slots=True)
class _CapturedFile:
    text: str
    total_bytes: int
    sha256: str
    truncated: bool


class _StreamCollector:
    def __init__(self, limit: int) -> None:
        self._limit = limit
        self._digest = hashlib.sha256()
        self._prefix = bytearray()
        self._total = 0

    def drain(self, stream: object) -> None:
        while True:
            chunk = stream.read(65_536)  # type: ignore[attr-defined]
            if not chunk:
                return
            self._digest.update(chunk)
            self._total += len(chunk)
            if len(self._prefix) < self._limit:
                self._prefix.extend(chunk[: self._limit - len(self._prefix)])

    def result(self) -> _CapturedFile:
        return _CapturedFile(
            text=bytes(self._prefix).decode("utf-8", errors="replace"),
            total_bytes=self._total,
            sha256=self._digest.hexdigest(),
            truncated=self._total > self._limit,
        )


@dataclass(frozen=True, slots=True)
class SandboxProcessResult:
    captured: CapturedCommand
    environment: SandboxEnvironmentManifest
    workspace_bytes_before: int
    workspace_bytes_after: int
    workspace_quota_exceeded: bool


class SandboxRunner(Protocol):
    profile: SandboxProfile

    def execute(
        self,
        *,
        action_id: ActionId,
        session_id: str,
        tool: ToolName,
        arguments: SandboxArguments,
        timeout_ms: int,
        workspace_root: Path | None = None,
    ) -> SandboxProcessResult: ...


class OciSandboxRunner:
    """Run each untrusted action in a fresh, digest-pinned rootless OCI container."""

    def __init__(
        self,
        *,
        profile: SandboxProfile,
        workspace_root: Path,
        transport: CommandTransport | None = None,
    ) -> None:
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        if any(character in str(root) for character in (",", "\r", "\n")):
            raise ValueError("workspace_root contains characters unsafe for an OCI mount argument")
        self.profile = profile
        self.workspace_root = root
        self._transport = transport or SubprocessCommandTransport()

    def execute(
        self,
        *,
        action_id: ActionId,
        session_id: str,
        tool: ToolName,
        arguments: SandboxArguments,
        timeout_ms: int,
        workspace_root: Path | None = None,
    ) -> SandboxProcessResult:
        execution_root = self._validate_workspace_root(workspace_root or self.workspace_root)
        runtime_version = self._verify_rootless_runtime()
        workdir = (execution_root / arguments.cwd).resolve(strict=False)
        if not workdir.is_relative_to(execution_root) or not workdir.is_dir():
            raise ToolExecutionError(
                "sandbox_workdir_not_found",
                "The requested sandbox working directory does not exist.",
                retryable=False,
            )
        workspace_before = self._workspace_size(execution_root)
        if workspace_before > self.profile.workspace_max_bytes:
            raise ToolExecutionError(
                "workspace_quota_exceeded",
                "The workspace already exceeds the sandbox quota.",
                retryable=False,
            )

        effective_timeout = min(timeout_ms, arguments.timeout_ms or timeout_ms)
        container_name = f"noezema-{session_id[:12]}-{str(action_id)[:12]}"
        argv = self.build_run_argv(
            container_name=container_name,
            session_id=session_id,
            tool=tool,
            arguments=arguments,
            workspace_root=execution_root,
        )
        try:
            captured = self._transport.run(
                argv,
                timeout_ms=effective_timeout,
                max_output_bytes=self.profile.max_output_bytes,
            )
        except CommandTransportTimeout as exc:
            self._force_remove(container_name)
            raise ToolExecutionError(
                "sandbox_timeout",
                "The sandbox action exceeded its trusted-host deadline.",
                retryable=False,
                outcome_known=False,
            ) from exc

        if captured.exit_code == 125:
            self._force_remove(container_name)
            raise ToolExecutionError(
                "sandbox_launch_failed",
                "The OCI runtime failed before returning a reliable command outcome.",
                retryable=False,
                outcome_known=False,
            )
        workspace_after = self._workspace_size(execution_root)
        return SandboxProcessResult(
            captured=captured,
            environment=SandboxEnvironmentManifest.from_profile(
                self.profile,
                runtime_version=runtime_version,
            ),
            workspace_bytes_before=workspace_before,
            workspace_bytes_after=workspace_after,
            workspace_quota_exceeded=workspace_after > self.profile.workspace_max_bytes,
        )

    def build_run_argv(
        self,
        *,
        container_name: str,
        session_id: str,
        tool: ToolName,
        arguments: SandboxArguments,
        workspace_root: Path | None = None,
    ) -> tuple[str, ...]:
        """Build the complete security boundary as argv, never as host-shell text."""

        if tool is ToolName.SHELL_EXECUTE and isinstance(arguments, ShellExecuteArguments):
            command = ("/bin/sh", "-eu", "-c", arguments.command)
        elif tool is ToolName.PYTHON_EXECUTE and isinstance(arguments, PythonExecuteArguments):
            command = ("python3", "-I", "-B", "-c", arguments.code)
        else:
            raise ValueError("sandbox tool and argument schema do not match")

        cpu_value = f"{self.profile.cpu_millis / 1000:.3f}".rstrip("0").rstrip(".")
        container_workdir = "/workspace"
        if arguments.cwd != ".":
            container_workdir = f"/workspace/{arguments.cwd}"
        execution_root = self._validate_workspace_root(workspace_root or self.workspace_root)
        mount = f"type=bind,source={execution_root},target=/workspace,readonly"
        return (
            self.profile.runtime,
            "run",
            "--rm",
            f"--name={container_name}",
            "--pull=never",
            "--label=noezema.managed=true",
            f"--label=noezema.session_id={session_id}",
            "--network=none",
            "--ipc=none",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            f"--user={self.profile.user_id}:{self.profile.group_id}",
            f"--cpus={cpu_value}",
            f"--memory={self.profile.memory_bytes}",
            f"--pids-limit={self.profile.pids_limit}",
            f"--tmpfs=/tmp:rw,nosuid,nodev,noexec,size={self.profile.tmpfs_bytes}",
            "--ulimit=nofile=64:64",
            f"--ulimit=fsize={self.profile.workspace_max_bytes}:{self.profile.workspace_max_bytes}",
            f"--mount={mount}",
            f"--workdir={container_workdir}",
            "--env=HOME=/tmp",
            "--env=TMPDIR=/tmp",
            "--env=PYTHONDONTWRITEBYTECODE=1",
            "--env=LC_ALL=C.UTF-8",
            self.profile.image,
            *command,
        )

    def _verify_rootless_runtime(self) -> str:
        if self.profile.runtime == "podman":
            argv = ("podman", "info", "--format=json")
        else:
            argv = ("docker", "info", "--format=json")
        try:
            result = self._transport.run(argv, timeout_ms=5_000, max_output_bytes=65_536)
        except CommandTransportTimeout as exc:
            raise ToolExecutionError(
                "sandbox_runtime_unavailable",
                "The OCI runtime security check timed out.",
                retryable=False,
            ) from exc
        if result.exit_code != 0 or result.stdout_truncated:
            raise ToolExecutionError(
                "sandbox_runtime_unavailable",
                "The OCI runtime did not return a complete security profile.",
                retryable=False,
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            raise ToolExecutionError(
                "sandbox_runtime_invalid_info",
                "The OCI runtime returned an invalid security profile.",
                retryable=False,
            ) from exc
        if not _reports_rootless(self.profile.runtime, payload):
            raise ToolExecutionError(
                "sandbox_runtime_not_rootless",
                "The configured OCI runtime is not running rootless.",
                retryable=False,
            )
        version = _runtime_version(self.profile.runtime, payload)
        if version is None:
            raise ToolExecutionError(
                "sandbox_runtime_invalid_info",
                "The OCI runtime did not report a bounded version fingerprint.",
                retryable=False,
            )
        return version

    @staticmethod
    def _validate_workspace_root(workspace_root: Path) -> Path:
        root = workspace_root.resolve(strict=True)
        if not root.is_dir():
            raise ValueError("workspace_root must be an existing directory")
        if any(character in str(root) for character in (",", "\r", "\n")):
            raise ValueError("workspace_root contains characters unsafe for an OCI mount argument")
        return root

    def _workspace_size(self, workspace_root: Path) -> int:
        total = 0
        pending = [workspace_root]
        while pending:
            directory = pending.pop()
            try:
                entries = list(os.scandir(directory))
            except OSError as exc:
                raise ToolExecutionError(
                    "workspace_inventory_failed",
                    "The workspace could not be inventoried before sandbox execution.",
                    retryable=False,
                ) from exc
            for entry in entries:
                try:
                    mode = entry.stat(follow_symlinks=False).st_mode
                except OSError as exc:
                    raise ToolExecutionError(
                        "workspace_inventory_failed",
                        "A workspace entry could not be inspected safely.",
                        retryable=False,
                    ) from exc
                if stat.S_ISDIR(mode):
                    pending.append(Path(entry.path))
                elif stat.S_ISREG(mode):
                    total += entry.stat(follow_symlinks=False).st_size
                else:
                    raise ToolExecutionError(
                        "workspace_special_file_forbidden",
                        "Symlinks, sockets and other special files are forbidden in a sandbox mount.",
                        retryable=False,
                    )
        return total

    def _force_remove(self, container_name: str) -> None:
        try:
            self._transport.run(
                (self.profile.runtime, "rm", "--force", container_name),
                timeout_ms=5_000,
                max_output_bytes=16_384,
            )
        except (CommandTransportTimeout, ToolExecutionError):
            pass


def _reports_rootless(runtime: str, payload: object) -> bool:
    if runtime == "docker":
        if not isinstance(payload, dict):
            return False
        options = payload.get("SecurityOptions") or payload.get("securityOptions")
        return isinstance(options, list) and any(
            "rootless" in str(item).casefold() for item in options
        )
    if not isinstance(payload, dict):
        return False
    host = payload.get("host") or payload.get("Host")
    if not isinstance(host, dict):
        return False
    security = host.get("security") or host.get("Security")
    if not isinstance(security, dict):
        return False
    return security.get("rootless") is True or security.get("Rootless") is True


def _runtime_version(runtime: str, payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    if runtime == "docker":
        value = payload.get("ServerVersion") or payload.get("serverVersion")
    else:
        version = payload.get("version") or payload.get("Version")
        value = None
        if isinstance(version, dict):
            value = version.get("Version") or version.get("version")
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    return normalized
