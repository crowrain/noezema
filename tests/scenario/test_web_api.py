"""Scenario: minimal Query/Command API (T1.17).

Runs the FastAPI app in-process against the scratch DB + fake LLM and
drives a full session through the operator command API.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.web.api import create_app
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление",
    "decision": {
        "kind": "tool",
        "tool": "python.execute",
        "arguments": {"code": "print(6*7)"},
    },
}
COMPLETE: JsonDict = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [
        {
            "statement": "6*7 равно 42",
            "claim_type": "computed_result",
            "scope": {"expr": "6*7"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


async def _seed_question(factory) -> str:
    async with factory() as db, transaction(db):
        q = await QuestionRepository.create(
            db, ORMQuestion(text="Сколько будет 6*7?", origin=QuestionOrigin.SEEDED.value)
        )
        return str(q.id)


async def _make_app(
    scratch_url: str,
    fake: FakeLLM,
    workspace: Path,
    with_orchestrator: bool = False,
):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orchestrator = None
    gateway = None
    if with_orchestrator:
        gateway = LLMMiddleware(
            LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
        )
        orchestrator = Orchestrator(
            session_factory=factory,
            gateway=gateway,
            profile=ModelProfile(model_alias="fake-thinker"),
            executor=StubToolExecutor(workspace),
        )
    app = create_app(orchestrator=orchestrator, engine=engine, factory=factory)
    return app, engine, factory, gateway


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@pytest.mark.asyncio
async def test_status_message_timeline(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    app, engine, _factory, _gw = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    async with _client(app) as client:
        r = await client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["node_state"] == "idle"
        assert data["session"] is None
        assert data["config"]["snapshot_id"]
        assert data["counts"]["sessions"] == 0

        r = await client.post("/api/v1/messages", json={"body": "привет"})
        assert r.status_code == 201
        assert r.json()["id"]
        assert r.json()["state"] == "created"

        r = await client.get("/api/v1/status")
        assert r.json()["counts"]["messages"] == 1

        # no session yet -> empty timeline
        r = await client.get("/api/v1/timeline")
        assert r.status_code == 200
        assert r.json()["events"] == []
    await engine.dispose()


@pytest.mark.asyncio
async def test_pause_resume_commands(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    app, engine, _factory, _gw = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k1"},
        )
        assert r.status_code == 202
        assert r.json()["state"] == "completed"
        assert r.json()["result"]["node_state"] == "paused"

        # replay the same idempotency key
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k1"},
        )
        assert r.json()["replayed"] is True

        r = await client.get("/api/v1/status")
        assert r.json()["node_state"] == "paused"

        r = await client.post(
            "/api/v1/commands",
            json={"type": "resume", "idempotency_key": "k2"},
        )
        assert r.json()["state"] == "completed"
        r = await client.get("/api/v1/status")
        assert r.json()["node_state"] == "idle"
    await engine.dispose()


@pytest.mark.asyncio
async def test_command_validation(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    app, engine, _factory, _gw = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    async with _client(app) as client:
        # unknown command type is rejected by the closed enum
        r = await client.post(
            "/api/v1/commands",
            json={"type": "reboot", "idempotency_key": "x1"},
        )
        assert r.status_code == 422

        # missing idempotency key
        r = await client.post("/api/v1/commands", json={"type": "pause"})
        assert r.status_code == 422

        # set_budget is rejected in M1
        r = await client.post(
            "/api/v1/commands",
            json={"type": "set_budget", "idempotency_key": "x2"},
        )
        assert r.status_code == 202
        assert r.json()["state"] == "rejected"
    await engine.dispose()


@pytest.mark.asyncio
async def test_stop_without_session_rejected(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    app, engine, _factory, _gw = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/commands",
            json={"type": "stop_gracefully", "idempotency_key": "s1"},
        )
        assert r.json()["state"] == "rejected"
        assert "no active session" in r.json()["result"]["reason"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_wake_now_runs_full_session(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    fake_llm.script(
        [
            {"content": TOOL_PYTHON},
            {"content": COMPLETE},
            {"content": CURATOR_OK},
        ]
    )
    app, engine, factory, gateway = await _make_app(
        scratch_url, fake_llm, tmp_path / "ws", with_orchestrator=True
    )
    question_id = await _seed_question(factory)
    try:
        async with _client(app) as client:
            r = await client.post(
                "/api/v1/commands",
                json={"type": "wake_now", "idempotency_key": "w1"},
            )
            assert r.status_code == 202
            assert r.json()["state"] == "completed"

            # poll until the session finishes and the node returns to idle
            for _ in range(100):
                r = await client.get("/api/v1/status")
                data = r.json()
                if data["node_state"] == "idle" and data["session"] is None:
                    break
                await asyncio.sleep(0.1)
            else:
                pytest.fail(f"session did not finish: {data}")

            assert data["counts"]["sessions"] == 1

            # timeline now has the full audit trail
            r = await client.get("/api/v1/timeline")
            events = r.json()["events"]
            assert len(events) >= 8
            types = [e["type"] for e in events]
            assert "session_started" in types
            assert "session_committed" in types

            # the question was verified (M1: simple commit)
            from sqlalchemy import text

            async with engine.connect() as conn:
                question_state = (
                    await conn.execute(text("SELECT state FROM questions WHERE id=:q"), {"q": question_id})
                ).scalar_one()
            assert question_state == "verified"
    finally:
        if gateway is not None:
            await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_wake_now_without_orchestrator(migrated_db, fake_llm: FakeLLM, tmp_path: Path) -> None:
    scratch_url, _engine = migrated_db
    app, engine, _factory, _gw = await _make_app(scratch_url, fake_llm, tmp_path / "ws")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/commands",
            json={"type": "wake_now", "idempotency_key": "w2"},
        )
        assert r.json()["state"] == "rejected"
        assert "orchestrator not attached" in r.json()["result"]["reason"]
    await engine.dispose()
