"""Unit: the tool-executor switch (T7.58, ADR-0023).

No docker and no DB here: the resolver, the profile mapping and the fail-closed
preflight are pure/cheap logic. The sandbox container itself is exercised in
tests/scenario/test_orchestrator_sandbox_executor.py.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.tool_executors import (
    EXECUTOR_MODES,
    SANDBOX_MODE,
    STUB_MODE,
    TOOL_EXECUTOR_ENV,
    SandboxSessionExecutor,
    SessionScopedExecutor,
    ToolExecutorConfigError,
    ToolExecutorUnavailableError,
    build_tool_executor,
    resolve_tool_executor_mode,
    sandbox_runtime_profile,
    tool_executor_mode_from_env,
)
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.policy.profiles import effective_profile

pytestmark = [pytest.mark.unit]


# ── (а) switch parsing ──────────────────────────────────────────────────────


def test_default_is_stub() -> None:
    assert resolve_tool_executor_mode(None) == STUB_MODE
    assert resolve_tool_executor_mode("") == STUB_MODE
    assert resolve_tool_executor_mode("   ") == STUB_MODE


def test_sandbox_value_is_accepted_stripping_case_and_spaces() -> None:
    assert resolve_tool_executor_mode("sandbox") == SANDBOX_MODE
    assert resolve_tool_executor_mode(" Sandbox ") == SANDBOX_MODE
    assert resolve_tool_executor_mode("STUB") == STUB_MODE


@pytest.mark.parametrize(
    "raw", ["docker", "sb", "real", "stubtool", "python", "noezema", "sandes", "sand box"]
)
def test_unknown_value_fails_closed(raw: str) -> None:
    """No silent fallback to the unisolated dev stub (§3, AGENTS.md §4)."""
    with pytest.raises(ToolExecutorConfigError) as excinfo:
        resolve_tool_executor_mode(raw)
    message = str(excinfo.value)
    assert TOOL_EXECUTOR_ENV in message
    assert raw in message
    assert all(mode in message for mode in EXECUTOR_MODES)


def test_env_read_helper_uses_the_switch(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOL_EXECUTOR_ENV, raising=False)
    assert tool_executor_mode_from_env() == STUB_MODE
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, SANDBOX_MODE)
    assert tool_executor_mode_from_env() == SANDBOX_MODE
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, "sandboxz")
    with pytest.raises(ToolExecutorConfigError):
        tool_executor_mode_from_env()


# ── (б) assembly: default stays the dev stub ────────────────────────────────


def test_build_default_returns_stub_executor(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOL_EXECUTOR_ENV, raising=False)
    executor = build_tool_executor(tmp_path / "ws")
    assert isinstance(executor, StubToolExecutor)
    assert executor.workspace_dir == tmp_path / "ws"
    # The orchestrator's session-scoped hooks must NOT appear on the stub path:
    # run_session then behaves exactly as before T7.58.
    assert not hasattr(executor, "open_session")
    assert not hasattr(executor, "close_session")


def test_build_rejects_unknown_mode_before_any_side_effect(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, "sandbox-mode?")
    with pytest.raises(ToolExecutorConfigError):
        build_tool_executor(tmp_path / "ws")
    assert not (tmp_path / "ws").exists()


def test_build_sandbox_without_engine_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No docker/podman on PATH → refusal, never a stub fallback, no workspace dir."""
    monkeypatch.setenv(TOOL_EXECUTOR_ENV, SANDBOX_MODE)
    monkeypatch.setenv("NOEZEMA_SANDBOX_ENGINE", "noezema-no-such-engine")
    monkeypatch.setenv("NOEZEMA_SANDBOX_WORK_ROOT", str(tmp_path / "sb"))
    with pytest.raises(ToolExecutorUnavailableError) as excinfo:
        build_tool_executor(tmp_path / "ws")
    assert "noezema-no-such-engine" in str(excinfo.value)
    assert not (tmp_path / "ws").exists()


# ── (в) capability profile → runtime profile ────────────────────────────────


def _sealed_cap():  # the real bootstrap snapshot policy (what a session gets)
    return effective_profile(BOOTSTRAP_PAYLOAD["policy"])


def test_runtime_profile_of_sealed_snapshot_is_none_network() -> None:
    cap = _sealed_cap()
    assert cap.name == "sealed"
    profile = sandbox_runtime_profile(cap)
    assert profile.network == "none"
    # resource caps come from sandbox/policy/sealed.yaml
    assert profile.timeout_seconds == 60
    assert profile.memory_mb == 512
    assert profile.max_processes == 32
    assert profile.read_only_rootfs and profile.no_new_privileges


def test_runtime_profile_never_gives_the_container_a_network() -> None:
    """curated declares research_proxy: egress is HOST-side (the research proxy
    executes research.fetch in both executor modes) — the container stays dark."""
    from packages.policy.profiles import NetworkMode

    policy = dict(BOOTSTRAP_PAYLOAD["policy"])
    policy["access_profile"] = "curated"
    cap = effective_profile(policy)
    assert cap.network is NetworkMode.RESEARCH_PROXY  # the YAML ceiling
    profile = sandbox_runtime_profile(cap)
    assert profile.name == "curated"
    assert profile.network == "none"
    assert profile.timeout_seconds == 120  # the YAML ceiling still applies


