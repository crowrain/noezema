"""Session tool executor: dev stub vs one-shot sandbox (T7.58, ADR-0023).

One switch for every host session entry point (eval run, wake tick, manual
entry, web app): the env variable ``NOEZEMA_TOOL_EXECUTOR``, read once per
``build_orchestrator`` call.

  stub    DEFAULT — apps.orchestrator.executor.StubToolExecutor, the M1 dev
          stand-in: path containment + output cap + hard timeout, but NO
          network/capability isolation (DEV ONLY by its own module docstring,
          AGENTS.md §7); shell.execute is not implemented there
          (`tool_not_supported:`, T7.57(b)). Every existing test and run uses
          this path; the default value keeps all of them unchanged.

  sandbox packages.broker.SandboxToolBroker over the one-shot container of
          packages.sandbox.runtime — non-root (uid 10001), read-only rootfs,
          cap-drop ALL, no-new-privileges, network none, CPU/memory/PID/timeout
          caps from sandbox/policy/<profile>.yaml. ONE container per session:
          the Orchestrator opens it inside phase 1 as soon as the effective
          capability profile is known (nothing has executed yet, no tool has
          run) and closes it on EVERY path out of phase 1 — a normal finish,
          an early terminal `_finish`, a raised `LeaseLost`, any infra
          exception (`Orchestrator._open_tool_sandbox` / `_close_tool_sandbox`).

Fail-closed rules (§3 invariants, AGENTS.md §4):

  - an unknown switch value raises — no silent fallback to the unisolated stub;
  - in sandbox mode an unavailable engine or image refuses the session START
    (nothing is executed, no session row survives) instead of falling back;
  - a sandbox profile may only be narrowed by the config snapshot
    (`effective_profile`), and the container always gets `--network none`:
    external egress exists only host-side through the research proxy
    (`research.fetch` is host-side in BOTH executor modes).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import uuid
from pathlib import Path
from typing import Protocol, runtime_checkable

from sqlalchemy.ext.asyncio import AsyncSession

from apps.orchestrator.executor import StubToolExecutor
from packages.broker import DeferredStagingWriter, SandboxToolBroker, ToolExecutor
from packages.domain.models.base import JsonDict
from packages.domain.schemas.observation import Observation
from packages.policy.profiles import POLICY_DIR, CapabilityProfile, NetworkMode
from packages.sandbox.runtime import (
    ContainerSandboxRuntime,
    SandboxError,
    SandboxHandle,
    SandboxProfile,
    SandboxSettings,
)

logger = logging.getLogger("noezema.tool_executor")

#: The switch (T7.58). Read from the environment of the host entry point.
TOOL_EXECUTOR_ENV = "NOEZEMA_TOOL_EXECUTOR"
STUB_MODE = "stub"
SANDBOX_MODE = "sandbox"
EXECUTOR_MODES: frozenset[str] = frozenset({STUB_MODE, SANDBOX_MODE})


class ToolExecutorConfigError(RuntimeError):
    """The executor switch (or the profile it implies) is not valid."""


class ToolExecutorUnavailableError(RuntimeError):
    """Sandbox mode is selected but the engine/image/container is unusable:
    the session does not start (fail-closed, never a fallback to the stub)."""


def resolve_tool_executor_mode(raw: str | None) -> str:
    """Pure resolver (AGENTS.md §4: separately testable logic).

    Not set / empty = the default dev stub. Any other unknown value is an
    error — a typo must not silently select the unisolated executor.
    """
    if raw is None or not raw.strip():
        return STUB_MODE
    mode = raw.strip().lower()
    if mode not in EXECUTOR_MODES:
        known = ", ".join(sorted(EXECUTOR_MODES))
        raise ToolExecutorConfigError(
            f"{TOOL_EXECUTOR_ENV}={raw!r}: unknown tool executor mode (known values: {known}); "
            "unknown values are rejected — there is no silent fallback to the dev stub"
        )
    return mode


def tool_executor_mode_from_env() -> str:
    """The mode the host entry points will use (also used for the log label)."""
    return resolve_tool_executor_mode(os.environ.get(TOOL_EXECUTOR_ENV))


#: Container network per capability profile. `research_proxy` is a HOST-side
#: mode (the research proxy performs the egress, §11.2/M6): the container of a
#: curated/open_lab session still gets no network at all (§3 "Sandbox").
_NETWORK_FOR_CONTAINER: dict[NetworkMode, str] = {
    NetworkMode.NONE: "none",
    NetworkMode.RESEARCH_PROXY: "none",
}


def sandbox_runtime_profile(cap_profile: CapabilityProfile) -> SandboxProfile:
    """Runtime profile for a session: the YAML ceiling of the snapshot's
    access profile (resources, rootfs, capabilities), with the container
    network resolved to what the engine can actually enforce.

    Fail-closed: an unknown access profile name or an unknown network mode is
    an error; a snapshot may narrow the tool grants but never widen them.
    """
    yaml_path = POLICY_DIR / f"{cap_profile.name}.yaml"
    if not yaml_path.is_file():
        raise ToolExecutorConfigError(
            f"sandbox profile for access profile {cap_profile.name!r} not found: {yaml_path}"
        )
    ceiling = SandboxProfile.from_yaml(yaml_path)
    if ceiling.name != cap_profile.name:
        raise ToolExecutorConfigError(
            f"sandbox profile {yaml_path} declares name {ceiling.name!r}, expected {cap_profile.name!r}"
        )
    widened = sorted(set(cap_profile.tools) - set(ceiling.tools))
    if widened:
        raise ToolExecutorConfigError(
            f"capability profile grants tools outside the '{ceiling.name}' ceiling: {widened}"
        )
    network = _NETWORK_FOR_CONTAINER.get(cap_profile.network)
    if network is None:
        raise ToolExecutorConfigError(
            f"access profile '{cap_profile.name}': network mode {cap_profile.network!r} "
            "cannot be enforced by the sandbox runtime"
        )
    # revalidated (not model_copy): a runtime profile is built through the same
    # closed schema as the YAML ceiling, so nothing slips in between
    return SandboxProfile.model_validate({**ceiling.model_dump(), "network": network})


def ensure_sandbox_available(settings: SandboxSettings) -> None:
    """Preflight BEFORE any session work starts: engine installed, image present.

    The message is actionable (it names the engine, the image and the build
    command) because refusing to start a session is only useful if the operator
    can see why.
    """
    if shutil.which(settings.engine) is None:
        raise ToolExecutorUnavailableError(
            f"tool executor={SANDBOX_MODE}: container engine {settings.engine!r} "
            "is not installed or not on PATH — the session does not start"
        )
    try:
        probe = subprocess.run(
            [settings.engine, "image", "inspect", settings.image],
            capture_output=True,
            timeout=30.0,
        )
    except OSError as exc:  # engine vanished between which() and the probe
        raise ToolExecutorUnavailableError(
            f"tool executor={SANDBOX_MODE}: cannot run {settings.engine!r} "
            f"{['image', 'inspect', settings.image]}: {exc}"
        ) from exc
    if probe.returncode != 0:
        detail = probe.stderr.decode("utf-8", "replace")[:300].strip() or "image inspect failed"
        raise ToolExecutorUnavailableError(
            f"tool executor={SANDBOX_MODE}: image {settings.image!r} is not available "
            f"for engine {settings.engine!r}: {detail}. Build it: "
            f"{settings.engine} build -f sandbox/Containerfile -t {settings.image} sandbox/"
        )


@runtime_checkable
class SessionScopedExecutor(Protocol):
    """Lifecycle the Orchestrator drives around phase 1 of `run_session` (T7.58).

    A duck-typed executor without these methods (StubToolExecutor, the test
    doubles) is left exactly as it was — `getattr` finds nothing and the stub
    path stays byte-identical.
    """

    workspace_dir: Path

    async def open_session(self, session_id: uuid.UUID, cap_profile: CapabilityProfile) -> None: ...

    async def close_session(self) -> None: ...


class SandboxSessionExecutor:
    """Sandboxed tool executor: one disposable container per session.

    The executor does not know the session id at build time (`run_session`
    generates it, and `build_orchestrator` is shared), so the container is
    opened by the orchestrator hook once the session row and its effective
    capability profile exist. `close_session` is called from a `finally`: it
    never raises (a cleanup problem must not replace the durable session
    outcome) but names every problem it could not fix.
    """

    def __init__(self, runtime: ContainerSandboxRuntime) -> None:
        self._runtime = runtime
        self._handle: SandboxHandle | None = None
        self._broker: SandboxToolBroker | None = None
        self._snapshot_id: uuid.UUID | None = None

    @property
    def runtime(self) -> ContainerSandboxRuntime:
        return self._runtime

    @property
    def handle(self) -> SandboxHandle | None:
        return self._handle

    @property
    def workspace_dir(self) -> Path:
        """The per-session overlay (the host bind mount of /workspace).

        Before the container is open there is no overlay: the path is returned
        as a non-existent placeholder under the sandbox root, which is what the
        orchestrator's `Path(workspace_dir).is_dir()` guard already treats as
        "nothing to freeze".
        """
        if self._handle is None:
            return self._runtime.work_root / "session-not-started"
        return self._handle.workspace_dir

    @workspace_dir.setter
    def workspace_dir(self, value: Path) -> None:  # pragma: no cover - guarded contract
        # The ToolExecutor protocol declares workspace_dir as an attribute; for
        # this executor it IS the session's container overlay and only
        # open_session may set it (repointing it would silently move a sandboxed
        # session's writes onto the host).
        raise TypeError(
            f"SandboxSessionExecutor.workspace_dir is set by open_session, not by the "
            f"caller (ignored {value!r})"
        )

    @property
    def snapshot_id(self) -> uuid.UUID | None:
        # T7.7 (EVAL-2): memory.search retrieval stays pinned to the session's
        # effective snapshot (§14.1) — same contract as StubToolExecutor owns.
        return self._snapshot_id

    @snapshot_id.setter
    def snapshot_id(self, value: uuid.UUID | None) -> None:
        self._snapshot_id = value
        if self._broker is not None:
            self._broker.snapshot_id = value

    async def open_session(
        self, session_id: uuid.UUID, cap_profile: CapabilityProfile
    ) -> None:
        if self._handle is not None:
            raise ToolExecutorConfigError(
                f"sandbox container {self._handle.container_name} is still open for session "
                f"{self._handle.session_id}; this executor runs one session at a time"
            )
        profile = sandbox_runtime_profile(cap_profile)
        settings = SandboxSettings(
            image=self._runtime.image, engine=self._runtime.engine, work_root=self._runtime.work_root
        )
        try:
            ensure_sandbox_available(settings)  # defense in depth for injected runtimes
            handle = await self._runtime.start(str(session_id), None, profile)
        except (SandboxError, OSError) as exc:
            raise ToolExecutorUnavailableError(
                f"tool executor={SANDBOX_MODE}: session {session_id} does not start — {exc}"
            ) from exc
        broker = SandboxToolBroker(
            handle, cap_profile, self._runtime, staging_writer=DeferredStagingWriter()
        )
        broker.snapshot_id = self._snapshot_id
        self._handle = handle
        self._broker = broker
        logger.info(
            "tool executor=sandbox opened session=%s container=%s profile=%s network=%s "
            "timeout_s=%d memory_mb=%d image=%s",
            session_id,
            handle.container_name,
            profile.name,
            profile.network,
            profile.timeout_seconds,
            profile.memory_mb,
            self._runtime.image,
        )

    async def close_session(self) -> None:
        """Destroy the container and remove the disposable per-session overlay.

        Returns nothing; problems are logged with their names (a leaked
        container must be findable in the journal, not silently swallowed).
        """
        handle = self._handle
        self._handle = None
        self._broker = None
        if handle is None:
            return
        try:
            await self._runtime.destroy(handle)
        except (SandboxError, OSError) as exc:
            logger.warning(
                "tool executor=sandbox LEAKED container %s (session %s): destroy failed: %s",
                handle.container_name,
                handle.session_id,
                exc,
            )
        # The overlay is disposable by design (§11, packages/sandbox/runtime.py):
        # the frozen workspace manifest in the DB is the only survivor.
        session_dir = handle.workspace_dir.parent
        try:
            shutil.rmtree(session_dir)
        except OSError as exc:
            logger.warning(
                "tool executor=sandbox session dir %s not removed: %s", session_dir, exc
            )
        logger.info(
            "tool executor=sandbox closed session=%s container=%s",
            handle.session_id,
            handle.container_name,
        )

    async def execute(
        self, tool: str, arguments: JsonDict, *, db: AsyncSession | None = None
    ) -> Observation:
        broker = self._broker
        if broker is None:
            raise ToolExecutorUnavailableError(
                f"tool executor={SANDBOX_MODE}: no sandbox container is open for this session "
                "(open_session did not run) — nothing is executed on the host instead"
            )
        return await broker.execute(tool, arguments, db=db)


def build_tool_executor(workspace_root: Path, *, mode: str | None = None) -> ToolExecutor:
    """The single assembly point of the session tool executor (T7.58).

    `mode=None` reads the environment (`NOEZEMA_TOOL_EXECUTOR`); an explicit
    value is used by tests and by callers that already resolved it. Sandbox
    mode runs the preflight here so a 7-session eval series refuses at once
    instead of failing session by session.
    """
    resolved = resolve_tool_executor_mode(
        mode if mode is not None else os.environ.get(TOOL_EXECUTOR_ENV)
    )
    if resolved == STUB_MODE:
        return StubToolExecutor(workspace_root)
    settings = SandboxSettings()
    ensure_sandbox_available(settings)
    runtime = ContainerSandboxRuntime(
        image=settings.image, engine=settings.engine, work_root=settings.work_root
    )
    logger.info(
        "tool executor=sandbox selected: image=%s engine=%s work_root=%s (one container per session)",
        settings.image,
        settings.engine,
        settings.work_root,
    )
    return SandboxSessionExecutor(runtime)
