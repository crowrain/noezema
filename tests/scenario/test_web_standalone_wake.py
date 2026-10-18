"""Scenario: the STANDALONE web entry answers «wake now» (T7.59(в), §13.1–§13.3).

The dev stand runs `python -m apps.web.main` → `build_standalone_app()`: the app is created first
(it owns the engine + session factory) and only then the orchestrator is built on that factory and
attached to `app.state.orchestrator`. The command handler used to close over the create_app
argument instead, which is None in this path — so on the real VM «wake now» answered
`rejected: orchestrator not attached` and nothing started. Every existing test passed the
orchestrator as a `create_app` argument, which is exactly why the defect was invisible.

This test therefore goes through the stand's own entry: env-driven `build_standalone_app()` against
the scratch DB with the FakeLLM (no GPU, no real LLM host) and the stub executor.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.api import build_standalone_app
from hostctl.unit_state import publish_unit_state
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]

TOOL_PYTHON: JsonDict = {
    "public_rationale": "Проверить вычисление",
    "decision": {"kind": "tool", "tool": "python.execute", "arguments": {"code": "print(6*7)"}},
}
COMPLETE: JsonDict = {
    "public_rationale": "Вопрос отвечен",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение",
    "claims": [{"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _seed_question(scratch_url: str, text: str) -> None:
    """Seed a candidate the FIFO selector can take (own engine: this test's app owns its own)."""
    engine = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, transaction(db):
            await QuestionRepository.create(db, ORMQuestion(text=text, origin=QuestionOrigin.SEEDED.value))
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_standalone_wake_now_reaches_admission_and_runs(
    migrated_db, fake_llm: FakeLLM, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_url, _fixture_engine = migrated_db
    data_root = tmp_path / "var-lib-noezema-dev"
    host_lib = data_root / "host"
    unit_state = host_lib / "unit-state.json"
    host_lib.mkdir(parents=True)

    # The stand shape: everything the standalone entry reads comes from the environment.
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_HOST_LIB", str(host_lib))
    monkeypatch.setenv("NOEZEMA_UNIT_STATE", str(unit_state))
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("NOEZEMA_LLM_MODEL", "fake-thinker")
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")  # no containers in a unit test
    monkeypatch.delenv("NOEZEMA_ADMIN_TOKEN", raising=False)

    await _seed_question(scratch_url, "Сколько будет 6*7?")
    fake_llm.script([{"content": TOOL_PYTHON}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    # The stand publishes unit-state like noezema-dev-unit-state.timer does; without a fresh
    # snapshot the Command API is 423 (T3.24) and this test would not reach the orchestrator.
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})

    app = build_standalone_app()
    try:
        # The real lifespan runs here: ASGITransport does not run it on its own.
        async with app.router.lifespan_context(app):
            assert app.state.orchestrator is not None, "the standalone entry must attach an orchestrator"
            # T7.59(в) fix under test: the stub workspace follows NOEZEMA_DATA_ROOT, not /var/lib/….
            assert (data_root / "workspace").is_dir()

            async with _client(app) as client:
                r = await client.post("/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-stand"})
                assert r.status_code == 202, r.text
                body = r.json()
                result = body["result"] or {}
                assert result.get("reason") != "orchestrator not attached", (
                    "the standalone app must answer wake_now with its attached orchestrator: "
                    f"{body}"
                )
                # It reached the admission gate and was granted the wake.
                assert body["state"] == "completed", body
                assert result["node_state"] == "session_running"

                data: JsonDict = {}
                for _ in range(150):
                    data = (await client.get("/api/v1/status")).json()
                    if data["node_state"] == "idle" and data["session"] is None:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"сессия через standalone-вход не завершилась: {data}")

                assert data["counts"]["sessions"] == 1
                types = [e["type"] for e in (await client.get("/api/v1/timeline")).json()["events"]]
                assert "session_started" in types and "session_committed" in types
    finally:
        await app.state.engine.dispose()
