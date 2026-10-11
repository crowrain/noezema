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
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import IdempotencyClass
from packages.domain.schemas.observation import Observation
from packages.policy.profiles import load_profile
from packages.sandbox.runtime import SandboxError, SandboxHandle, SandboxHealthError


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


# ── T7.91: the engine still reports a killed container as running for a moment ─


class _FakeRuntime:
    """exec always fails with one exit code; is_running answers from a scripted
    list (the last answer repeats forever) and counts its calls."""

    def __init__(self, exit_code: int, running: list[bool]) -> None:
        self.exit_code = exit_code
        self.running = running
        self.exec_calls = 0
        self.running_calls = 0

    async def exec(self, handle: object, command: list[str], timeout: float | None = None):
        from packages.sandbox.runtime import ExecResult

        self.exec_calls += 1
        return ExecResult(exit_code=self.exit_code, stdout="", stderr="")

    async def is_running(self, handle: object) -> bool:
        answer = self.running[min(self.running_calls, len(self.running) - 1)]
        self.running_calls += 1
        return answer


def _broker_with(tmp_path: Path, runtime: _FakeRuntime) -> SandboxToolBroker:
    handle = _handle(tmp_path)
    return SandboxToolBroker(handle, handle.profile, runtime)  # type: ignore[arg-type]


@pytest.mark.unit
def test_only_kill_consistent_exit_codes_get_the_second_look() -> None:
    """T7.91: a container kill reaches the exec'd process as a fatal signal
    (137 = 128+SIGKILL, 143 = 128+SIGTERM) or as a docker CLI refusal (125). A
    command's own nonzero exit is NOT kill-consistent — it is an answer."""
    from packages.broker.broker import exit_is_consistent_with_container_death

    assert exit_is_consistent_with_container_death(137)
    assert exit_is_consistent_with_container_death(143)
    assert exit_is_consistent_with_container_death(125)
    assert not exit_is_consistent_with_container_death(1)
    assert not exit_is_consistent_with_container_death(7)
    assert not exit_is_consistent_with_container_death(126)
    assert not exit_is_consistent_with_container_death(127)


@pytest.mark.unit
def test_container_death_recheck_window_is_bounded() -> None:
    """The second look is a fixed, small wall-clock budget — never a stall and
    never a retry of the action."""
    from packages.broker import broker as bb

    assert 0 < bb.CONTAINER_DEATH_RECHECK_INTERVAL <= bb.CONTAINER_DEATH_RECHECK_SECONDS
    assert bb.CONTAINER_DEATH_RECHECK_SECONDS <= 2.0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signal_death_while_engine_still_says_running_is_unknown(tmp_path: Path) -> None:
    """T7.91 (the CI flake): `docker exec` returned 137 while `docker inspect` has
    not flipped State.Running yet. The outcome is UNKNOWN — the command may have
    executed partially — not a clean "exit code 137" failure."""
    rt = _FakeRuntime(137, [True, True, False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert not obs.ok
    assert obs.result_unknown, obs.error
    assert obs.error == "container gone mid-execution"
    assert not obs.transient  # unknown outcomes are never retried
    assert obs.idempotency_class is IdempotencyClass.NON_IDEMPOTENT
    assert rt.exec_calls == 1  # the command was executed exactly once
    assert rt.running_calls == 3  # the stale answer, then the flip inside the window


@pytest.mark.unit
@pytest.mark.asyncio
async def test_docker_cli_failure_while_engine_still_says_running_is_unknown(tmp_path: Path) -> None:
    """125 = the docker CLI could not run the exec at all: the container may have
    been killed while the exec was starting, so this is kill-consistent too."""
    rt = _FakeRuntime(125, [True, False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "true"})

    assert not obs.ok
    assert obs.result_unknown
    assert obs.error == "container gone mid-execution"
    assert rt.exec_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_plain_failure_with_live_container_is_not_unknown(tmp_path: Path) -> None:
    """A command's own nonzero exit with a live container stays a result — and pays
    nothing for the T7.91 second look (one state check only)."""
    rt = _FakeRuntime(1, [True])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "false"})

    assert not obs.ok
    assert not obs.result_unknown
    assert obs.error == "exit code 1"
    assert not obs.transient
    assert rt.running_calls == 1  # no bounded wait for a kill-inconsistent code


