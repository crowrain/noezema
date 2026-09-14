"""M1 stub tool executor (DEV ONLY).

Executes the Sealed tool set in-process against a per-session workspace
directory. This is a *development* stand-in for the sandboxed ToolBroker
(M2): it has path containment, output caps and a hard timeout, but NO
network/capability isolation — it must never run in production with an
untrusted model. M2 replaces it with the rootless single-use container.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass, field
from pathlib import Path

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import IdempotencyClass

MAX_OUTPUT_BYTES = 10_000
TOOL_TIMEOUT_SECONDS = 15.0

_IDEMPOTENCY: dict[str, IdempotencyClass] = {
    "workspace.read": IdempotencyClass.PURE,
    "workspace.list": IdempotencyClass.PURE,
    "workspace.write": IdempotencyClass.NON_IDEMPOTENT,
    "python.execute": IdempotencyClass.NON_IDEMPOTENT,
    "memory.search": IdempotencyClass.OBSERVATION,
    "question.create": IdempotencyClass.IDEMPOTENT,
    "message.reply": IdempotencyClass.NON_IDEMPOTENT,
}


@dataclass(slots=True)
class Observation:
    tool: str
    ok: bool
    data: JsonDict = field(default_factory=dict)
    error: str | None = None

    @property
    def idempotency_class(self) -> IdempotencyClass:
        return _IDEMPOTENCY[self.tool]


class UnknownToolError(ValueError):
    pass


class StubToolExecutor:
    def __init__(self, workspace_dir: Path) -> None:
        self.workspace_dir = workspace_dir
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        candidate = (self.workspace_dir / path).resolve()
        root = self.workspace_dir.resolve()
        if candidate != root and root not in candidate.parents:
            raise ValueError(f"path escapes workspace: {path!r}")
        return candidate

    async def execute(self, tool: str, arguments: JsonDict) -> Observation:
        try:
            if tool not in _IDEMPOTENCY:
                raise UnknownToolError(tool)
        except UnknownToolError as exc:
            return Observation(tool=tool, ok=False, error=f"unknown tool: {exc}")

        try:
            if tool == "workspace.read":
                return await self._workspace_read(arguments)
            if tool == "workspace.list":
                return await self._workspace_list(arguments)
            if tool == "workspace.write":
                return await self._workspace_write(arguments)
            if tool == "python.execute":
                return await self._python_execute(arguments)
            if tool == "memory.search":
                return Observation(
                    tool=tool,
                    ok=True,
                    data={"results": [], "note": "durable memory search lands in M3"},
                )
            if tool in ("question.create", "message.reply"):
                # Host-side effects are applied by the orchestrator, which
                # owns the DB transaction.
                return Observation(tool=tool, ok=True, data={"deferred": True, "arguments": arguments})
            return Observation(tool=tool, ok=False, error="unreachable")  # pragma: no cover
        except Exception as exc:
            return Observation(tool=tool, ok=False, error=str(exc)[:500])

    async def _workspace_read(self, arguments: JsonDict) -> Observation:
        path = self._resolve(str(arguments.get("path", "")))
        if not path.is_file():
            return Observation("workspace.read", ok=False, error="not found")
        data = path.read_bytes()[:MAX_OUTPUT_BYTES]
        return Observation(
            "workspace.read",
            ok=True,
            data={"path": str(path.relative_to(self.workspace_dir)), "content": data.decode("utf-8", "replace")},
        )

    async def _workspace_list(self, arguments: JsonDict) -> Observation:
        path = self._resolve(str(arguments.get("path", ".")))
        if not path.is_dir():
            return Observation("workspace.list", ok=False, error="not a directory")
        entries = sorted(p.relative_to(self.workspace_dir).as_posix() for p in path.iterdir())
        return Observation("workspace.list", ok=True, data={"path": str(path), "entries": entries[:500]})

    async def _workspace_write(self, arguments: JsonDict) -> Observation:
        path = self._resolve(str(arguments.get("path", "")))
        content = str(arguments.get("content", ""))
        if len(content.encode("utf-8")) > 1_000_000:
            return Observation("workspace.write", ok=False, error="content too large")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return Observation("workspace.write", ok=True, data={"path": str(path), "bytes": len(content)})

    async def _python_execute(self, arguments: JsonDict) -> Observation:
        code = str(arguments.get("code", ""))
        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            "-c",
            code,
            cwd=self.workspace_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=TOOL_TIMEOUT_SECONDS)
        except TimeoutError:
            proc.kill()
            return Observation("python.execute", ok=False, error="timeout")
        return Observation(
            "python.execute",
            ok=proc.returncode == 0,
            data={
                "exit_code": proc.returncode,
                "stdout": stdout.decode("utf-8", "replace")[:MAX_OUTPUT_BYTES],
                "stderr": stderr.decode("utf-8", "replace")[:MAX_OUTPUT_BYTES],
            },
            error=None if proc.returncode == 0 else f"exit code {proc.returncode}",
        )


def arguments_hash(arguments: JsonDict) -> str:
    return canonical_sha256(arguments)
