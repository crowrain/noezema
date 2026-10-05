"""Scenario: the node session lane itself (T7.61(а), §5.2.1) — acquire, release, crash semantics.

`apps/orchestrator/node_guard.py` is what closes the decide→session-row window that let a scheduled tick
start a second session next to «wake now» on the dev stand (the race itself is reproduced in
`test_node_session_exclusion.py`). What this file pins down is the part the reproducer cannot see:

- the lock really excludes across connections/engines, and only for ONE node owner;
- it is released after a normal finish, after an exception inside the session and after a cancelled task;
- it survives the worst case — the holder's backend is terminated (a killed systemd unit, a crash): the
  lane becomes free with no cleanup, because PostgreSQL drops session-level advisory locks with the
  connection that held them.

Everything here runs against one scratch DB through two or more separate engines: that IS the stand's
process boundary for this purpose (`wake-tick`, `noezema-dev-web.service` and a manual run are different
processes sharing one PostgreSQL). No wall-clock assertions.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.main import _run
from apps.orchestrator.node_guard import NodeSessionGuard, node_session_guard
from apps.orchestrator.orchestrator import Orchestrator, SessionOutcome
from apps.orchestrator.scheduler import REASON_SESSION_IN_PROGRESS, node_session_lock_name
from apps.web.api import create_app
from hostctl.unit_state import publish_unit_state
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import REPO_ROOT

pytestmark = [pytest.mark.scenario]

OWNER = "local-node"


@asynccontextmanager
async def _own_engine(url: str) -> AsyncIterator[AsyncEngine]:
    """A separate engine = a separate pool of connections (the stand's other process)."""
    engine = create_async_engine(url)
    try:
        yield engine
    finally:
        await engine.dispose()


async def _session_count(url: str) -> int:
    async with _own_engine(url) as engine, engine.connect() as conn:
        return int((await conn.execute(text("SELECT count(*) FROM sessions"))).scalar_one())


async def _lock_holders(url: str, name: str) -> list[int]:
    """Backend PIDs currently holding this advisory lock — read from another connection."""
    async with _own_engine(url) as engine, engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT pid FROM pg_locks "
                    "WHERE locktype = 'advisory' AND objid = hashtext(:n) "
                    "AND pid <> pg_backend_pid()"
                ),
                {"n": name},
            )
        ).all()
    return [int(r[0]) for r in rows]


async def _free_lane(url: str, owner: str = OWNER, attempts: int = 40) -> bool:
    """True once a fresh holder can take the lane (polling a state change, not a duration)."""
    for _ in range(attempts):
        guard = NodeSessionGuard(url, owner)
        if await guard.acquire():
            await guard.release()
            return True
        await asyncio.sleep(0.05)
    return False


# ── (г) mutual exclusion across engines, and (е) per-node lanes ────────────────────


@pytest.mark.asyncio
async def test_lane_is_exclusive_across_engines_and_reusable_after_release(migrated_db) -> None:
    scratch_url, _fixture_engine = migrated_db

    holder = NodeSessionGuard(scratch_url, OWNER)
    assert await holder.acquire() is True
    assert holder.held is True

    rival = NodeSessionGuard(scratch_url, OWNER)
    assert await rival.acquire() is False, "другое соединение того же узла не должно получать лейн"
    assert await _lock_holders(scratch_url, node_session_lock_name(OWNER)) != []

    # re-acquiring one's own lane is a no-op, not a deadlock and not a second lock
    assert await holder.acquire() is True

    await holder.release()
    await holder.release()  # idempotent: the done-callback path may call it a second time
    assert holder.held is False

    assert await rival.acquire() is True, "после освобождения лейн обязан быть доступен"
    await rival.release()


@pytest.mark.asyncio
async def test_different_node_owners_do_not_block_each_other(migrated_db) -> None:
    """The key is per node owner: two nodes on one DB (the eval/smoke shape) must not fight."""
    scratch_url, _fixture_engine = migrated_db

    a = NodeSessionGuard(scratch_url, "node-a")
    b = NodeSessionGuard(scratch_url, "node-b")
    assert await a.acquire() is True
    assert await b.acquire() is True, "разные node_owner должны брать разные замки"
    assert node_session_lock_name("node-a") != node_session_lock_name("node-b")

    blocked = NodeSessionGuard(scratch_url, "node-a")
    assert await blocked.acquire() is False

    await a.release()
    await b.release()


