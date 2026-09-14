"""One-shot container sandbox runtime (T2.3, ADR-0002, §11).

A disposable single-use container per session:
  - network: none, read-only rootfs, cap-drop ALL, no-new-privileges;
  - /workspace is a per-session HOST directory bind-mounted rw, initialized
    from the read-only /base at container start (disposable: the host dir is
    destroyed with the session; the frozen manifest is the only survivor —
    PR #14). A host bind is used instead of a container tmpfs because
    docker-cp from tmpfs is broken on Docker 29 + the containerd store;
  - a scratch /tmp tmpfs makes the read-only rootfs usable by the tools;
  - hard CPU / memory / PID / timeout limits;
  - destroyed after the session (and on any failure path).

The engine is the `docker` CLI in dev and `podman` in prod — the command
verbs used here are identical for both.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict
from pydantic_settings import BaseSettings, SettingsConfigDict

WORKSPACE_PATH = "/workspace"
BASE_PATH = "/base"
# the sandbox user in sandbox/Containerfile (uid/gid 10001)
SANDBOX_UID = 10001
SANDBOX_GID = 10001
INIT_COMMAND = f"cp -a {BASE_PATH}/. {WORKSPACE_PATH}/ 2>/dev/null; exec sleep infinity"

_KNOWN_PROFILE_KEYS = frozenset(
    {
        "version",
        "name",
        "network",
        "read_only_rootfs",
        "capabilities",
        "no_new_privileges",
        "seccomp",
        "resource_limits",
        "tools",
        "paths",
        "secrets",
        "metadata_endpoints",
    }
)


class SandboxError(RuntimeError):
    """Base for sandbox failures."""


class SandboxHealthError(SandboxError):
    """The engine or image is not usable."""


class SandboxImageMissingError(SandboxHealthError):
    pass


class SandboxProfile(BaseModel):
    """Capability profile for the sandbox (T2.1/T2.4 input).

    Closed schema: unknown keys are rejected so a typo in a YAML cannot
    silently widen (or narrow) the profile.
    """

    model_config = ConfigDict(extra="forbid")

    version: str = "v1"
    name: str
    network: str = "none"
    read_only_rootfs: bool = True
    capabilities: list[str] = []
    no_new_privileges: bool = True
    seccomp: str = "default"
    resource_limits: dict[str, Any] = {
        "cpu": "1.0",
        "memory_mb": 512,
        "max_processes": 32,
        "timeout_seconds": 60,
        "max_file_size_mb": 50,
    }
    tools: dict[str, bool] = {}
    paths: dict[str, list[str]] = {"writable": [WORKSPACE_PATH], "readable": [BASE_PATH, WORKSPACE_PATH]}
    secrets: str = "deny"
    metadata_endpoints: str = "deny"

    @classmethod
    def from_yaml(cls, path: Path) -> SandboxProfile:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise SandboxError(f"profile {path} is not a mapping")
        unknown = set(raw) - _KNOWN_PROFILE_KEYS
        if unknown:
            raise SandboxError(f"profile {path} has unknown keys: {sorted(unknown)}")
        profile = cls.model_validate(raw)
        if profile.secrets != "deny":
            raise SandboxError(f"profile {path}: secrets must be 'deny' (§11.2)")
        if profile.metadata_endpoints != "deny":
            raise SandboxError(f"profile {path}: metadata endpoints must be 'deny'")
        return profile

    def tool_allowed(self, tool: str) -> bool:
        return self.tools.get(tool, False)

    @property
    def cpu(self) -> str:
        return str(self.resource_limits.get("cpu", "1.0"))

    @property
    def memory_mb(self) -> int:
        return int(self.resource_limits.get("memory_mb", 512))

    @property
    def max_processes(self) -> int:
        return int(self.resource_limits.get("max_processes", 32))

    @property
    def timeout_seconds(self) -> int:
        return int(self.resource_limits.get("timeout_seconds", 60))

    @property
    def max_file_size_mb(self) -> int:
        return int(self.resource_limits.get("max_file_size_mb", 50))

    @property
    def tmpfs_size_mb(self) -> int:
        """Workspace tmpfs cap: headroom over the single-file limit."""
        return max(64, self.max_file_size_mb * 4)


class SandboxSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NOEZEMA_SANDBOX_")

    image: str = "noezema-sandbox:dev"
    engine: str = "docker"  # or "podman" in prod
    work_root: Path = Path("/var/lib/noezema/sandbox")


@dataclass(frozen=True)
class ExecResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool = False


@dataclass(frozen=True)
class SandboxHandle:
    session_id: str
    container_name: str
    base_dir: Path
    workspace_dir: Path  # host destination for collect_workspace()
    profile: SandboxProfile


class ContainerSandboxRuntime:
    """Wraps the container engine CLI; one container per session."""

    MAX_OUTPUT = 1_000_000

    def __init__(
        self,
        image: str,
        engine: str = "docker",
        work_root: Path | None = None,
    ) -> None:
        self.image = image
        self.engine = engine
        self.work_root = work_root if work_root is not None else SandboxSettings().work_root
        self.work_root.mkdir(parents=True, exist_ok=True)

    # ── pure command builders (unit-tested) ───────────────────────────────

    @staticmethod
    def create_command(
        name: str,
        image: str,
        profile: SandboxProfile,
        base_path: Path,
        workspace_path: Path,
    ) -> list[str]:
        cmd = [
            "create",
            "--name",
            name,
            "--network",
            profile.network,
            "--cap-drop",
            "ALL",
            "--memory",
            f"{profile.memory_mb}m",
            "--cpus",
            profile.cpu,
            "--pids-limit",
            str(profile.max_processes),
            # scratch tmpfs so the read-only rootfs is usable by the tools
            "--tmpfs",
            "/tmp:rw,size=64m,mode=1777",
            # per-session writable workspace (disposable host dir; rw is the
            # default, so no mode suffix — --mount only accepts key=value)
            "--mount",
            f"type=bind,src={workspace_path},dst={WORKSPACE_PATH}",
            # read-only base, copied into the workspace at init
            "--mount",
            f"type=bind,src={base_path},dst={BASE_PATH},ro",
        ]
        if profile.read_only_rootfs:
            cmd += ["--read-only"]
        if profile.no_new_privileges:
            cmd += ["--security-opt", "no-new-privileges"]
        cmd += [image, "bash", "-c", INIT_COMMAND]
        return cmd

    @staticmethod
    def exec_command(name: str, command: list[str], timeout_seconds: float) -> list[str]:
        # timeout(1) inside the container: a host-side kill would not stop
        # the in-container process.
        return ["exec", name, "timeout", "-k", "5", str(timeout_seconds), *command]

    @staticmethod
    def destroy_command(name: str) -> list[str]:
        return ["rm", "-f", name]

    # ── engine ────────────────────────────────────────────────────────────

    async def _run(self, args: list[str], timeout: float = 120.0) -> tuple[int, str, str]:
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    self.engine,
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                ),
                timeout=timeout,
            )
            stdout, stderr = await proc.communicate()
        except TimeoutError as exc:
            raise SandboxError(f"engine command timed out: {' '.join(args[:2])}") from exc
        except FileNotFoundError as exc:
            raise SandboxHealthError(f"container engine not found: {self.engine}") from exc
        return (
            proc.returncode if proc.returncode is not None else -1,
            stdout.decode("utf-8", "replace")[: self.MAX_OUTPUT],
            stderr.decode("utf-8", "replace")[: self.MAX_OUTPUT],
        )

    async def health_check(self) -> None:
        """Engine reachable, image present, a one-shot container runs."""
        rc, _out, err = await self._run(["image", "inspect", self.image])
        if rc != 0:
            raise SandboxImageMissingError(f"image {self.image} not found: {err[:200]}")
        name = f"noezema-sb-hc-{uuid.uuid4().hex[:8]}"
        cmd = [
            "run", "--rm", "--name", name,
            "--network", "none", "--read-only", "--cap-drop", "ALL",
            "--security-opt", "no-new-privileges",
            "--tmpfs", "/tmp:rw,size=64m,mode=1777",
            self.image, "bash", "-c", "echo ok > /tmp/.health",
        ]
        rc, _out, err = await self._run(cmd, timeout=60)
        if rc != 0:
            raise SandboxHealthError(f"sandbox health check failed: {err[:300]}")

    async def start(
        self,
        session_id: str,
        base_workspace: Path | None,
        profile: SandboxProfile,
    ) -> SandboxHandle:
        sb_dir = self.work_root / session_id
        base_dir = sb_dir / "base"
        workspace_dir = sb_dir / "work"
        base_dir.mkdir(parents=True, exist_ok=True)
        workspace_dir.mkdir(parents=True, exist_ok=True)
        if base_workspace is not None and base_workspace.exists():
            shutil.copytree(base_workspace, base_dir, dirs_exist_ok=True)

        # the in-container user (uid 10001) must be able to write the
        # workspace bind; chown when possible, else make it world-writable
        # (the dir is disposable and destroyed with the session)
        try:
            os.chown(workspace_dir, SANDBOX_UID, SANDBOX_GID)
        except PermissionError:
            os.chmod(workspace_dir, 0o777)

        name = f"noezema-sb-{session_id.replace('-', '')[:12]}"
        cmd = self.create_command(name, self.image, profile, base_dir, workspace_dir)
        rc, _out, err = await self._run(cmd, timeout=60)
        if rc != 0:
            raise SandboxError(f"failed to create sandbox container: {err[:300]}")
        rc, _out, err = await self._run(["start", name], timeout=60)
        if rc != 0:
            await self._run(self.destroy_command(name), timeout=30.0)
            raise SandboxError(f"failed to start sandbox container: {err[:300]}")
        return SandboxHandle(
            session_id=session_id,
            container_name=name,
            base_dir=base_dir,
            workspace_dir=workspace_dir,
            profile=profile,
        )

    async def exec(
        self,
        handle: SandboxHandle,
        command: list[str],
        timeout: float | None = None,
    ) -> ExecResult:
        timeout = timeout if timeout is not None else float(handle.profile.timeout_seconds)
        rc, out, err = await self._run(
            self.exec_command(handle.container_name, command, timeout), timeout=timeout + 15.0
        )
        return ExecResult(
            exit_code=rc,
            stdout=out,
            stderr=err,
            timed_out=rc == 124,
        )

    async def is_running(self, handle: SandboxHandle) -> bool:
        """Whether the sandbox container is still alive."""
        rc, out, _err = await self._run(
            ["inspect", "-f", "{{.State.Running}}", handle.container_name], timeout=15.0
        )
        return rc == 0 and out.strip() == "true"

    async def collect_workspace(self, handle: SandboxHandle) -> Path:
        # the workspace is a host bind mount: it is already on the host
        return handle.workspace_dir

    async def destroy(self, handle: SandboxHandle) -> None:
        rc, _out, err = await self._run(self.destroy_command(handle.container_name), timeout=30.0)
        if rc != 0:
            raise SandboxError(f"failed to destroy sandbox: {err[:300]}")


def sandbox_available(engine: str = "docker") -> bool:
    """Best-effort check used by tests to skip when no engine is present."""
    if shutil.which(engine) is None:
        return False
    try:
        import subprocess

        probe = subprocess.run([engine, "info"], capture_output=True, timeout=10)
        return probe.returncode == 0
    except Exception:  # availability probe: any failure means "not available"
        return False
