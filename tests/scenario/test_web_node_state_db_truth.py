"""Scenario: `system_constants.node_state` in the DB is the truth for commands and for status.

T7.59(в) — the defect found on the real dev stand: the web read the node state ONCE in its lifespan
and kept it in memory. If the web started while an external `hostctl wake-tick` held a session, the
copy stayed `session_running` forever: «wake now» answered "a session is already running" and
/api/v1/status reported a node state the DB no longer had.

These tests write the node state through a SECOND connection — i.e. exactly as another process
(a tick unit) does — while the web app is already running.

T7.62 — observation is anchored on barriers, not on time polling. The old tail of
`test_leftover_session_running_marker_does_not_wedge_the_node` polled /api/v1/status every 50 ms and
demanded `node_state_stale_marker is False` from EVERY snapshot that said `session_running`. That
statement is false even when the product behaves correctly: after `node.session_task` has ended, but
before the separate outcome task (the done-callback schedules `_record_session_outcome`) commits
`idle`, a snapshot legitimately sees `session_running` + no nonterminal session row + no live task in
this process = `node_state_stale_marker is True`. The window is milliseconds wide; a loaded runner
hits it, a quiet local run slips through it. A sample taken WHILE this web owns the session can
never be stale (`_owns_live_session`), so phase-1 samples are produced by holding the session at a
tool step (asyncio.Event gate in `StubToolExecutor.execute` — same event loop as the app); the tail
window is asserted separately (test 4) with an explicit stale=True expectation. Waits are event
awaits; absolute times appear only as ceilings against a hang, never as correctness conditions.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.scheduler import WakeScheduler
from apps.web.api import create_app, effective_node_state
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON = {
    "public_rationale": "Проверить вычисление",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _await_event(event: asyncio.Event, what: str, ceiling: float = 120.0) -> None:
    """Await a synchronization event. The ceiling only guards against a hang — it is never an
    expected duration (T7.62: no wall-clock as a correctness condition)."""
    await asyncio.wait_for(event.wait(), timeout=ceiling)


async def _make_app(
    scratch_url: str, fake: FakeLLM, workspace: Path, executor: StubToolExecutor | None = None
):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=executor if executor is not None else StubToolExecutor(workspace),
    )
    app = create_app(engine=engine, factory=factory, orchestrator=orchestrator)
    return app, engine, factory, gateway


async def _set_node_state(url: str, state: str) -> None:
    """Another process writes the node state (what the wake tick unit does)."""
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO system_constants (key, value) VALUES ('node_state', :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = :v"
                ),
                {"v": state},
            )
    finally:
        await engine.dispose()


async def _insert_external_session(factory, *, state: str) -> uuid.UUID:
    """A session row of an external tick: nonterminal while it runs, terminal when it ends."""
    async with factory() as db, transaction(db):
        bootstrap_id = (
            await db.execute(
                text("SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'")
            )
        ).scalar_one()
        session_id = uuid.uuid4()
        await db.execute(
            text("INSERT INTO sessions (id, state, config_snapshot_id) VALUES (:id, :s, :c)"),
            {"id": session_id, "s": state, "c": str(bootstrap_id)},
        )
    return session_id


async def _finish_external_session(factory, session_id: uuid.UUID) -> None:
    async with factory() as db, transaction(db):
        await db.execute(
            text("UPDATE sessions SET state = 'succeeded', finished_at = now() WHERE id = :id"),
            {"id": str(session_id)},
        )


async def _seed_question(factory) -> None:
    async with factory() as db, transaction(db):
        await QuestionRepository.create(
            db, ORMQuestion(text="Сколько будет 6*7?", origin=QuestionOrigin.SEEDED.value)
        )


class _GateExecutor(StubToolExecutor):
    """T7.62 barrier: holds the session INSIDE phase 1 at its tool step until the test releases it.

    `run_session` executes tools through this same executor (the single `self.executor.execute(...)`
    call site in apps/orchestrator/orchestrator.py) inside the app's own event loop — so an
    `asyncio.Event.wait()` here parks the session mid-flight without any time assumption. While the
    gate is held, this web provably owns the session (`node.session_task` is live), which is exactly
    the precondition the status assertions need; the old test tried to reach it by polling instead.
    """

    def __init__(self, workspace_dir: Path) -> None:
        super().__init__(workspace_dir)
        self.reached_gate = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, tool, arguments, *, db=None):
        if tool == "python.execute":
            self.reached_gate.set()
            await self.release.wait()
        return await super().execute(tool, arguments, db=db)


def _wrap_run_session(orchestrator: Orchestrator, finished: asyncio.Event) -> None:
    """Instance-level wrapper so the test can await session completion deterministically.

    `create_app` seeds `app.state.orchestrator`; the command handler reads it AT COMMAND TIME
    (T7.59(в)) and `_run_locked_session` calls exactly this object's `run_session`, so patching the
    instance BEFORE wake is race-free and touches no product module.
    """
    orig_run = orchestrator.run_session

    async def _run_and_signal(*args, **kwargs):
        try:
            return await orig_run(*args, **kwargs)
        finally:
            finished.set()

    orchestrator.run_session = _run_and_signal


def _patch_record_outcome(
    monkeypatch: pytest.MonkeyPatch,
    done: asyncio.Event,
    *,
    entered: asyncio.Event | None = None,
    hold: asyncio.Event | None = None,
) -> None:
    """Wrap `WakeScheduler.record_session_result` — the DB write that `_record_session_outcome`
    performs AFTER the session task has already ended (api.py schedules it from the task's
    done-callback).

    `done` fires once the real call COMMITTED the terminal node state: a deterministic end of the
    tail window. With `hold`, the wrapper blocks before that write until the test releases it — the
    tail window is then pinned open for observation (test 4); `entered` announces that the outcome
    task has reached the blocked write. Test-side instrumentation only; product code and its order
    are untouched.
    """
    orig = WakeScheduler.record_session_result

    async def _wrapped(self, *, final_state, now):
        if entered is not None:
            entered.set()
        if hold is not None:
            await hold.wait()
        out = await orig(self, final_state=final_state, now=now)
        done.set()
        return out

    monkeypatch.setattr(WakeScheduler, "record_session_result", _wrapped)


@pytest.mark.asyncio
async def test_external_session_blocks_wake_only_while_it_runs(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """(а)+(б): the web starts while an external tick holds a session.

    While that session is nonterminal «wake now» is refused (the invariant), and after it terminates
    and the DB says `idle` the SAME web process accepts «wake now» — no restart, no stuck memory.
    """
    scratch_url, _fixture_engine = migrated_db
    app, engine, factory, gateway = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    try:
        await _set_node_state(scratch_url, "session_running")
        external = await _insert_external_session(factory, state="exploring")

        # The web starts now — this is the moment that used to freeze its copy of the state.
        async with app.router.lifespan_context(app), _client(app) as client:
            status = (await client.get("/api/v1/status")).json()
            assert status["node_state"] == "session_running", status

            r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-ext-1"})
            assert r.status_code == 202
            assert r.json()["state"] == "rejected", r.text
            assert r.json()["result"]["reason"] == "a session is already running"

            # the external tick finishes: its session goes terminal, the marker goes back to idle
            await _finish_external_session(factory, external)
            await _set_node_state(scratch_url, "idle")
            await _seed_question(factory)

            r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-ext-2"})
            assert r.status_code == 202
            body = r.json()
            assert body["state"] == "completed", body
            assert body["result"]["node_state"] == "session_running"

            data: dict = {}
            for _ in range(150):
                data = (await client.get("/api/v1/status")).json()
                if data["node_state"] == "idle" and data["session"] is None:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail(f"сессия не завершилась: {data}")
            assert data["counts"]["sessions"] == 2
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_reports_the_db_state_written_after_startup(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """(в): /api/v1/status shows the DB value, including one changed by another process."""
    scratch_url, _fixture_engine = migrated_db
    app, engine, _factory, gateway = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    try:
        async with app.router.lifespan_context(app), _client(app) as client:
            assert (await client.get("/api/v1/status")).json()["node_state"] == "idle"

            await _set_node_state(scratch_url, "paused")
            status = (await client.get("/api/v1/status")).json()
            assert status["node_state"] == "paused", status
            # and a paused node refuses wake_now on the DB state, not on a remembered one
            r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-paused"})
            assert r.json()["state"] == "rejected"
            assert r.json()["result"]["reason"] == "node is paused"

            await _set_node_state(scratch_url, "idle")
            assert (await client.get("/api/v1/status")).json()["node_state"] == "idle"
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_leftover_session_running_marker_does_not_wedge_the_node(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `session_running` marker of a tick that has already ended (or died) is named in status and
    does not refuse wake_now forever; while this web runs its own session the marker is never stale.

    T7.62 rewrite of the observation, because CI run 37290125308 caught this test's tail failing on
    `all(s["node_state_stale_marker"] is False for s in while_running)`: the old loop polled status
    every 50 ms until idle and treated every `session_running` sample as "taken while this web owns
    the session". It is not — after the session task ended, before `_record_session_outcome` commits
    `idle`, a further snapshot legitimately shows stale_marker True (the tail window; pinned and
    asserted on its own in test 4 below). Barriers now produce and hold every observed state instead
    of racing it: no sample is taken in the tail window here at all.
    """
    scratch_url, _fixture_engine = migrated_db
    gate = _GateExecutor(tmp_path / "ws")
    app, engine, factory, gateway = await _make_app(scratch_url, fake_llm, tmp_path / "ws", executor=gate)
    session_finished = asyncio.Event()
    marker_reset = asyncio.Event()
    _wrap_run_session(app.state.orchestrator, session_finished)
    _patch_record_outcome(monkeypatch, marker_reset)
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    try:
        await _set_node_state(scratch_url, "session_running")  # marker only, no session behind it
        async with app.router.lifespan_context(app), _client(app) as client:
            status = (await client.get("/api/v1/status")).json()
            # the view stays honest about the DB value and names the mismatch explicitly
            assert status["node_state"] == "session_running", status
            assert status["node_state_stale_marker"] is True, status

            await _seed_question(factory)
            r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-stale"})
            # the leftover marker did not wedge the node: the wake was granted and started a session
            assert r.json()["state"] == "completed", r.text

            # barrier: park the session inside phase 1 at its tool step. From here until `release`
            # this web provably owns the session — no polling, no dependence on window widths.
            await _await_event(gate.reached_gate, "сессия не дошла до барьера в фазе 1")

            while_running: list[dict] = []
            for _ in range(3):
                data = (await client.get("/api/v1/status")).json()
                if data["node_state"] == "session_running":
                    # снимок, сделанный, пока этот веб владеет сессией: маркер обязан описывать её
                    while_running.append(data)

            # the wake put the marker back to a session_running that DOES describe a running session:
            # every such sample is fresh. The old claim covered "EVERY polled snapshot" and so also
            # ate tail-window samples where stale=True is correct — none is taken here (test 4 pins
            # that window separately).
            assert while_running, status
            assert all(s["node_state_stale_marker"] is False for s in while_running), while_running
            # Visibility mechanism as a comment (T7.61(б), see test_node_session_exclusion.py): phase 1
            # keeps the session row uncommitted until COMMITTING, so during the hold another
            # connection sees `session: None` while this process's live task explains the marker —
            # which is exactly why stale_marker must be False here.

            # and wake_now during the held session answers exactly like it would for any live session
            r2 = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-stale-busy"})
            assert r2.json()["state"] == "rejected", r2.text
            assert r2.json()["result"]["reason"] == "a session is already running"

            # release the barrier; completion is awaited via events, not raced against status
            gate.release.set()
            await _await_event(session_finished, "сессия не завершилась после снятия барьера")
            await _await_event(marker_reset, "учёт исхода сессии не записал итоговое состояние")

            data = (await client.get("/api/v1/status")).json()
            assert data["node_state"] == "idle", data
            assert data["session"] is None, data
            assert data["counts"]["sessions"] == 1, data
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_tail_window_after_session_end_is_stale_but_harmless(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The exact sample class CI 37290125308 caught in test 3 — pinned where it belongs.

    The session task has ended (its terminal row is committed), and the separate outcome task has
    not yet written `idle`: status then legitimately shows `session_running` with
    `node_state_stale_marker is True`. This is a fact of the process order in api.py (the done-
    callback schedules `_record_session_outcome` as its own task), not a defect — commands are
    decided on `effective_node_state`, which resolves precisely this combination to `idle`, so the
    node is not wedged. Here the window is pinned open with an event gate around that DB write, so
    the snapshot cannot slip past it; outside such a held window this class must not be demanded of
    every snapshot (test 3 takes no tail-window samples at all).
    """
    scratch_url, _fixture_engine = migrated_db
    app, engine, factory, gateway = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    session_finished = asyncio.Event()
    tail_open = asyncio.Event()
    hold_tail = asyncio.Event()
    marker_reset = asyncio.Event()
    _wrap_run_session(app.state.orchestrator, session_finished)
    _patch_record_outcome(monkeypatch, marker_reset, entered=tail_open, hold=hold_tail)
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    try:
        await _seed_question(factory)
        async with app.router.lifespan_context(app), _client(app) as client:
            r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "k-tail"})
            assert r.json()["state"] == "completed", r.text

            # the session ends; its outcome write is deliberately parked before touching the DB, so
            # the tail window is held open while we observe it
            await _await_event(session_finished, "сессия не завершилась")
            await _await_event(tail_open, "задача учёта исхода не дошла до блокированной записи")

            data = (await client.get("/api/v1/status")).json()
            assert data["node_state"] == "session_running", data  # the marker of THIS process's session
            assert data["node_state_stale_marker"] is True, data  # task done + no nonterminal row => stale
            assert data["session"] is None, data  # the terminal row is committed: nothing runs anymore
            assert data["counts"]["sessions"] == 1, data

            # ...and this exact combination resolves to `idle` for commands — the node is not wedged.
            resolved = effective_node_state(
                "session_running", web_owns_session=False, db_has_nonterminal_session=False
            )
            assert resolved == "idle"

            hold_tail.set()
            await _await_event(marker_reset, "хвостовое окно не закрылось записью idle")

            data = (await client.get("/api/v1/status")).json()
            assert data["node_state"] == "idle", data
            assert data["session"] is None, data
            assert data["node_state_stale_marker"] is False, data
            assert data["counts"]["sessions"] == 1, data
    finally:
        await gateway.close()
        await engine.dispose()