# ── (в) the holder's backend dies ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_lane_is_free_after_the_holders_backend_is_terminated(migrated_db) -> None:
    """A killed unit must not wedge the node — that is why a session-level lock was chosen.

    `pg_terminate_backend` is the sharpest available stand-in for `systemctl kill`, an OOM or a crash:
    PostgreSQL drops every session-level advisory lock of a backend that went away, so the next wake gets
    the lane without any reset, reconciler rule or manual cleanup.
    """
    scratch_url, _fixture_engine = migrated_db

    holder = NodeSessionGuard(scratch_url, OWNER)
    assert await holder.acquire() is True
    pids = await _lock_holders(scratch_url, node_session_lock_name(OWNER))
    assert len(pids) == 1, f"замок должен держать ровно одно соединение: {pids}"

    async with _own_engine(scratch_url) as killer, killer.connect() as conn:
        terminated = (
            await conn.execute(text("SELECT pg_terminate_backend(:pid)"), {"pid": pids[0]})
        ).scalar_one()
    assert bool(terminated) is True

    # the guard's own release must not raise on a dead backend, and must not hang
    await holder.release()

    assert await _free_lane(scratch_url), "following wake must get the lane after the holder died"


# ── (б) release on every exit path of a session task ───────────────────────────────


@pytest.mark.asyncio
async def test_context_manager_releases_when_the_session_raises(migrated_db) -> None:
    scratch_url, _fixture_engine = migrated_db

    with pytest.raises(RuntimeError, match="session blew up"):
        async with node_session_guard(scratch_url, OWNER):
            raise RuntimeError("session blew up")

    assert await _free_lane(scratch_url), "исключение внутри сессии обязано вернуть лейн"


@pytest.mark.asyncio
async def test_context_manager_releases_when_the_session_task_is_cancelled(migrated_db) -> None:
    scratch_url, _fixture_engine = migrated_db
    started = asyncio.Event()

    async def _cancelled_session() -> None:
        async with node_session_guard(scratch_url, OWNER):
            started.set()
            await asyncio.sleep(600)  # the session task is cancelled while it runs

    task = asyncio.create_task(_cancelled_session())
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _free_lane(scratch_url), "отмена задачи сессии обязана вернуть лейн"


# ── (д) web «wake now» while another process holds the lane + release on cancel ────


class _SlowOrchestrator:
    """Stand-in for `Orchestrator.run_session`: a session that runs until it is cancelled."""

    def __init__(self) -> None:
        self.entered = asyncio.Event()
        self.left = False

    async def run_session(self) -> SessionOutcome:
        self.entered.set()
        try:
            await asyncio.sleep(600)
        finally:
            self.left = True
        raise AssertionError("unreachable")


def _web_app(url: str, workspace: Path, orchestrator: object):
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(LLMGatewayConfig(base_url="http://127.0.0.1:1/v1", model="fake-thinker"))
    real = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
    )
    app = create_app(engine=engine, factory=factory, orchestrator=orchestrator or real)
    return app, engine, gateway


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_web_wake_now_rejects_while_another_process_holds_the_lane(migrated_db, tmp_path) -> None:
    """The stand case from the operator's side: a tick is mid-session, the operator presses «wake now»."""
    scratch_url, _fixture_engine = migrated_db
    app, engine, gateway = _web_app(scratch_url, tmp_path, None)

    holder = NodeSessionGuard(scratch_url, OWNER)
    assert await holder.acquire() is True

    try:
        async with app.router.lifespan_context(app):
            async with _client(app) as client:
                response = await client.post(
                    "/api/v1/commands",
                    json={"type": "wake_now", "idempotency_key": f"busy-{uuid4()}"},
                )
                assert response.status_code == 202, response.text
                body = response.json()
                assert body["state"] == "rejected", body
                assert body["result"]["reason"] == "a session is already running", body
                assert body["result"]["wake_reason"] == REASON_SESSION_IN_PROGRESS, body
            assert await _session_count(scratch_url) == 0, "отказанное wake не создаёт сессию"
    finally:
        await gateway.close()
        await holder.release()
        await engine.dispose()


