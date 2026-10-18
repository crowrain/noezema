"""Unit: an external cancellation must not orphan the engine CLI, the container or the overlay (T7.95).

Fake container engine only — no docker, no podman, no DB, no real wall-clock thresholds: every
ceiling here is a generous bound, never a measurement. The wedged verb sleeps far longer than any
ceiling used below, so a test can only finish if the code under test KILLS what it cancelled.

What these tests pin down (each one was verified red on 6ee80c5):
  (а) `_run`: cancelling a call in flight kills and reaps the CLI client, CancelledError unchanged;
  (б/г) `start`: once `create` handed the container name to the engine, cancellation AND ceiling
      expiry end with a bounded `rm -f <that exact name>`; an engine that ANSWERED "no" (`rc != 0`)
      is the one case where nothing is removed — see docs/STATUS.md T7.95;
  (в) `close_session`: cancellation during `destroy` still finishes the removal under its own
      ceiling (or names the leak), always removes the session overlay dir, and re-raises the
      cancellation; a repeated cancellation may not hang it.

T7.95a adds the CONFIRMATION layer (verified red on d0e27c1, measurements in docs/STATUS.md T7.95a):
  (1) an `rm -f` refused with "removal … is already in progress" is NOT a leak — dockerd is removing
      on its own; the cleanup waits for the engine to confirm absence and names nothing;
  (2) an `rm -f` that answered rc=0 while the container then appears (a create dockerd finished after
      our client died) triggers exactly one re-removal, still no LEAKED line;
  (3) a container the engine keeps reporting present gets exactly ONE LEAKED line saying "present" at
      the ceiling's expiry; an engine that never gives a conclusive answer gets exactly one line
      saying "absence UNCONFIRMED";
  (4) a `start` that ended without a handle — cancelled, ceiling-expired or engine-refused — removes
      `<work_root>/<session_id>`, keeping its original exception/cancellation byte-identical.

The fake engine now MODELS CONTAINER EXISTENCE (create records it, rm -f clears it, inspect answers
"no such object" when there is nothing), because a confirmation-only fix cannot be tested against an
engine that never distinguishes present from absent; measured docker 29 semantics per docs/STATUS.md.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pytest

import packages.sandbox.runtime as runtime_module
from apps.orchestrator.tool_executors import SandboxSessionExecutor
from packages.sandbox.runtime import (
    ContainerSandboxRuntime,
    SandboxError,
    SandboxHandle,
    SandboxProfile,
)

pytestmark = [pytest.mark.unit]

SESSION_ID = uuid.uuid4()
CONTAINER_NAME = f"noezema-sb-{str(SESSION_ID).replace('-', '')[:12]}"

#: How long a wedged engine verb sleeps. Far above every ceiling below: the test finishes only
#: because the cancelled call is killed, never because the fake engine grew tired.
WEDGE_SECONDS = 20.0
#: Test knobs of the product cleanup ceilings (product values are NOT changed by these tests).
# T7.95a: the ceiling knob must fit the whole remove→confirm sequence inside it, and a confirmation
# needs its full absent-window plus room for a re-removal — hence 4.0 s where T7.95 had 1.0 s.
# Assertions ("< 10 s" bounds) are unchanged; only the knob of the lowered product ceiling grew.
CLEANUP_CEILING_SECONDS = 4.0
CREATE_CEILING_SECONDS = 1.0
START_VERB_CEILING_SECONDS = 1.0
REAP_CEILING_SECONDS = 1.0

PROFILE = SandboxProfile(
    name="unit-cancel",
    network="none",
    resource_limits={
        "cpu": "1.0",
        "memory_mb": 256,
        "max_processes": 32,
        "timeout_seconds": 1,
        "max_file_size_mb": 50,
    },
)


# ── fake engine + process probes ────────────────────────────────────────────


def _engine(
    tmp_path: Path,
    *,
    wedge_on: str = "",
    wedge_once: bool = False,
    fail_on: str = "",
    refuse_in_progress_once: bool = False,
    reappear_after_rm: bool = False,
    stubborn_rm: bool = False,
) -> str:
    """Path of a fake container engine. It journals every call, wedges on `wedge_on`, and MODELS
    whether the container exists (T7.95a): `create` registers it when it completes; `rm -f` clears it;
    `inspect` answers presence (`rc 0`) or "error: no such object" (`rc 1`) — the exact shapes measured
    on docker 29 (docs/STATUS.md T7.95a), which the confirmation fix reads.

    Scenario flags model the measured behaviours that fooled d0e27c1:
      `refuse_in_progress_once` — the first completed `rm -f` is refused with «removal … already in
        progress» while dockerd removes the container behind our back (manager probe defect 1);
      `reappear_after_rm` — `rm -f` answers rc=0 for a name whose create is not finished and the
        container APPEARS on the next inspect (probe defect 2, measured lag ≤ ~0,13 s);
      `stubborn_rm` — every `rm -f` answers success but the container never goes away.

    A wedged invocation appends its own PID to `engine-pids.log` so the test can prove afterwards
    that every CLI client it cancelled is really gone (killed AND reaped).
    """
    calls = tmp_path / "engine-calls.log"
    pids = tmp_path / "engine-pids.log"
    present = tmp_path / ".sb-present"  # marker: the engine currently has this container
    reap = tmp_path / ".sb-reap"  # a delayed create will materialise the container again
    reaped = tmp_path / ".sb-reaped"  # the delayed materialisation already happened once
    refused = tmp_path / ".sb-refused"  # the one "already in progress" refusal has been given
    lines = [
        "#!/bin/sh",
        f'J="{calls}"; P="{pids}"; ST="{present}"; AP="{reap}"; RD="{reaped}"; RQ="{refused}"',
        'echo "$*" >> "$J"',
    ]
    if wedge_on:
        lines.append(f'if [ "$1" = {wedge_on} ]; then')
        if wedge_once:
            # only the FIRST such verb wedges; the engine answers every later call normally
            lines.append(
                f'[ -e "$P.once" ] && echo "engine answered after one wedge"'
                f' || {{ touch "$P.once"; echo $$ >> "$P"; exec sleep {WEDGE_SECONDS}; }}'
            )
        else:
            lines.append('echo $$ >> "$P"')
            lines.append(f"exec sleep {WEDGE_SECONDS}")
        lines.append("fi")
    if fail_on:
        lines.append(f'if [ "$1" = {fail_on} ]; then echo "fake engine says no" >&2; exit 1; fi')
    lines.append('case "$1" in')
    lines.append("  create) touch \"$ST\" ;;")  # a create that COMPLETED registers the container
    lines.append("  start|exec|logs) : ;;")
    if refuse_in_progress_once:
        # the FIRST completed rm -f is refused while dockerd removes behind our back; later ones clear normally
        lines.extend(
            [
                '  rm) if [ ! -e "$RQ" ]; then',
                '      touch "$RQ"; rm -f "$ST"',
                '      echo "Error response from daemon: removal of container $3 is already in progress" >&2',
                "      exit 1",
                "    fi",
                '    rm -f "$ST" ;;',
            ]
        )
    elif reappear_after_rm:
        lines.append('  rm) if [ ! -e "$RD" ]; then touch "$RD" "$AP"; else rm -f "$ST"; fi ;;')
    elif stubborn_rm:
        lines.append('  rm) touch "$ST" ;;')  # "success" that removes nothing — the container stays
    else:
        lines.append('  rm) rm -f "$ST" ;;')
    lines.append('  inspect)')
    lines.append('    if [ -e "$AP" ]; then rm -f "$AP"; touch "$ST"; echo created; exit 0; fi')
    lines.append('    if [ -e "$ST" ]; then echo running; exit 0; fi')
    lines.append('    echo "error: no such object: $2" >&2; exit 1 ;;')
    lines.append("  *) : ;;")
    lines.append("esac")
    lines.append("exit 0")
    script = tmp_path / "engine.sh"
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(0o755)
    return str(script)


def _runtime(tmp_path: Path, engine: str) -> ContainerSandboxRuntime:
    return ContainerSandboxRuntime(image="fake-image:test", engine=engine, work_root=tmp_path / "sandbox")


def _journal(tmp_path: Path) -> list[str]:
    calls = tmp_path / "engine-calls.log"
    return calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []


def _cancelled_pids(tmp_path: Path) -> list[int]:
    pids = tmp_path / "engine-pids.log"
    return [int(line) for line in pids.read_text(encoding="utf-8").split()] if pids.exists() else []


def _process_state(pid: int) -> str:
    """"gone" when the PID is reaped; otherwise the process state letter ("S", "Z", ...)."""
    try:
        after_paren = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split(") ", 1)[1]
    except (FileNotFoundError, IndexError):
        return "gone"
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return "gone"
    return after_paren.split(" ", 1)[0]


async def _wait_until(description: str, predicate: Any, limit: float = 8.0) -> None:
    """Poll a condition with a generous bound — this suite must not lean on tight timing."""
    started = time.monotonic()
    while not predicate():
        if time.monotonic() - started > limit:
            pytest.fail(f"condition never held within {limit:.1f} s: {description}")
        await asyncio.sleep(0.05)


async def _cancel_after_wedge(coro: Any, tmp_path: Path) -> tuple[BaseException | None, float]:
    """Run `coro`, wait until the fake engine reports its PID, cancel the task, return the outcome."""
    task = asyncio.ensure_future(coro)
    await _wait_until("the fake engine started and recorded its PID", lambda: bool(_cancelled_pids(tmp_path)))
    started = time.monotonic()
    task.cancel()
    outcome: BaseException | None = None
    try:
        await task
    except asyncio.CancelledError as exc:
        outcome = exc
    except Exception as exc:  # a cancellation must never come back as something else — the tests assert this
        outcome = exc
    return outcome, time.monotonic() - started


@pytest.fixture(autouse=True)
def _cleanup_ceilings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Lower the CLEANUP ceilings only (raising=False: on 6ee80c5 some of them do not exist yet,
    which is exactly why the same tests are red there rather than erroring)."""
    monkeypatch.setattr(runtime_module, "_CLEANUP_CEILING_SECONDS", CLEANUP_CEILING_SECONDS, raising=False)
    monkeypatch.setattr(runtime_module, "_CREATE_CEILING_SECONDS", CREATE_CEILING_SECONDS, raising=False)
    monkeypatch.setattr(runtime_module, "_START_VERB_CEILING_SECONDS", START_VERB_CEILING_SECONDS, raising=False)
    monkeypatch.setattr(runtime_module, "_REAP_CEILING_SECONDS", REAP_CEILING_SECONDS, raising=False)


