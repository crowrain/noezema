"""Tool Broker: executes authorized tool calls in the sandbox (T2.7-T2.10).

The broker sits between the orchestrator (which proposes + gets the policy
decision) and the one-shot container (which enforces isolation). It owns:

  - the §5.7 retry policy per repeatability class:
        pure               retries on TRANSIENT failures only;
        observation        never retried blindly after start;
        idempotent(key)    one retry, safe because of the host key;
        non_idempotent     never retried blindly after start;
  - idempotency-key discipline (T2.8): the host-generated key is bound to
    tool + canonical arguments hash; a key reused with a DIFFERENT hash is
    a security incident (alert + audit), a key reused with the same hash
    and an already-completed action is a replay (no re-execution);
  - ActionOutcomeUnknown (T2.9): actions that were started but never
    finished (crash between start and result) are reconciled to
    outcome_unknown, never to a guessed success;
  - the real sandboxed tools (T2.10): shell.execute, python.execute run in
    the container; workspace.read/list/write operate on the per-session
    overlay only; question.create / message.reply go through the injected
    staging writer (durable session_staging lands in PR #14).
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import ActionState, AuditEventType, IdempotencyClass
from packages.domain.sanitization import mask_nul
from packages.domain.schemas.observation import Observation
from packages.policy.profiles import CapabilityProfile
from packages.policy.tools import get_tool
from packages.sandbox.runtime import (
    ContainerSandboxRuntime,
    SandboxError,
    SandboxHandle,
    SandboxHealthError,
)

# §5.7 retry policy: how many ADDITIONAL attempts after the first.
RETRY_POLICY: dict[IdempotencyClass, int] = {
    IdempotencyClass.PURE: 2,
    IdempotencyClass.IDEMPOTENT: 1,
    IdempotencyClass.OBSERVATION: 0,
    IdempotencyClass.NON_IDEMPOTENT: 0,
}

MAX_OUTPUT = 10_000

# T7.91: the engine flips the container state ASYNCHRONOUSLY. Measured on `.87`
# (docker 29.8, probe script outside the repo): after `docker kill -s KILL` the
# `docker exec` client returns with 137 BEFORE dockerd answers State.Running=false.
# The gap from the exec return to the first "not running" answer: median 33 ms /
# max 60 ms idle, median 70 ms / max 101 ms with six concurrent kills (the `-n auto`
# profile). A fast unix-socket API client caught a stale `Running=true` right after
# the exec return in 5/20 idle and 5/18 concurrent runs. The `docker inspect` CLI
# costs 29–102 ms on this host, so locally the window usually hides behind the CLI's
# own latency — which is exactly why only a CI runner tripped over it (run 38112342827).
# A failed exec whose exit code is CONSISTENT with the container having been killed
# therefore gets one bounded second look before it may be called a plain failure.
# T7.92: the same window is reused for an ENGINE-level failure (SandboxError) of
# shell.execute/python.execute — there too, "still running" is not conclusive.
CONTAINER_DEATH_RECHECK_SECONDS = 1.0
CONTAINER_DEATH_RECHECK_INTERVAL = 0.1

# 125 = `docker exec` could not run/attach the command at all: that is a CLI-level
# refusal, not an answer from the command — the container may have been killed while
# the exec was starting.
DOCKER_CLI_EXEC_FAILURE_EXIT = 125
# exit > 128 means "killed by signal N-128": 137 = 128+SIGKILL, 143 = 128+SIGTERM —
# precisely how a container kill reaches the exec'd process.
FATAL_SIGNAL_EXIT_FLOOR = 128


def exit_is_consistent_with_container_death(exit_code: int) -> bool:
    """Could this failed exec have been caused by the container dying?

    A container kill reaches the exec'd process as a fatal signal (137/143) or, if
    the kill wins before the exec process starts, as a docker CLI refusal (125).
    Any other nonzero code is the command's OWN answer: the command ran and reported,
    so the outcome is known even if the container died a moment later.
    """
    return exit_code > FATAL_SIGNAL_EXIT_FLOOR or exit_code == DOCKER_CLI_EXEC_FAILURE_EXIT


class StagingWriter(Protocol):
    """Applies a host-side staging operation inside the caller's
    transaction. The durable session_staging backend lands in PR #14;
    until then DeferredStagingWriter keeps the M1 behavior."""

    async def write(self, op: str, payload: JsonDict, db: AsyncSession) -> JsonDict: ...


class ToolExecutor(Protocol):
    """What the orchestrator sees: the dev stub executor or the sandboxed
    broker — one contract (T2.7)."""

    workspace_dir: Path

    async def execute(
        self, tool: str, arguments: JsonDict, *, db: AsyncSession | None = None
    ) -> Observation: ...


class DeferredStagingWriter:
    """M1-compatible staging: the operation is recorded in memory and the
    host-side effect is applied by the orchestrator phase that owns it."""

    def __init__(self) -> None:
        self.operations: list[tuple[str, JsonDict]] = []

    async def write(self, op: str, payload: JsonDict, db: AsyncSession) -> JsonDict:
        self.operations.append((op, payload))
        return {"deferred": True, "op": op}


@dataclass(frozen=True)
class IdempotencyVerdict:
    """new | replay | conflict (T2.8)."""

    status: str
    existing_state: str | None = None


def check_idempotency(existing: dict[str, Any] | None, arguments_hash: str) -> IdempotencyVerdict:
    """The host key is bound to tool + canonical arguments hash forever.

    - no row            -> new action;
    - same hash, done   -> replay (return the stored outcome, no re-run);
    - same hash, live   -> replay (the action is in flight or already
                           recorded; a blind re-run is forbidden);
    - DIFFERENT hash    -> security incident: the key was reused with
                           other arguments.
    """
    if existing is None:
        return IdempotencyVerdict("new")
    if existing["arguments_hash"] != arguments_hash:
        return IdempotencyVerdict("conflict", existing["state"])
    return IdempotencyVerdict("replay", existing["state"])


class SandboxToolBroker:
    def __init__(
        self,
        handle: SandboxHandle,
        profile: CapabilityProfile,
        runtime: ContainerSandboxRuntime,
        staging_writer: StagingWriter | None = None,
    ) -> None:
        self.handle = handle
        self.profile = profile
        self.runtime = runtime
        self.staging = staging_writer if staging_writer is not None else DeferredStagingWriter()
        # T7.7 (EVAL-2): memory.search retrieval is pinned to the
        # session's effective snapshot (set by the orchestrator)
        self.snapshot_id: uuid.UUID | None = None

    @property
    def workspace_dir(self) -> Path:
        # the per-session overlay on the host (T2.12 freeze input)
        return self.handle.workspace_dir

    # ── public ────────────────────────────────────────────────────────────

    async def execute(
        self, tool: str, arguments: JsonDict, *, db: AsyncSession | None = None
    ) -> Observation:
        spec = get_tool(tool)
        if spec is None:
            return Observation(tool=tool, ok=False, error=f"unknown tool: {tool}")

        max_attempts = RETRY_POLICY[spec.idempotency_class] + 1
        obs: Observation | None = None
        for attempt in range(max_attempts):
            obs = await self._run_once(tool, spec.idempotency_class, arguments, db, attempt)
            if obs.ok or not obs.transient or attempt + 1 >= max_attempts:
                break
        assert obs is not None
        return obs

    # ── one attempt ───────────────────────────────────────────────────────

    async def _run_once(
        self,
        tool: str,
        klass: IdempotencyClass,
        arguments: JsonDict,
        db: AsyncSession | None,
        attempt: int,
    ) -> Observation:
        obs: Observation
        try:
            if tool in ("shell.execute", "python.execute"):
                obs = await self._sandbox_exec(tool, arguments)
            elif tool in ("workspace.read", "workspace.list", "workspace.write"):
                obs = self._workspace(tool, arguments)
            elif tool == "memory.search":
                obs = await self._memory_search(str(arguments.get("query", "")), db)
            elif tool in ("question.create", "message.reply"):
                if db is None:
                    obs = Observation(tool=tool, ok=False, error="staging requires a db session", transient=True)
                else:
                    data = await self.staging.write(tool, dict(arguments), db)
                    obs = Observation(tool=tool, ok=True, data=data)
            else:  # pragma: no cover
                obs = Observation(tool=tool, ok=False, error="unreachable")
        except SandboxError as exc:
            # engine-level failure: transient, retryable per class policy;
            # but if the container is gone mid-command the outcome is
            # UNKNOWN (never a clean failure, never retried)
            container_gone = False
            if tool in ("shell.execute", "python.execute"):
                try:
                    if not await self.runtime.is_running(self.handle):
                        # the T2.22 fast path: the engine already answers "gone" — no waiting
                        container_gone = True
                    elif not isinstance(exc, SandboxHealthError):
                        # T7.92: an engine-level failure (engine command timeout) while docker
                        # still answers "running" is NOT conclusive — the engine flips the
                        # container state asynchronously (T7.91 window), and a wedged/half-dead
                        # dockerd is exactly when that window widens. So the same bounded second
                        # look applies, with the same rule: unknown only when the engine ITSELF
                        # answers "not running". A health error is exempt from the wait: there the
                        # engine cannot be asked anything at all, so a recheck could only repeat
                        # the same failing command — the classification stays as before.
                        container_gone = await self._await_container_death()
                except Exception:
                    # a state probe that itself failed proves nothing (unchanged behavior):
                    # unknown is declared only on an actual "not running" answer
                    container_gone = False
            obs = Observation(
                tool=tool,
                ok=False,
                error=f"sandbox: {exc}"[:500],
                transient=not container_gone,
                result_unknown=container_gone,
            )
        except ValueError as exc:
            obs = Observation(tool=tool, ok=False, error=str(exc)[:500])
        except Exception as exc:  # infra failure
            obs = Observation(tool=tool, ok=False, error=f"broker: {exc}"[:500], transient=True)
        obs.idempotency_class = klass
        obs.attempt = attempt
        return obs

    # ── tools ─────────────────────────────────────────────────────────────

    async def _memory_search(
        self, query: str, db: AsyncSession | None
    ) -> Observation:
        """T7.7 (EVAL-2): durable memory search — the same retrieval the
        context pack uses (pointer equality §14.1)."""
        if db is None or self.snapshot_id is None:
            return Observation(
                "memory.search",
                ok=True,
                data={"results": [], "note": "memory search unavailable in this context"},
            )
        from packages.cognition.retrieval import retrieve

        result = await retrieve(db, query, snapshot_id=self.snapshot_id)
        return Observation(
            "memory.search",
            ok=True,
            data={
                "results": [c.line for c in result.current],
                "pending_invalid": [c.line for c in result.pending_invalid],
            },
        )

    async def _sandbox_exec(self, tool: str, arguments: JsonDict) -> Observation:
        if tool == "shell.execute":
            cmd = ["bash", "-c", str(arguments["command"])]
        else:
            cmd = ["python3", "-I", "-c", str(arguments["code"])]
        result = await self.runtime.exec(self.handle, cmd)
        # T7.46a: mask NUL at the capture (the SOURCE layer for the M2
        # path, the same principle as the stub executor, SMOKE-V12-K2 §3)
        data: JsonDict = {
            "exit_code": result.exit_code,
            "stdout": mask_nul(result.stdout)[:MAX_OUTPUT],
            "stderr": mask_nul(result.stderr)[:MAX_OUTPUT],
        }
        if result.timed_out:
            return Observation(tool=tool, ok=False, data=data, error="timeout")
        if result.exit_code == 0:
            return Observation(tool=tool, ok=True, data=data)
        # a failed exec where the CONTAINER IS GONE is not a result — the
        # command may have executed partially (T2.22 failpoint)
        if not await self.runtime.is_running(self.handle):
            return self._container_gone(tool, data)
        # T7.91: "still running" straight after a signal death is NOT conclusive —
        # the engine has not flipped State.Running yet (CONTAINER_DEATH_RECHECK_*).
        # Only an exit code that a container kill can produce gets the bounded
        # second look; a command killed from INSIDE a live container stays a plain
        # failure carrying its real exit code.
        if exit_is_consistent_with_container_death(result.exit_code) and await self._await_container_death():
            return self._container_gone(tool, data)
        return Observation(tool=tool, ok=False, data=data, error=f"exit code {result.exit_code}")

    def _container_gone(self, tool: str, data: JsonDict) -> Observation:
        """Outcome unknown: the command may have run partially (T2.22)."""
        return Observation(
            tool=tool,
            ok=False,
            data=data,
            error="container gone mid-execution",
            result_unknown=True,
        )

    async def _await_container_death(self) -> bool:
        """Re-check the container state for a bounded window (T7.91).

        True as soon as the engine reports the container gone. This is a STATE
        re-check, not a retry of the action: the command was executed exactly once
        either way, and an unknown outcome is still never retried (RETRY_POLICY /
        §5.7 untouched). The budget is a constant, so the wait cannot become a stall.

        Two call sites share it: a kill-consistent failed exec (T7.91) and an
        engine-level failure of shell.execute/python.execute (T7.92). A probe that
        raises is NOT swallowed here — each caller decides what a failing probe means.
        """
        waited = 0.0
        while waited < CONTAINER_DEATH_RECHECK_SECONDS:
            await asyncio.sleep(CONTAINER_DEATH_RECHECK_INTERVAL)
            waited += CONTAINER_DEATH_RECHECK_INTERVAL
            if not await self.runtime.is_running(self.handle):
                return True
        return False

    def _workspace(self, tool: str, arguments: JsonDict) -> Observation:
        root = self.handle.workspace_dir.resolve()
        if not root.is_dir():
            return Observation(tool=tool, ok=False, error="workspace unavailable", transient=True)

        def resolve(raw: str) -> Path:
            # absolute paths are container paths: only /workspace/... maps
            # to the overlay; anything else is outside by construction
            if raw.startswith("/"):
                if not (raw == "/workspace" or raw.startswith("/workspace/")):
                    raise ValueError(f"path outside the session overlay: {raw!r}")
                rel = raw[len("/workspace"):]
            else:
                rel = raw
            candidate = (root / rel).resolve()
            if candidate != root and root not in candidate.parents:
                raise ValueError(f"path escapes the session overlay: {raw!r}")
            return candidate

        try:
            if tool == "workspace.read":
                path = resolve(str(arguments["path"]))
                if not path.is_file():
                    return Observation(tool=tool, ok=False, error="not found")
                # T7.46a: mask NUL at the capture (SOURCE layer)
                content = mask_nul(path.read_bytes()[:MAX_OUTPUT].decode("utf-8", "replace"))
                return Observation(
                tool=tool, ok=True, data={"path": path.relative_to(root).as_posix(), "content": content}
            )
            if tool == "workspace.list":
                path = resolve(str(arguments.get("path", ".")))
                if not path.is_dir():
                    return Observation(tool=tool, ok=False, error="not a directory")
                entries = sorted(p.relative_to(root).as_posix() for p in path.iterdir())
                return Observation(tool=tool, ok=True, data={"entries": entries[:500]})
            path = resolve(str(arguments["path"]))
            content = str(arguments.get("content", ""))
            if len(content.encode("utf-8")) > 1_000_000:
                return Observation(tool=tool, ok=False, error="content too large")
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
            return Observation(
                tool=tool, ok=True, data={"path": path.relative_to(root).as_posix(), "bytes": len(content)}
            )
        except ValueError as exc:
            return Observation(tool=tool, ok=False, error=str(exc)[:500])


async def reconcile_stuck_actions(db: AsyncSession, session_id: str) -> int:
    """T2.9: actions that started but never finished (crash between start
    and result) become outcome_unknown — never a guessed success.

    Runs inside the caller's transaction. Returns the number of actions
    reconciled (each gets an ACTION_OUTCOME_UNKNOWN audit + outbox twin).
    """
    import uuid

    from packages.domain.services.audit import AuditService

    result = await db.execute(
        text(
            "UPDATE actions SET state = :stuck, error_code = 'outcome_unknown', finished_at = now() "
            "WHERE session_id = :s AND state IN ('accepted', 'started') AND finished_at IS NULL "
            "RETURNING id, tool"
        ),
        {"stuck": ActionState.OUTCOME_UNKNOWN.value, "s": session_id},
    )
    rows = result.fetchall()
    audit = AuditService(db)
    sid = uuid.UUID(session_id)
    for row in rows:
        await audit.record(
            AuditEventType.ACTION_OUTCOME_UNKNOWN,
            session_id=sid,
            payload={"action_id": str(row[0]), "tool": row[1]},
            public_summary=f"action outcome unknown: {row[1]}",
        )
    return len(rows)
