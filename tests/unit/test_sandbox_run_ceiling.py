"""T7.93: the engine-call ceiling covers the whole CLI call, not only its spawn.

The defect (AGENTS.md §7, T7.92 entry): ``asyncio.wait_for(create_subprocess_exec(...),
timeout)`` guarded only the launch of the engine client; ``proc.communicate()`` was outside
the ceiling — a wedged dockerd/network left the CLI call waiting forever (probe: engine
``sleep 3`` with a 0,5 s ceiling returned rc=0 after 3,003 s without SandboxError). A hung
docker exec/inspect/rm therefore hanged the session indefinitely: neither unknown nor
transient — silence.

All tests here use a FAKE engine: an executable script that behaves like a wedged daemon —
it answers `inspect` (the container-state probe) instantly with "true" and hangs on any
other verb (exec CLI). On the hang path it records its own PID, so "the CLI was killed and
reaped" is checkable after _run returns: no live process survives at that PID. The script
hangs through ``exec sleep`` on purpose — an exec'd replacement never leaves a child behind,
so an unclosed ceiling could not hide an orphaned sleep.

Timing assertions use a generous margin (ceiling 1 s checked against < 10 s): the tests
are deterministic in what they assert (SandboxError raised or not, process dead or alive,
poll and attempt counts) and never depend on a tight wall-clock threshold. The product's
per-call timeouts (15/30/60/+15 s) are NOT redefined here — see docs/STATUS.md T7.93 p.2.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from packages.broker import SandboxToolBroker
from packages.domain.models.enums import IdempotencyClass
from packages.sandbox.runtime import (
    ContainerSandboxRuntime,
    SandboxError,
    SandboxHandle,
    SandboxHealthError,
    SandboxProfile,
)

# Dyadic constants for the broker-window polls (the same style as the T7.92 tests):
# the poll count is exactly SECONDS/INTERVAL and does not depend on wall-clock.
_WINDOW_SECONDS = 0.1875
_WINDOW_INTERVAL = 0.0625

# The fake engine hangs for this long (well above every ceiling exercised here: 0,3 s in
# the direct _run tests and profile+15 = 16 s in the broker test). On the OLD code the CLI
# was never interrupted, so a red-check run simply waits this out and completes.
_ENGINE_HANG_SECONDS = 30


def _engine_script(tmp_path: Path) -> Path:
    """A fake engine that answers `inspect` instantly and hangs on every other verb."""
    script = tmp_path / "wedged-engine.sh"
    script.write_text(
        "#!/bin/sh\n"
        'echo "$*" >> "$T793_ENGINE_LOG"\n'
        'if [ "$1" = inspect ]; then echo true; exit 0; fi\n'
        'echo $$ > "$T793_ENGINE_PID_FILE"\n'
        f"exec sleep {_ENGINE_HANG_SECONDS}\n",
        encoding="utf-8",
    )
    script.chmod(0o755)
    return script


def _wedged_runtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> ContainerSandboxRuntime:
    log = tmp_path / "engine-calls.log"
    pid_file = tmp_path / "engine-cli.pid"
    monkeypatch.setenv("T793_ENGINE_LOG", str(log))
    monkeypatch.setenv("T793_ENGINE_PID_FILE", str(pid_file))
    return ContainerSandboxRuntime(
        image="noezema-sandbox:unused-in-these-tests",
        engine=str(_engine_script(tmp_path)),
        work_root=tmp_path,
    )


def _hang_cli_pid(tmp_path: Path) -> int:
    return int((tmp_path / "engine-cli.pid").read_text(encoding="utf-8").strip())


def _cli_still_alive(pid: int) -> bool:
    """True if a process is still running under this PID.

    A killed-but-never-reaped CLI (a zombie) also answers signal 0; a reaped-and-vanished
    PID raises ProcessLookupError. /proc/<pid>/cmdline is consulted so an already-zombie
    child counts as ALIVE (the reap is part of what these tests demand).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return True  # signal 0 succeeded — cannot prove it is gone
    return any(part for part in cmdline.split(b"\x00") if part)