# ── (а) cancellation inside a call in flight: the CLI client must die with it ─────────


@pytest.mark.asyncio
async def test_cancel_during_a_wedged_exec_kills_and_reaps_the_engine_cli(tmp_path: Path) -> None:
    """`wait_for` cancels its inner coroutine only — without T7.95 the docker client stayed alive."""
    engine = _engine(tmp_path, wedge_on="exec")
    rt = _runtime(tmp_path, engine)

    outcome, elapsed = await _cancel_after_wedge(
        rt._run(["exec", CONTAINER_NAME, "timeout", "-k", "5", "1", "echo", "hi"], timeout=30.0),
        tmp_path,
    )

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert elapsed < 10.0, "a cancelled engine call must return control at once, not wait out its ceiling"
    pids = _cancelled_pids(tmp_path)
    assert pids, "the fake engine never recorded its PID"
    await _wait_until(
        "every cancelled engine CLI is gone (killed and reaped)",
        lambda: all(_process_state(pid) == "gone" for pid in pids),
    )


@pytest.mark.asyncio
async def test_uncancelled_engine_calls_are_untouched(tmp_path: Path) -> None:
    """The fast path gains no cleanup call and no extra verb (T7.93 tests still describe it)."""
    engine = _engine(tmp_path)
    rt = _runtime(tmp_path, engine)

    rc, out, _err = await rt._run(["exec", CONTAINER_NAME, "echo", "ok"], timeout=5.0)

    assert (rc, out.strip()) == (0, "")  # the fake engine only journals what it was asked to run
    assert _journal(tmp_path) == [f"exec {CONTAINER_NAME} echo ok"]