@pytest.mark.unit
@pytest.mark.asyncio
async def test_container_gone_at_once_is_unknown_without_waiting(tmp_path: Path) -> None:
    """The T2.22 fast path is unchanged: the engine already says "gone" — unknown
    immediately, no extra polls."""
    rt = _FakeRuntime(137, [False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert obs.result_unknown
    assert rt.running_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_signal_death_in_a_live_container_stays_a_plain_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`kill -9 $$` inside the command: the container is ALIVE and docker reports
    the real exit code. After the bounded window the broker must call it a plain
    failure — a false unknown would fail the whole session at the safe boundary over
    an answer the command actually gave."""
    from packages.broker import broker as bb

    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_SECONDS", 0.15)
    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_INTERVAL", 0.05)
    rt = _FakeRuntime(137, [True])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "kill -9 $$"})

    assert not obs.ok
    assert not obs.result_unknown, obs.error
    assert obs.error == "exit code 137"
    assert not obs.transient
    assert rt.exec_calls == 1


# ── T7.92: the same second look after an ENGINE-level failure (SandboxError) ─────

# Dyadic constants: the number of window probes is exactly SECONDS/INTERVAL, so the
# assertions below count POLLS and never depend on wall-clock timing.
_WINDOW_SECONDS = 0.1875
_WINDOW_INTERVAL = 0.0625


class _EngineErrorRuntime:
    """exec always raises an engine-level error (the failure mode under test);
    is_running answers from a scripted list (the last answer repeats forever) and
    counts its calls. No command is ever repeated."""

    def __init__(self, error: Exception, running: list[bool] | None = None) -> None:
        self.error = error
        self.running = running if running is not None else [True]
        self.exec_calls = 0
        self.running_calls = 0

    async def exec(self, handle: object, command: list[str], timeout: float | None = None):
        self.exec_calls += 1
        raise self.error

    async def is_running(self, handle: object) -> bool:
        answer = self.running[min(self.running_calls, len(self.running) - 1)]
        self.running_calls += 1
        return answer


class _BrokenProbeRuntime(_EngineErrorRuntime):
    """The state probe itself fails — it proves nothing about the container."""

    async def is_running(self, handle: object) -> bool:
        self.running_calls += 1
        raise RuntimeError("docker inspect failed")


def _small_window(monkeypatch: pytest.MonkeyPatch) -> None:
    from packages.broker import broker as bb

    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_SECONDS", _WINDOW_SECONDS)
    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_INTERVAL", _WINDOW_INTERVAL)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_failure_while_container_flips_inside_window_is_unknown(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7.92: `docker exec` never answered (engine command timed out) and the engine
    still says "running". Straight after a container death that answer is not
    conclusive — inside the bounded window it flips to "not running", so the outcome
    is UNKNOWN, not a retryable infra failure."""
    _small_window(monkeypatch)
    rt = _EngineErrorRuntime(SandboxError("engine command timed out: exec c1"), [True, True, False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert not obs.ok
    assert obs.result_unknown, obs.error
    # the message keeps the engine-level reason — only the classification changed
    assert obs.error == "sandbox: engine command timed out: exec c1"
    assert not obs.transient  # unknown outcomes are never retried
    assert obs.idempotency_class is IdempotencyClass.NON_IDEMPOTENT
    assert rt.exec_calls == 1  # the command was executed exactly once
    assert rt.running_calls == 3  # the stale answer, then the flip inside the window


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_failure_flips_to_unknown_on_python_execute_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The rule covers both container tools, not only shell.execute."""
    _small_window(monkeypatch)
    rt = _EngineErrorRuntime(SandboxError("engine command timed out: exec c1"), [True, False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("python.execute", {"code": "import time; time.sleep(30)"})

    assert obs.result_unknown, obs.error
    # the message keeps the engine-level reason — only the classification changed
    assert obs.error == "sandbox: engine command timed out: exec c1"
    assert not obs.transient
    assert rt.exec_calls == 1
    assert rt.running_calls == 2


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_failure_with_live_container_stays_transient_after_second_look(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A live container until the end of the window keeps the PREVIOUS classification
    (transient engine failure, never unknown) — the fix only adds the bounded second
    look, it does not invent an unknown outcome. The poll count proves the second look
    happened and that it is bounded by the constant."""
    _small_window(monkeypatch)
    rt = _EngineErrorRuntime(SandboxError("engine command timed out: exec c1"), [True])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert not obs.ok
    assert obs.transient
    assert not obs.result_unknown, obs.error
    assert (obs.error or "").startswith("sandbox: engine command timed out")
    assert rt.exec_calls == 1
    polls = 1 + round(_WINDOW_SECONDS / _WINDOW_INTERVAL)
    assert rt.running_calls == polls  # one first probe + the bounded window


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_failure_with_container_already_gone_is_unknown_without_waiting(
    tmp_path: Path,
) -> None:
    """The T2.22 fast path in this branch is unchanged: the engine already answers
    "not running" — unknown immediately, no extra polls."""
    rt = _EngineErrorRuntime(SandboxError("engine command timed out: exec c1"), [False])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert obs.result_unknown
    assert not obs.transient
    assert rt.exec_calls == 1
    assert rt.running_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_broken_state_probe_after_engine_failure_does_not_wait(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A probe that itself raises proves nothing about the container — unchanged
    behavior (transient, no waiting): unknown is declared only on a real answer."""
    _small_window(monkeypatch)
    rt = _BrokenProbeRuntime(SandboxError("engine command timed out: exec c1"))
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert not obs.ok
    assert obs.transient
    assert not obs.result_unknown, obs.error
    assert (obs.error or "").startswith("sandbox:")
    assert rt.exec_calls == 1
    assert rt.running_calls == 1  # the failing probe is not retried in a loop


@pytest.mark.unit
@pytest.mark.asyncio
async def test_health_error_is_not_rechecked_and_stays_transient(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SandboxHealthError = the ENGINE is unusable (`container engine not found`): it
    cannot be asked again, so the bounded window would only repeat a failing call. The
    classification stays transient with exactly one probe."""
    _small_window(monkeypatch)
    rt = _EngineErrorRuntime(SandboxHealthError("container engine not found: docker"), [True])
    broker = _broker_with(tmp_path, rt)

    obs = await broker.execute("shell.execute", {"command": "sleep 30"})

    assert not obs.ok
    assert obs.transient
    assert not obs.result_unknown, obs.error
    assert (obs.error or "").startswith("sandbox: container engine not found")
    assert rt.exec_calls == 1
    assert rt.running_calls == 1


@pytest.mark.unit
@pytest.mark.asyncio
async def test_engine_failure_outside_container_tools_probes_nothing(tmp_path: Path) -> None:
    """The state re-check stays scoped to shell.execute/python.execute: a host-side
    staging failure is not a container command, so nothing about the container may be
    claimed from it."""
    class _EngineBrokenStaging:
        async def write(self, op: str, payload: JsonDict, db: object) -> JsonDict:
            raise SandboxError("engine command timed out: exec c1")

    rt = _EngineErrorRuntime(SandboxError("engine command timed out: exec c1"), [True])
    handle = _handle(tmp_path)
    broker = SandboxToolBroker(
        handle, handle.profile, rt, staging_writer=_EngineBrokenStaging()  # type: ignore[arg-type]
    )

    obs = await broker.execute("question.create", {"text": "Почему 42?"}, db=object())  # type: ignore[arg-type]

    assert not obs.ok
    assert obs.transient
    assert not obs.result_unknown
    assert rt.running_calls == 0
