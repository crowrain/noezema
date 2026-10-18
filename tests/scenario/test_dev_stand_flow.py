"""Scenario: the dev-stand flow end to end (T7.59(b), §5.2.1/§5.3.2/§13.1–§13.3).

What a human does on the stand VM — ask a question in the UI, press «wake now», watch the
session and the queue — exercised against the scratch DB with the FakeLLM instead of the real
model and the stub executor (no GPU, no LLM host), which is exactly the shape of the stand.

Also pins the documented "empty queue is not an error" behaviour: admission passes, a session
runs, finds no candidate and terminates FAILED `no_question`, and the wake bookkeeping counts it.

T7.62 — the visibility of a running session in /api/v1/status used to be polled out of a narrow
window (the row is committed at COMMITTING and terminal by the final tx: milliseconds), and under
`-n auto` load a full run missed it (`assert saw_session` went red). The observation is now taken
at a barrier point: the test parks the session between the committed phase 1 and the prepared-attempt
step (event gate around `commit_prepare`, test-side monkeypatch) and reads status there — same
claim, no race. `_wait_idle` remains a hang ceiling only.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator import orchestrator as orchestrator_module
from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.unit_state import publish_unit_state
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

ADMIN_TOKEN = "stand-admin-token"
QUESTION_TEXT = "Сколько будет 6*7?"

TOOL_PYTHON = {
    "public_rationale": "Проверить вычисление",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE = {"public_rationale": "Вопрос отвечен", "decision": {"kind": "complete", "reason": "goal_reached"}}
CURATOR_OK = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_stand(scratch_url: str, fake: FakeLLM, tmp_path: Path):
    """The stand shape: web + orchestrator + host protocol files under one data root."""
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "sandbox-work"),
    )
    host_lib = tmp_path / "host"
    unit_state = host_lib / "unit-state.json"
    host_lib.mkdir(parents=True, exist_ok=True)
    # The stand publishes unit-state like noezema-dev-unit-state.timer does; without it the
    # Command API and POST questions would be 423 (T3.24).
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    app = create_app(
        engine=engine,
        factory=factory,
        orchestrator=orchestrator,
        host_adapter=HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state),
        admin_token=ADMIN_TOKEN,
    )
    return app, engine, gateway


async def _wait_idle(client: httpx.AsyncClient) -> dict:
    """Poll /api/v1/status until the node is idle again.

    T7.62: this polling is only a hang ceiling. State-dependent observations are taken at
    barrier-pinned points (see test 1), never by trying to catch a millisecond commit window
    with a lucky snapshot.
    """
    data: dict = {}
    for _ in range(120):
        data = (await client.get("/api/v1/status")).json()
        if data["node_state"] == "idle" and data["session"] is None:
            return data
        await asyncio.sleep(0.1)
    pytest.fail(f"сессия не завершилась: {data}")


async def _hold_commit_window(
    monkeypatch: pytest.MonkeyPatch, prepared_visible: asyncio.Event, hold_prepare: asyncio.Event
) -> None:
    """Test-side gate on the seam between committed phase 1 and the prepared-attempt step.

    When `commit_prepare` is about to be called, the session row has ALREADY been committed at
    COMMITTING (visible to every other connection), and the fenced final transaction has not begun
    yet. Parking here keeps the visibility window open until the test has read /api/v1/status — the
    old version raced that window with 0.1 s polls and lost under `-n auto` load. The product's own
    order is unchanged; monkeypatch is restored at test end.
    """
    orig_prepare = orchestrator_module.commit_prepare

    async def _gated_prepare(*args, **kwargs):
        prepared_visible.set()
        await hold_prepare.wait()
        return await orig_prepare(*args, **kwargs)

    monkeypatch.setattr(orchestrator_module, "commit_prepare", _gated_prepare)


@pytest.mark.asyncio
async def test_asked_question_runs_a_full_session_and_leaves_the_queue(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_url, _ = migrated_db
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    app, engine, gateway = await _make_stand(scratch_url, fake_llm, tmp_path)
    prepared_visible = asyncio.Event()
    hold_prepare = asyncio.Event()
    await _hold_commit_window(monkeypatch, prepared_visible, hold_prepare)
    try:
        async with _client(app) as client:
            r = await client.post(
                "/api/v1/questions",
                json={"text": QUESTION_TEXT, "priority": 9},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 201, r.text
            body = r.json()
            assert body["origin"] == "message" and body["state"] == "candidate"
            assert body["position"] == 1 and body["replayed"] is False

            rows = (await client.get("/api/v1/questions")).json()["questions"]
            assert [row["id"] for row in rows] == [body["id"]]
            assert rows[0]["priority"] == 9 and rows[0]["position"] == 1

            r = await client.post(
                "/api/v1/commands",
                json={"type": "wake_now", "idempotency_key": "dev-stand-wake-1"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 202 and r.json()["state"] == "completed", r.text

            # barrier point: the row is committed at COMMITTING, the final tx has not started.
            # Read status HERE — "the session must be visible in /api/v1/status" then holds by
            # construction instead of by catching a millisecond window with polls (T7.62).
            await asyncio.wait_for(prepared_visible.wait(), timeout=120)
            mid = (await client.get("/api/v1/status")).json()
            assert mid["node_state"] == "session_running", mid
            assert mid["session"] is not None and mid["session"]["state"] == "committing", (
                f"сессия должна была быть видна в /api/v1/status: {mid}"
            )
            assert mid["node_state_stale_marker"] is False, mid
            hold_prepare.set()

            status = await _wait_idle(client)
            assert status["counts"]["sessions"] == 1

            events = (await client.get("/api/v1/timeline")).json()["events"]
            types = [e["type"] for e in events]
            assert "session_started" in types and "session_committed" in types

            # the operator question left the queue: no candidates, session annotation attached
            rows = (await client.get("/api/v1/questions")).json()["questions"]
            assert len(rows) == 1
            row = rows[0]
            assert row["state"] == "verified", row
            assert row["position"] is None and row["origin"] == "message"
            assert row["session"] is not None and row["session"]["id"]

            async with engine.connect() as conn:
                session_row = (
                    await conn.execute(
                        text("SELECT state, termination_reason FROM sessions ORDER BY created_at DESC LIMIT 1")
                    )
                ).first()
            assert session_row is not None and session_row.termination_reason != "no_question"
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_wake_with_an_empty_queue_is_a_recorded_no_question_not_an_error(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """§5.2.1 on a stand with nothing to ask about: wake happens, no candidate is found, the
    session ends FAILED no_question and consecutive_failures grows — that is not an error."""
    scratch_url, _ = migrated_db
    app, engine, gateway = await _make_stand(scratch_url, fake_llm, tmp_path)
    try:
        async with _client(app) as client:
            assert (await client.get("/api/v1/questions")).json()["count"] == 0

            r = await client.post(
                "/api/v1/commands",
                json={"type": "wake_now", "idempotency_key": "dev-stand-empty"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 202 and r.json()["state"] == "completed", r.text

            status = await _wait_idle(client)
            assert status["node_state"] == "idle"
            assert status["counts"]["sessions"] == 1

            async with engine.connect() as conn:
                reason = (
                    await conn.execute(
                        text("SELECT termination_reason FROM sessions ORDER BY created_at DESC LIMIT 1")
                    )
                ).scalar_one()
                failures = (
                    await conn.execute(text("SELECT consecutive_failures FROM wake_scheduler_state"))
                ).scalar_one()
            assert reason == "no_question"
            assert failures >= 1
            # and the intake still works after such a tick: the queue is not poisoned
            r = await client.post(
                "/api/v1/questions",
                json={"text": "А теперь вопрос"},
                headers={"X-Admin-Token": ADMIN_TOKEN},
            )
            assert r.status_code == 201 and r.json()["position"] == 1
    finally:
        await gateway.close()
        await engine.dispose()