# ── (б/г) cancellation after the name reached the engine: remove what we handed over ──


@pytest.mark.asyncio
async def test_cancel_during_create_removes_the_container_and_propagates(tmp_path: Path) -> None:
    engine = _engine(tmp_path, wedge_on="create")
    rt = _runtime(tmp_path, engine)

    outcome, elapsed = await _cancel_after_wedge(rt.start(str(SESSION_ID), None, PROFILE), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert elapsed < 10.0
    calls = _journal(tmp_path)
    assert any(line.startswith("create --name ") and CONTAINER_NAME in line for line in calls), calls
    assert f"rm -f {CONTAINER_NAME}" in calls, f"no removal of the handed-over name in {calls}"
    await _wait_until(
        "every cancelled engine CLI is gone",
        lambda: all(_process_state(pid) == "gone" for pid in _cancelled_pids(tmp_path)),
    )


@pytest.mark.asyncio
async def test_cancel_during_start_verb_removes_the_container_it_created(tmp_path: Path) -> None:
    engine = _engine(tmp_path, wedge_on="start")
    rt = _runtime(tmp_path, engine)

    outcome, elapsed = await _cancel_after_wedge(rt.start(str(SESSION_ID), None, PROFILE), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert elapsed < 10.0
    calls = _journal(tmp_path)
    assert any(line == f"start {CONTAINER_NAME}" for line in calls), calls
    assert f"rm -f {CONTAINER_NAME}" in calls, f"no removal of the created container in {calls}"


@pytest.mark.asyncio
async def test_create_ceiling_expiry_also_removes_the_container_and_keeps_its_error(tmp_path: Path) -> None:
    """T7.93 killed the CLI; T7.95 also removes the container, and still raises the ceiling error."""
    engine = _engine(tmp_path, wedge_on="create")
    rt = _runtime(tmp_path, engine)

    with pytest.raises(SandboxError) as excinfo:
        await rt.start(str(SESSION_ID), None, PROFILE)

    assert "engine command timed out" in str(excinfo.value)
    calls = _journal(tmp_path)
    assert f"rm -f {CONTAINER_NAME}" in calls, f"a ceiling-expired create left the name with the engine: {calls}"
    await _wait_until(
        "the wedged create CLI was killed and reaped",
        lambda: all(_process_state(pid) == "gone" for pid in _cancelled_pids(tmp_path)),
    )


@pytest.mark.asyncio
async def test_engine_that_answered_create_with_failure_is_unchanged_and_removes_nothing(
    tmp_path: Path,
) -> None:
    """`rc != 0` is the one documented exception: the engine ANSWERED that there is no container.

    Removing a name the engine never confirmed would be the only path able to touch a foreign
    container sharing the `noezema-sb-` prefix (docs/STATUS.md T7.95, orphan-sweep proposal).
    """
    engine = _engine(tmp_path, fail_on="create")
    rt = _runtime(tmp_path, engine)

    with pytest.raises(SandboxError) as excinfo:
        await rt.start(str(SESSION_ID), None, PROFILE)

    assert str(excinfo.value).startswith("failed to create sandbox container: fake engine says no")
    assert not any(line.startswith("rm -f") for line in _journal(tmp_path)), _journal(tmp_path)


@pytest.mark.asyncio
async def test_engine_that_refuses_to_start_still_removes_the_container_with_its_own_error(
    tmp_path: Path,
) -> None:
    """Behaviour of T2.3 kept byte-identical; only the cleanup ceiling behind it is bounded now."""
    engine = _engine(tmp_path, fail_on="start")
    rt = _runtime(tmp_path, engine)

    with pytest.raises(SandboxError) as excinfo:
        await rt.start(str(SESSION_ID), None, PROFILE)

    assert str(excinfo.value).startswith("failed to start sandbox container: fake engine says no")
    calls = _journal(tmp_path)
    assert f"rm -f {CONTAINER_NAME}" in calls, calls


# ── (в) cancellation during `destroy` inside close_session ────────────────────────────


async def _open_close_executor(tmp_path: Path, engine: str) -> tuple[SandboxSessionExecutor, SandboxHandle]:
    rt = _runtime(tmp_path, engine)
    handle = await rt.start(str(SESSION_ID), None, PROFILE)
    executor = SandboxSessionExecutor(rt)
    executor._handle = handle  # exactly what open_session() assigns
    return executor, handle


@pytest.mark.asyncio
async def test_cancel_during_destroy_finishes_the_removal_and_removes_the_overlay(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The engine answers the bounded retry: no leak to name, session dir gone, CancelledError out."""
    executor, handle = await _open_close_executor(tmp_path, _engine(tmp_path, wedge_on="rm", wedge_once=True))
    session_dir = handle.workspace_dir.parent
    assert session_dir.is_dir()

    with caplog.at_level(logging.WARNING):
        outcome, elapsed = await _cancel_after_wedge(executor.close_session(), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert elapsed < 10.0
    calls = _journal(tmp_path)
    assert calls.count(f"rm -f {CONTAINER_NAME}") == 2, f"destroy + the bounded retry expected in {calls}"
    assert not session_dir.exists(), "a cancelled close_session left the session overlay behind"
    assert CONTAINER_NAME not in caplog.text.replace("closed session", ""), caplog.text


@pytest.mark.asyncio
async def test_cancel_during_a_wedged_destroy_names_the_leak_and_removes_the_overlay(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The engine is wedged for real: the leak gets a name in the log, the dir still goes away."""
    executor, handle = await _open_close_executor(tmp_path, _engine(tmp_path, wedge_on="rm"))
    session_dir = handle.workspace_dir.parent

    with caplog.at_level(logging.WARNING):
        outcome, elapsed = await _cancel_after_wedge(executor.close_session(), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert elapsed < 10.0, "cleanup must fit its own ceiling, not the 30 s of a normal destroy"
    await _wait_until(
        f"a LEAKED line naming {CONTAINER_NAME}",
        lambda: f"LEAKED container {CONTAINER_NAME}" in caplog.text,
    )
    assert not session_dir.exists(), "a cancelled close_session left the session overlay behind"
    assert _journal(tmp_path).count(f"rm -f {CONTAINER_NAME}") == 2, _journal(tmp_path)


@pytest.mark.asyncio
async def test_repeated_cancellation_of_close_session_does_not_hang(tmp_path: Path) -> None:
    """A shutdown drain cancels twice (T7.63): cleanup is bounded, so the second cancel still lands."""
    executor, handle = await _open_close_executor(tmp_path, _engine(tmp_path, wedge_on="rm"))
    session_dir = handle.workspace_dir.parent
    task = asyncio.ensure_future(executor.close_session())
    await _wait_until("the destroy call started", lambda: bool(_cancelled_pids(tmp_path)))

    task.cancel()  # the cancellation of the session itself
    await asyncio.sleep(0.2)
    started = time.monotonic()
    task.cancel()  # what `apps/web/api.py` sends at its drain ceiling, mid-cleanup
    outcome: BaseException | None = None
    try:
        await task
    except asyncio.CancelledError as exc:
        outcome = exc

    assert isinstance(outcome, asyncio.CancelledError), f"the cancellation became {outcome!r}"
    assert time.monotonic() - started < 10.0, "a repeated cancellation must not hang the teardown"
    assert not session_dir.exists()
    await _wait_until(
        "the detached cleanup call finished on its own ceiling and its CLI was reaped",
        lambda: all(_process_state(pid) == "gone" for pid in _cancelled_pids(tmp_path))
        and _journal(tmp_path).count(f"rm -f {CONTAINER_NAME}") >= 2,
    )


# ── bounded reap against a wedged child watcher ───────────────────────────────


class _WedgedChild:
    """An engine CLI that was killed but whose `wait()` never comes back (wedged child watcher)."""

    def __init__(self) -> None:
        self.pid = os.getpid()
        self.returncode: int | None = None
        self.killed = False

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        await asyncio.sleep(2.5)  # longer than the patched reap ceiling, short enough to finish here
        return -9


@pytest.mark.asyncio
async def test_kill_and_reap_is_bounded_when_the_child_watch_wedges(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Giving up on a reap costs the reap only — never the shutdown drain that awaits us."""
    child = _WedgedChild()

    with caplog.at_level(logging.WARNING):
        started = time.monotonic()
        await asyncio.wait_for(ContainerSandboxRuntime._kill_and_reap(child), timeout=10.0)
        elapsed = time.monotonic() - started

    assert child.killed is True, "SIGKILL must be delivered synchronously, before any await"
    assert elapsed < 8.0, f"the reap waited out the wedge instead of bounding it ({elapsed:.1f} s)"
    assert "was killed but not reaped" in caplog.text, caplog.text


# ── T7.95a: confirmation of absence (all verified red on d0e27c1) ─────────────


def _rm_count(tmp_path: Path) -> int:
    return _journal(tmp_path).count(f"rm -f {CONTAINER_NAME}")


async def _open_executor(tmp_path: Path, engine: str) -> SandboxSessionExecutor:
    """A runtime + executor holding a live fake container (create/start complete normally)."""
    rt = _runtime(tmp_path, engine)
    handle = await rt.start(str(SESSION_ID), None, PROFILE)
    assert handle.container_name == CONTAINER_NAME
    ex = SandboxSessionExecutor(rt)
    ex._handle = handle
    return ex


@pytest.mark.asyncio
async def test_in_progress_refusal_confirms_absence_and_names_no_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Manager probe defect 1 at unit level: killing our `rm -f` client does NOT cancel dockerd's removal.

    The first (destroy) `rm -f` is cancelled mid-call; the helper's `rm -f` then gets docker's measured
    refusal «removal … is already in progress» while the container disappears behind our back. d0e27c1
    called this LEAKED on every mid-removal cancellation — with nothing actually leaked (probe: 3/3).
    """
    engine = _engine(tmp_path, wedge_on="rm", wedge_once=True, refuse_in_progress_once=True)
    ex = await _open_executor(tmp_path, engine)

    with caplog.at_level(logging.WARNING):
        outcome, elapsed = await _cancel_after_wedge(ex.close_session(), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), outcome
    assert elapsed < 10.0, f"close_session hung instead of bounding its cleanup ({elapsed:.1f} s)"
    calls = _journal(tmp_path)
    assert _rm_count(tmp_path) == 2, f"expected destroy's interrupted rm and exactly one helper rm: {calls}"
    assert f"LEAKED container {CONTAINER_NAME}" not in caplog.text.replace("closed session", ""), (
        "a removal that dockerd completed on its own is NOT a leak — the old line was the false alarm: "
        + caplog.text
    )
    assert not (tmp_path / "sandbox" / str(SESSION_ID)).exists(), "the overlay dir must go either way"


@pytest.mark.asyncio
async def test_container_that_reappeared_after_a_successful_rm_is_removed_again_without_leak(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Manager probe defect 2 at unit level: `rm -f` answering rc=0 proves NOTHING (measured docker).

    The cancelled create leaves dockerd completing the container after our client died; the helper's
    first `rm -f` returns "success" for a name not materialised yet, and the container appears on the
    confirmation inspect. It must be removed again (visible in the engine's own journal) and confirmed
    absent — with no LEAKED line. d0e27c1 removed once, never looked, stayed silent, leaked 4/24 probes.
    """
    engine = _engine(tmp_path, wedge_on="create", reappear_after_rm=True)
    rt = _runtime(tmp_path, engine)

    with caplog.at_level(logging.WARNING):
        outcome, elapsed = await _cancel_after_wedge(rt.start(str(SESSION_ID), None, PROFILE), tmp_path)

    assert isinstance(outcome, asyncio.CancelledError), outcome
    assert elapsed < 10.0, f"cancelled start must bound its own cleanup ({elapsed:.1f} s)"
    calls = _journal(tmp_path)
    assert _rm_count(tmp_path) == 2, (
        f"the reappeared container must be removed a second time, engine journal: {calls}"
    )
    assert "inspect" in "\n".join(calls), "absence must be confirmed by the engine, not assumed from rc=0"
    assert f"LEAKED container {CONTAINER_NAME}" not in caplog.text, (
        "confirmed cleanup must stay silent: " + caplog.text
    )


@pytest.mark.asyncio
async def test_container_never_removed_gets_exactly_one_present_leak_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A container the engine keeps reporting present is named ONCE, and the line says it is PRESENT."""
    engine = _engine(tmp_path, stubborn_rm=True)  # every rm -f "succeeds"; nothing actually disappears
    rt = _runtime(tmp_path, engine)
    handle = await rt.start(str(SESSION_ID), None, PROFILE)
    assert handle.container_name == CONTAINER_NAME

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        cleaned = await rt.remove_container_best_effort(CONTAINER_NAME, "unit scenario")
    elapsed = time.monotonic() - started

    assert cleaned is False, "an engine that keeps reporting the container present did not confirm absence"
    assert elapsed < 10.0, f"cleanup exceeded its bounded sequence ({elapsed:.1f} s)"
    assert _rm_count(tmp_path) == 2, f"one removal + exactly one re-removal: {_journal(tmp_path)}"
    leaks = [line for line in caplog.text.splitlines() if f"LEAKED container {CONTAINER_NAME}" in line]
    assert len(leaks) == 1, f"exactly one leak line per cleanup attempt: {caplog.text}"
    assert "keeps reporting the container present" in leaks[0], leaks[0]


@pytest.mark.asyncio
async def test_engine_that_never_answers_gives_one_unconfirmed_absence_leak_line(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """LEAKED only at the ceiling's expiry — and it then says the absence stayed UNCONFIRMED."""
    engine = _engine(tmp_path, wedge_on="inspect")  # rm -f answers; every confirmation question hangs
    rt = _runtime(tmp_path, engine)
    handle = await rt.start(str(SESSION_ID), None, PROFILE)
    assert handle.container_name == CONTAINER_NAME

    started = time.monotonic()
    with caplog.at_level(logging.WARNING):
        cleaned = await rt.remove_container_best_effort(CONTAINER_NAME, "unit scenario")
    elapsed = time.monotonic() - started

    assert cleaned is False, "no conclusive engine answer means the container may well be alive"
    assert elapsed < 10.0, f"the hung confirmation must end at its own ceiling ({elapsed:.1f} s)"
    assert _rm_count(tmp_path) == 1, (
        f"a wedged engine gets no blind re-removal: {_journal(tmp_path)}"
    )
    leaks = [line for line in caplog.text.splitlines() if f"LEAKED container {CONTAINER_NAME}" in line]
    assert len(leaks) == 1, f"exactly one leak line per cleanup attempt: {caplog.text}"
    assert "absence UNCONFIRMED" in leaks[0], leaks[0]
    # the wedged confirmation CLIs must be gone too (T7.93 kill-and-reap covers every ceiling call)
    pids = _cancelled_pids(tmp_path)
    assert pids, "the fake engine never recorded its wedged inspect PID"
    await _wait_until(
        "every wedged confirmation CLI is gone (killed and reaped at the cleanup ceiling)",
        lambda: all(_process_state(pid) == "gone" for pid in pids),
    )


@pytest.mark.asyncio
async def test_cancelled_start_leaves_no_session_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """T7.95a defect 3: a cancelled `start` used to leave `<work_root>/<session_id>` forever (30/30 probes)."""
    engine = _engine(tmp_path, wedge_on="create")
    rt = _runtime(tmp_path, engine)

    outcome, elapsed = await _cancel_after_wedge(rt.start(str(SESSION_ID), None, PROFILE), tmp_path)
    assert isinstance(outcome, asyncio.CancelledError), outcome
    assert elapsed < 10.0, f"a cancelled start must return control quickly ({elapsed:.1f} s)"
    assert not (tmp_path / "sandbox" / str(SESSION_ID)).exists(), "a failed start must remove its session dir"
    assert list(rt.work_root.iterdir()) == [], rt.work_root


@pytest.mark.asyncio
async def test_ceiling_expired_start_leaves_no_session_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The ceiling path of the same rule: SandboxError as before (T7.93), directory gone (T7.95a)."""
    engine = _engine(tmp_path, wedge_on="create")
    rt = _runtime(tmp_path, engine)

    with pytest.raises(SandboxError) as raised:
        await rt.start(str(SESSION_ID), None, PROFILE)
    assert "engine command timed out" in str(raised.value), str(raised.value)
    assert f"rm -f {CONTAINER_NAME}" in _journal(tmp_path), "container cleanup stays as T7.95 had it"
    assert not (tmp_path / "sandbox" / str(SESSION_ID)).exists(), "a ceilinged start must remove its session dir"


@pytest.mark.asyncio
async def test_refused_create_leaves_no_session_dir_and_still_touches_no_container(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The engine-refused (`rc != 0`) path keeps removing NO container name (T7.95 invariant) — but its
    session directory is not exempt from cleanup anymore."""
    engine = _engine(tmp_path, fail_on="create")
    rt = _runtime(tmp_path, engine)

    with pytest.raises(SandboxError) as raised:
        await rt.start(str(SESSION_ID), None, PROFILE)
    assert "failed to create sandbox container" in str(raised.value), str(raised.value)
    calls = _journal(tmp_path)
    assert _rm_count(tmp_path) == 0, f"a name the engine never confirmed as ours is not removed blindly: {calls}"
    assert not (tmp_path / "sandbox" / str(SESSION_ID)).exists(), "a refused start must remove its session dir"
