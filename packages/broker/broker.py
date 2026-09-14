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

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import ActionState, AuditEventType, IdempotencyClass
from packages.domain.schemas.observation import Observation
from packages.policy.profiles import CapabilityProfile
from packages.policy.tools import get_tool
from packages.sandbox.runtime import (
    ContainerSandboxRuntime,
    SandboxError,
    SandboxHandle,
)

# §5.7 retry policy: how many ADDITIONAL attempts after the first.
RETRY_POLICY: dict[IdempotencyClass, int] = {
    IdempotencyClass.PURE: 2,
    IdempotencyClass.IDEMPOTENT: 1,
    IdempotencyClass.OBSERVATION: 0,
    IdempotencyClass.NON_IDEMPOTENT: 0,
}

MAX_OUTPUT = 10_000


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
                obs = Observation(
                    tool=tool,
                    ok=True,
                    data={"results": [], "note": "durable memory search lands in M3"},
                )
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
                    container_gone = not await self.runtime.is_running(self.handle)
                except Exception:
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

    async def _sandbox_exec(self, tool: str, arguments: JsonDict) -> Observation:
        if tool == "shell.execute":
            cmd = ["bash", "-c", str(arguments["command"])]
        else:
            cmd = ["python3", "-I", "-c", str(arguments["code"])]
        result = await self.runtime.exec(self.handle, cmd)
        data: JsonDict = {
            "exit_code": result.exit_code,
            "stdout": result.stdout[:MAX_OUTPUT],
            "stderr": result.stderr[:MAX_OUTPUT],
        }
        if result.timed_out:
            return Observation(tool=tool, ok=False, data=data, error="timeout")
        if result.exit_code == 0:
            return Observation(tool=tool, ok=True, data=data)
        # a failed exec where the CONTAINER IS GONE is not a result — the
        # command may have executed partially (T2.22 failpoint)
        if not await self.runtime.is_running(self.handle):
            return Observation(
                tool=tool,
                ok=False,
                data=data,
                error="container gone mid-execution",
                result_unknown=True,
            )
        return Observation(tool=tool, ok=False, data=data, error=f"exit code {result.exit_code}")

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
                content = path.read_bytes()[:MAX_OUTPUT].decode("utf-8", "replace")
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
