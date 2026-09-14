"""Tests for the Tool Broker retry/idempotency contract (T2.8, T2.9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.broker import (
    RETRY_POLICY,
    DeferredStagingWriter,
    SandboxToolBroker,
    check_idempotency,
)
from packages.domain.models.enums import IdempotencyClass
from packages.domain.schemas.observation import Observation
from packages.policy.profiles import load_profile
from packages.sandbox.runtime import SandboxHandle


def _broker(tmp_path: Path) -> SandboxToolBroker:
    handle = _handle(tmp_path)
    return SandboxToolBroker(handle, handle.profile, runtime=None)  # type: ignore[arg-type]


def _transient(tool: str, klass, attempt: int) -> Observation:
    return Observation(
        tool=tool, ok=False, error="sandbox: boom", transient=True, idempotency_class=klass, attempt=attempt
    )


def _handle(tmp_path: Path) -> SandboxHandle:
    profile = load_profile("sealed")
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    return SandboxHandle(
        session_id="s1",
        container_name="c1",
        base_dir=tmp_path / "base",
        workspace_dir=work,
        profile=profile,
    )


@pytest.mark.unit
def test_retry_policy_matches_spec() -> None:
    assert RETRY_POLICY[IdempotencyClass.PURE] == 2
    assert RETRY_POLICY[IdempotencyClass.OBSERVATION] == 0
    assert RETRY_POLICY[IdempotencyClass.IDEMPOTENT] == 1
    assert RETRY_POLICY[IdempotencyClass.NON_IDEMPOTENT] == 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pure_retries_transient_failure(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    calls = 0

    async def fake_run_once(tool, klass, arguments, db, attempt):
        nonlocal calls
        calls += 1
        if calls == 1:
            return _transient(tool, klass, attempt)
        return Observation(tool=tool, ok=True, data={"n": calls}, idempotency_class=klass, attempt=attempt)

    broker._run_once = fake_run_once  # type: ignore[method-assign]
    obs = await broker.execute("workspace.read", {"path": "a.txt"})
    assert obs.ok
    assert calls == 2  # one retry for a pure tool


@pytest.mark.unit
@pytest.mark.asyncio
async def test_non_idempotent_never_retried(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    calls = 0

    async def fake_run_once(tool, klass, arguments, db, attempt):
        nonlocal calls
        calls += 1
        return _transient(tool, klass, attempt)

    broker._run_once = fake_run_once  # type: ignore[method-assign]
    obs = await broker.execute("python.execute", {"code": "print(1)"})
    assert not obs.ok
    assert calls == 1  # no blind retry after start


@pytest.mark.unit
@pytest.mark.asyncio
async def test_observation_never_retried(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import packages.broker.broker as bb
    from packages.policy.tools import ToolSpec, WorkspaceListArgs

    # an observation-class tool (web.search lands in M6): simulate it
    spec = ToolSpec("web.search", "t", IdempotencyClass.OBSERVATION, WorkspaceListArgs)
    real_get_tool = bb.get_tool

    def fake_get_tool(name):
        return spec if name == "web.search" else real_get_tool(name)

    monkeypatch.setattr(bb, "get_tool", fake_get_tool)
    broker = _broker(tmp_path)
    calls = 0

    async def fake_run_once(tool, klass, arguments, db, attempt):
        nonlocal calls
        calls += 1
        return _transient(tool, klass, attempt)

    broker._run_once = fake_run_once  # type: ignore[method-assign]
    await broker.execute("web.search", {"query": "x"})
    assert calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_pure_exhausts_retries(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    calls = 0

    async def fake_run_once(tool, klass, arguments, db, attempt):
        nonlocal calls
        calls += 1
        return _transient(tool, klass, attempt)

    broker._run_once = fake_run_once  # type: ignore[method-assign]
    obs = await broker.execute("workspace.read", {"path": "a.txt"})
    assert not obs.ok
    assert calls == 3  # 1 + 2 retries


@pytest.mark.unit
def test_check_idempotency_verdicts() -> None:
    assert check_idempotency(None, "h1").status == "new"
    assert check_idempotency({"arguments_hash": "h1", "state": "completed"}, "h1").status == "replay"
    assert check_idempotency({"arguments_hash": "h1", "state": "started"}, "h1").status == "replay"
    verdict = check_idempotency({"arguments_hash": "h1", "state": "completed"}, "h2")
    assert verdict.status == "conflict"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_unknown_tool(tmp_path: Path) -> None:
    broker = _broker(tmp_path)
    obs = await broker.execute("net.fetch", {"url": "https://x"})
    assert not obs.ok
    assert "unknown tool" in (obs.error or "")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_workspace_roundtrip_and_escape(tmp_path: Path) -> None:
    handle = _handle(tmp_path)
    broker = _broker(tmp_path)
    obs = await broker.execute("workspace.write", {"path": "notes/a.md", "content": "hello"})
    assert obs.ok, obs.error
    assert (handle.workspace_dir / "notes/a.md").read_text() == "hello"

    obs = await broker.execute("workspace.read", {"path": "notes/a.md"})
    assert obs.ok and obs.data["content"] == "hello"

    obs = await broker.execute("workspace.list", {"path": "."})
    assert obs.ok and "notes" in obs.data["entries"]

    # escaping the overlay is a hard error (never transient)
    obs = await broker.execute("workspace.write", {"path": "../outside.txt", "content": "x"})
    assert not obs.ok
    assert "escapes" in (obs.error or "")
    assert not obs.transient
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.unit
@pytest.mark.asyncio
async def test_sandbox_exec_maps_exit_code(tmp_path: Path) -> None:
    from packages.sandbox.runtime import ExecResult

    broker = _broker(tmp_path)

    async def fake_exec(h, cmd, timeout=None):
        return ExecResult(exit_code=3, stdout="out", stderr="err", timed_out=False)

    async def fake_running(h) -> bool:
        return True  # the container survived: a plain exit-code result

    from types import SimpleNamespace

    broker.runtime = SimpleNamespace(exec=fake_exec, is_running=fake_running)  # type: ignore[assignment]
    obs = await broker.execute("shell.execute", {"command": "false"})
    assert not obs.ok
    assert obs.data["exit_code"] == 3
    assert obs.error == "exit code 3"
    assert not obs.transient  # a nonzero exit is a result, not an infra failure


@pytest.mark.unit
@pytest.mark.asyncio
async def test_staging_deferred(tmp_path: Path) -> None:
    handle = _handle(tmp_path)
    writer = DeferredStagingWriter()
    broker = SandboxToolBroker(handle, handle.profile, runtime=None, staging_writer=writer)  # type: ignore[arg-type]
    obs = await broker.execute("question.create", {"text": "Почему 42?"}, db=object())  # type: ignore[arg-type]
    assert obs.ok
    assert obs.data["deferred"] is True
    assert writer.operations == [("question.create", {"text": "Почему 42?"})]
