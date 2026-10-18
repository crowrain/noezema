"""Scenario: operator question intake over HTTP (T7.59, §13.1/§13.2).

POST /api/v1/questions is a Command-API endpoint (admin token + the host
fail-closed gate); GET /api/v1/questions is an open query (§13.1). Also
checks that the intake lands in the queue the FIFO selector serves, and that
the main page carries the form and the queue table.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord
from hostctl.unit_state import publish_unit_state
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository

pytestmark = [pytest.mark.scenario]


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(scratch_url: str, host_lib: Path, unit_state: Path, admin_token: str | None = None):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state)
    app = create_app(engine=engine, factory=factory, host_adapter=adapter, admin_token=admin_token)
    return app, engine, factory


def _healthy_host(host_lib: Path, unit_state: Path) -> None:
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})


def _degraded_host(host_lib: Path, unit_state: Path) -> None:
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})
    store = JournalStore(host_lib)
    store.write_record(
        TransitionRecord(
            attempt_id="t1",
            operation="offline_rules",
            candidate_snapshot_id=None,
            base_snapshot_id=None,
            observed_pointer_tuple={},
            state=STATE_CHECKING,
        )
    )


@pytest.mark.asyncio
async def test_intake_requires_the_admin_token(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.post("/api/v1/questions", json={"text": "без токена"})
        assert r.status_code == 401
        r = await client.post(
            "/api/v1/questions",
            json={"text": "неверный токен"},
            headers={"X-Admin-Token": "wrong"},
        )
        assert r.status_code == 401
        # nothing was queued by the refused attempts
        r = await client.get("/api/v1/questions")
        assert r.status_code == 200
        assert r.json()["count"] == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_intake_then_queue_view_and_replay(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, factory = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/questions",
            json={"text":  "  Где зимуют ежи?  ", "priority": 6},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 201
        created = r.json()
        assert created["origin"] == QuestionOrigin.MESSAGE.value
        assert created["state"] == QuestionState.CANDIDATE.value
        assert created["text"] == "Где зимуют ежи?"
        assert created["priority"] == 6
        assert created["position"] == 1
        assert created["replayed"] is False

        # idempotent replay: the same formulation (whitespace-insensitive)
        r = await client.post(
            "/api/v1/questions",
            json={"text": "Где зимуют ежи?\n", "priority": 1},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 200
        replayed = r.json()
        assert replayed["id"] == created["id"]
        assert replayed["replayed"] is True

        r = await client.get("/api/v1/questions")
        assert r.status_code == 200
        rows = r.json()["questions"]
        assert [row["id"] for row in rows] == [created["id"]]
        assert rows[0]["position"] == 1
        assert rows[0]["session"] is None

    async with factory() as db:
        count = (
            await db.execute(text("SELECT count(*) FROM questions"))
        ).scalar_one()
    assert int(count) == 1  # the replay created no duplicate row
    await engine.dispose()


@pytest.mark.asyncio
async def test_queries_stay_open_and_mutation_is_closed_on_a_degraded_host(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _degraded_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.get("/api/v1/questions")  # read-only observer mode keeps queries open
        assert r.status_code == 200
        r = await client.post(
            "/api/v1/questions",
            json={"text": "нельзя на деградированном хосте"},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 423
        # the identical fail-closed body as /api/v1/commands (T3.24)
        body = r.json()
        assert body["rejected"] is True
        assert body["reason"] == "host_not_healthy"
        assert body["recovery_state"]
    await engine.dispose()


@pytest.mark.asyncio
async def test_validation_rejects_before_the_queue(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, factory = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        # whitespace-only text passes the request schema and is refused by the
        # intake service (400, with the operator-readable reason)
        r = await client.post(
            "/api/v1/questions", json={"text": "   "}, headers={"X-Admin-Token": "secret"}
        )
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_question"
        # out-of-band priority is refused by the request schema (422)
        r = await client.post(
            "/api/v1/questions", json={"text": "q", "priority": 10**6}, headers={"X-Admin-Token": "secret"}
        )
        assert r.status_code == 422
        # an unknown field cannot smuggle a different origin/state
        r = await client.post(
            "/api/v1/questions",
            json={"text": "q", "origin": "seeded"},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 422
    async with factory() as db:
        count = (await db.execute(text("SELECT count(*) FROM questions"))).scalar_one()
    assert int(count) == 0
    await engine.dispose()


@pytest.mark.asyncio
async def test_operator_priority_moves_the_question_to_the_head_of_fifo(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine, factory = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with factory() as db, transaction(db):
        older = await QuestionRepository.create(
            db, ORMQuestion(text="старый кандидат", origin=QuestionOrigin.SEEDED.value, priority=0)
        )
    async with _client(app) as client:
        r = await client.post(
            "/api/v1/questions",
            json={"text": "срочный вопрос оператора", "priority": 9},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 201

    async with factory() as db:
        selected = await FIFOQuestionSelector().select(db)
        assert selected is not None and selected.text == "срочный вопрос оператора"

    # the queue view agrees with the selector about the order
    async with _client(app) as client:
        r = await client.get("/api/v1/questions")
        rows = r.json()["questions"]
        assert [row["position"] for row in rows] == [1, 2]
        assert rows[0]["origin"] == QuestionOrigin.MESSAGE.value
        assert ids_of(rows).index(str(older.id)) == 1
    await engine.dispose()


def ids_of(rows: list[dict]) -> list[str]:
    return [row["id"] for row in rows]


@pytest.mark.asyncio
async def test_main_page_carries_the_ask_form_and_the_queue_table(migrated_db, tmp_path: Path) -> None:
    """§13.3: the main page is a thin viewer over the same JSON API — no new
    JS dependencies, only the form + the queue table over §13.1/§13.2 routes.

    T7.59(b) adds the «wake now» control: it posts the closed `wake_now` command to the
    existing command route (T3.18/T3.29), so the marker check covers the route too.
    """
    scratch_url, _ = migrated_db
    app, engine, _factory = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200
        html = r.text
    for marker in (
        'id="ask-form"',
        'id="ask-text"',
        'id="ask-priority"',
        'id="ask-token"',
        'id="queue"',
        "Задать вопрос",
        "Очередь вопросов",
        "/api/v1/questions",
        "X-Admin-Token",
        "sessionStorage",  # the token is remembered for the session, not on disk
        'id="wake-now"',  # T7.59(b): wake_now button over the existing command route
        "/api/v1/commands",
        "wake now",
    ):
        assert marker in html
    await engine.dispose()