@pytest.mark.asyncio
async def test_web_session_returns_the_lane_when_its_task_is_cancelled(migrated_db, tmp_path) -> None:
    """Shutdown/abort path: the lane must not stay held by a web process that stopped its session."""
    scratch_url, _fixture_engine = migrated_db
    slow = _SlowOrchestrator()
    app, engine, gateway = _web_app(scratch_url, tmp_path, slow)

    try:
        async with app.router.lifespan_context(app):
            async with _client(app) as client:
                response = await client.post(
                    "/api/v1/commands",
                    json={"type": "wake_now", "idempotency_key": f"cancel-{uuid4()}"},
                )
                assert response.json()["state"] == "completed", response.json()

            await slow.entered.wait()
            task = app.state.node.session_task
            assert task is not None
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            assert slow.left is True

            # the wrapper's finally is what releases it; a second wake from another process now works
            assert await _free_lane(scratch_url), "отменённая веб-сессия оставила лейн занятым"
    finally:
        await gateway.close()
        await engine.dispose()


# ── wake-tick CLI contract and the manual entry point ──────────────────────────────


def _wake_tick_process(url: str, data_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update(
        {
            "NOEZEMA_DATABASE_URL": url,
            "NOEZEMA_DATA_ROOT": str(data_root),
            "NOEZEMA_TOOL_EXECUTOR": "stub",
            "NOEZEMA_LLM_BASE_URL": "http://127.0.0.1:1/v1",  # never reached: the tick skips first
            "NOEZEMA_LLM_MODEL": "fake-thinker",
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "hostctl.cli", "wake-tick"],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


@pytest.mark.asyncio
async def test_wake_tick_exits_zero_with_the_exact_skip_reason(migrated_db, tmp_path) -> None:
    """§5.2.1: a skipped wake is not an error, and it records its exact reason."""
    scratch_url, _fixture_engine = migrated_db
    data_root = tmp_path / "data"
    host_lib = data_root / "host"
    host_lib.mkdir(parents=True)
    unit_state = host_lib / "unit-state.json"

    holder = NodeSessionGuard(scratch_url, OWNER)
    assert await holder.acquire() is True
    try:
        # the stand's host protocol must be satisfied, otherwise the tick fails closed on units (§5.2.1)
        publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
        os.environ["NOEZEMA_HOST_LIB"] = str(host_lib)
        os.environ["NOEZEMA_UNIT_STATE"] = str(unit_state)
        tick = _wake_tick_process(scratch_url, data_root)
    finally:
        await holder.release()

    assert tick.returncode == 0, f"skip не ошибка: code={tick.returncode} stderr={tick.stderr!r}"
    assert "wake-tick: skip (session_in_progress)" in tick.stdout, tick.stdout
    assert await _session_count(scratch_url) == 0

    async with _own_engine(scratch_url) as engine, engine.connect() as conn:
        rows = (
            await conn.execute(
                text(
                    "SELECT payload->>'reason', payload->>'source' FROM audit_events "
                    "WHERE type = 'wake_skipped' ORDER BY sequence"
                )
            )
        ).all()
    assert ("session_in_progress", "scheduled") in [tuple(r) for r in rows], rows


@pytest.mark.asyncio
async def test_manual_entry_does_not_start_a_second_session(migrated_db, tmp_path, monkeypatch) -> None:
    """`python -m apps.orchestrator` is a host entry point too (§5.2.1): busy lane → skip, exit 0."""
    scratch_url, _fixture_engine = migrated_db
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    monkeypatch.setenv("NOEZEMA_LLM_MODEL", "fake-thinker")

    holder = NodeSessionGuard(scratch_url, OWNER)
    assert await holder.acquire() is True
    try:
        code = await _run()
    finally:
        await holder.release()

    assert code == 0, f"занятый лейн — skip, не провал: {code}"
    assert await _session_count(scratch_url) == 0