def test_runtime_profile_unknown_access_profile_fails_closed() -> None:
    from dataclasses import replace

    from packages.policy.profiles import load_profile

    cap = replace(load_profile("sealed"), name="noezema-no-such-profile")
    with pytest.raises(ToolExecutorConfigError) as excinfo:
        sandbox_runtime_profile(cap)
    assert "noezema-no-such-profile" in str(excinfo.value)


def test_runtime_profile_rejects_tools_outside_the_ceiling() -> None:
    """A narrowed snapshot is the norm; a widened grant is a bug → refusal, not
    a container that runs tools the profile never allowed."""
    from dataclasses import replace

    from packages.policy.profiles import load_profile

    ceiling = load_profile("sealed")
    cap = replace(ceiling, tools=ceiling.tools | {"network.post"})
    with pytest.raises(ToolExecutorConfigError) as excinfo:
        sandbox_runtime_profile(cap)
    assert "outside the 'sealed' ceiling" in str(excinfo.value)


# ── (г) the session-scoped contract + fail-closed execution ────────────────


def test_sandbox_executor_implements_the_session_scoped_contract(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ContainerSandboxRuntime

    executor = SandboxSessionExecutor(
        ContainerSandboxRuntime(image="noezema-sandbox:test", engine="docker", work_root=tmp_path / "sb")
    )
    assert isinstance(executor, SessionScopedExecutor)
    # no container is open yet: workspace_dir is a placeholder that does NOT exist
    # (the freeze guard `Path(workspace_dir).is_dir()` then skips the manifest)
    assert executor.workspace_dir == tmp_path / "sb" / "session-not-started"
    assert not executor.workspace_dir.is_dir()


def test_workspace_dir_is_not_repointable(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ContainerSandboxRuntime

    executor = SandboxSessionExecutor(
        ContainerSandboxRuntime(image="noezema-sandbox:test", engine="docker", work_root=tmp_path / "sb")
    )
    with pytest.raises(TypeError):
        executor.workspace_dir = Path("/tmp/elsewhere")  # type: ignore[misc]


async def test_execute_without_open_container_fails_closed(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ContainerSandboxRuntime

    executor = SandboxSessionExecutor(
        ContainerSandboxRuntime(image="noezema-sandbox:test", engine="docker", work_root=tmp_path / "sb")
    )
    with pytest.raises(ToolExecutorUnavailableError):
        await executor.execute("python.execute", {"code": "print(1)"})


async def test_open_with_unavailable_image_fails_closed(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ContainerSandboxRuntime

    executor = SandboxSessionExecutor(
        ContainerSandboxRuntime(
            image="noezema-sandbox:no-such-tag-t758", engine="docker", work_root=tmp_path / "sb"
        )
    )
    # the preflight fails before any container is created: nothing to clean up
    with pytest.raises(ToolExecutorUnavailableError) as excinfo:
        await executor.open_session(uuid.uuid4(), _sealed_cap())
    assert "sandbox" in str(excinfo.value)
    assert executor.handle is None
    await executor.close_session()  # idempotent, no-op


async def test_close_without_open_is_a_noop(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ContainerSandboxRuntime

    executor = SandboxSessionExecutor(
        ContainerSandboxRuntime(image="noezema-sandbox:test", engine="docker", work_root=tmp_path / "sb")
    )
    # runs in the finally of run_session: it must never raise, and it must not
    # touch the engine when no container was ever started
    await executor.close_session()
    assert executor.handle is None


# ── (д) stub mode behavior is unchanged through the switch ──────────────────


async def test_stub_mode_behavior_unchanged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(TOOL_EXECUTOR_ENV, raising=False)
    executor = build_tool_executor(tmp_path / "ws")
    assert isinstance(executor, StubToolExecutor)

    obs = await executor.execute("shell.execute", {"command": "echo hi"})
    assert not obs.ok
    assert obs.error is not None and obs.error.startswith("tool_not_supported:")  # T7.57(b)

    obs = await executor.execute("python.execute", {"code": "print(1)"})
    assert obs.ok, obs.error
    assert obs.data["stdout"].strip() == "1"

    obs = await executor.execute("artifact.create", {})
    assert not obs.ok and "unknown tool" in (obs.error or "")


# ── (е) the orchestrator hook is duck-typed: the stub path is untouched ─────


def test_orchestrator_hook_names(tmp_path: Path) -> None:
    """The lifecycle is driven through getattr, so an executor without the hooks
    (StubToolExecutor, the test doubles) keeps byte-identical behavior."""
    assert hasattr(Orchestrator, "_open_tool_sandbox")
    assert hasattr(Orchestrator, "_close_tool_sandbox")
