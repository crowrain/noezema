"""Scenario: `system_constants.node_state` in the DB is the truth for commands and for status.

T7.59(в) — the defect found on the real dev stand: the web read the node state ONCE in its lifespan
and kept it in memory. If the web started while an external `hostctl wake-tick` held a session, the
copy stayed `session_running` forever: «wake now» answered "a session is already running" and
/api/v1/status reported a node state the DB no longer had.

These tests write the node state through a SECOND connection — i.e. exactly as another process
(a tick unit) does — while the web app is already running.
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
from apps.web.api import create_app
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


async def _make_app(scratch_url: str, fake: FakeLLM, workspace: Path):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
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
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """A `session_running` marker of a tick that has already ended (or died) is named in status and
    does not refuse wake_now forever; while a session row is nonterminal it still does."""
    scratch_url, _fixture_engine = migrated_db
    app, engine, factory, gateway = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
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
            assert r.json()["state"] == "completed", r.text

            data: dict = {}
            during_run: dict | None = None
            for _ in range(150):
                data = (await client.get("/api/v1/status")).json()
                if data["node_state"] == "session_running" and data["session"] is not None:
                    during_run = data
                if data["node_state"] == "idle" and data["session"] is None:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail(f"сессия не завершилась: {data}")
            assert data["counts"]["sessions"] == 1
            # the wake put the marker back to a session_running that DOES describe a session
            assert during_run is not None and during_run["node_state_stale_marker"] is False, during_run
    finally:
        await gateway.close()
        await engine.dispose()