@pytest.mark.unit
@pytest.mark.asyncio
async def test_hanging_engine_cli_hits_the_ceiling_and_is_killed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A hung engine CLI must end in the classified SandboxError, not in silence.

    Ceiling 1 s vs a 30 s hang: with the T7.93 ceiling _run returns shortly after the
    ceiling and kills+reaps the CLI; without it (the old code) communicate() waits out the
    whole hang and reports rc=0 — the exact defect this task closes.
    """
    rt = _wedged_runtime(tmp_path, monkeypatch)

    started = time.monotonic()
    with pytest.raises(SandboxError) as excinfo:
        await rt._run(
            ["exec", "c1", "timeout", "-k", "5", "1", "bash", "-c", "sleep 30"], timeout=1.0
        )
    elapsed = time.monotonic() - started

    # the same class and message as before — broker classification (T7.92) applies unchanged
    assert isinstance(excinfo.value, SandboxError)
    assert str(excinfo.value) == "engine command timed out: exec c1"
    # generous margin only against a hang that never returns: expected ~0,3 s, bound 10 s
    assert elapsed < 10.0, f"_run waited {elapsed:.2f} s for a hung engine CLI (defect is back)"

    pid = _hang_cli_pid(tmp_path)
    assert not _cli_still_alive(pid), f"engine CLI pid {pid} survived the ceiling (orphan/zombie)"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_fast_engine_call_is_unchanged(tmp_path: Path) -> None:
    """Insurance for the unchanged happy path: a fast call returns rc/stdout as before."""
    rt = ContainerSandboxRuntime(
        image="noezema-sandbox:unused-in-these-tests", engine="echo", work_root=tmp_path
    )
    rc, out, err = await rt._run(["hello-t793"], timeout=10.0)
    assert rc == 0
    assert out.strip() == "hello-t793"
    assert err == ""


@pytest.mark.unit
@pytest.mark.asyncio
async def test_missing_engine_still_raises_health_error(tmp_path: Path) -> None:
    """Insurance for the unchanged failure class: a missing engine binary is a
    SandboxHealthError before and after the ceiling change (the spawn failure is not a
    timeout)."""
    rt = ContainerSandboxRuntime(
        image="noezema-sandbox:unused-in-these-tests",
        engine=str(tmp_path / "no-such-engine-binary"),
        work_root=tmp_path,
    )
    with pytest.raises(SandboxHealthError) as excinfo:
        await rt._run(["inspect", "c1"], timeout=5.0)
    assert str(excinfo.value).startswith("container engine not found:")


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cli_hang_with_live_container_is_classified_transient_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The broker scenario over the REAL runtime path: docker exec hangs at CLI level while
    the container is alive (the fake engine answers `inspect` with "true"). The ceiling
    turns the hang into SandboxError, and T7.92 rules classify it transient (never unknown),
    without repeating the command.

    Profile timeout_seconds=1 here is a fixture knob for the profile value only: the call-site
    margin stays +15 s exactly as in production, so the exec ceiling is 16 s — well inside the
    30 s hang this fake engine performs."""
    from packages.broker import broker as bb

    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_SECONDS", _WINDOW_SECONDS)
    monkeypatch.setattr(bb, "CONTAINER_DEATH_RECHECK_INTERVAL", _WINDOW_INTERVAL)

    rt = _wedged_runtime(tmp_path, monkeypatch)
    profile = SandboxProfile(
        name="t793-wedged-engine",
        network="none",
        read_only_rootfs=True,
        no_new_privileges=True,
        resource_limits={
            "cpu": "1.0",
            "memory_mb": 512,
            "max_processes": 32,
            "timeout_seconds": 1,  # fixture knob; the +15 s CLI margin is not redefined
            "max_file_size_mb": 50,
        },
    )
    work = tmp_path / "work"
    work.mkdir(parents=True, exist_ok=True)
    handle = SandboxHandle(
        session_id="s-t793",
        container_name="c1",
        base_dir=tmp_path / "base",
        workspace_dir=work,
        profile=profile,
    )
    broker = SandboxToolBroker(handle, profile, rt)

    started = time.monotonic()
    obs = await broker.execute("shell.execute", {"command": "sleep 999"})
    elapsed = time.monotonic() - started

    # engine-level failure with a live container: transient per T7.92, never unknown
    assert not obs.ok
    assert obs.error == "sandbox: engine command timed out: exec c1", obs.error
    assert obs.transient, obs.error
    assert not obs.result_unknown, obs.error
    assert obs.idempotency_class is IdempotencyClass.NON_IDEMPOTENT

    # the command was attempted exactly once (no blind retry of a non-idempotent tool):
    # count the engine's own invocation log, not any mock counter
    calls = (tmp_path / "engine-calls.log").read_text(encoding="utf-8").splitlines()
    exec_calls = [line for line in calls if line.startswith("exec c1 timeout -k 5 ")]
    probe_calls = [line for line in calls if line.startswith("inspect ")]
    assert len(exec_calls) == 1, f"the engine CLI was asked to run the command {len(exec_calls)} times"
    polls = 1 + round(_WINDOW_SECONDS / _WINDOW_INTERVAL)
    assert len(probe_calls) == polls  # one first probe + the bounded window (all "true")

    # generous bound: the exec ceiling is 16 s and the broker window adds < 0,2 s;
    # the old code never returned early at all (it waited out the 30 s hang with rc=0)
    assert elapsed < 45.0, f"broker took {elapsed:.2f} s to reject a hung engine CLI"

    pid = _hang_cli_pid(tmp_path)
    assert not _cli_still_alive(pid), f"engine CLI pid {pid} survived the ceiling (orphan/zombie)"
