"""Scenario: the M3 web slice (T3.18–T3.24, §13).

Query/Command separation + auth, fail-closed commands on an unhealthy host,
the SSE timeline, session detail, message TTL, and the main/session pages.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.api import create_app
from apps.web.host_status import HostStatusAdapter
from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord
from hostctl.unit_state import publish_unit_state


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make(scratch_url: str, host_lib: Path, unit_state: Path, admin_token: str | None = None):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    adapter = HostStatusAdapter(host_lib_base=host_lib, unit_state_path=unit_state)
    app = create_app(engine=engine, factory=factory, host_adapter=adapter, admin_token=admin_token)
    return app, engine


def _healthy_host(host_lib: Path, unit_state: Path) -> None:
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})


def _unresolved_host(host_lib: Path, unit_state: Path) -> None:
    host_lib.mkdir(parents=True, exist_ok=True)
    publish_unit_state(unit_state, units={"noezema-runtime.target": "inactive"})
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
async def test_status_includes_host_view(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.get("/api/v1/status")
        assert r.status_code == 200
        data = r.json()
        assert data["host"]["healthy"] is True
        assert data["host"]["recovery_state"] == "none"
    await engine.dispose()


@pytest.mark.asyncio
async def test_commands_fail_closed_on_unresolved_host(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _unresolved_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.post("/api/v1/commands", json={"type": "pause", "idempotency_key": "k1"})
        assert r.status_code == 423
        body = r.json()
        assert body["rejected"] is True
        assert body["reason"] == "host_not_healthy"
        assert any("unresolved_host_transition" in w for w in body["warnings"])
    await engine.dispose()


@pytest.mark.asyncio
async def test_commands_succeed_on_healthy_host(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        r = await client.post("/api/v1/commands", json={"type": "pause", "idempotency_key": "k1"})
        assert r.status_code == 202
        assert r.json()["state"] == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_command_requires_admin_token(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        # no token -> 401
        r = await client.post("/api/v1/commands", json={"type": "pause", "idempotency_key": "k1"})
        assert r.status_code == 401
        # wrong token -> 401
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k1"},
            headers={"X-Admin-Token": "wrong"},
        )
        assert r.status_code == 401
        # correct token -> accepted
        r = await client.post(
            "/api/v1/commands",
            json={"type": "pause", "idempotency_key": "k1"},
            headers={"X-Admin-Token": "secret"},
        )
        assert r.status_code == 202
        assert r.json()["state"] == "completed"
    await engine.dispose()


@pytest.mark.asyncio
async def test_query_endpoints_need_no_token(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(
        scratch_url, tmp_path / "host", tmp_path / "unit-state.json", admin_token="secret"
    )
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    async with _client(app) as client:
        # queries are open even with an admin token configured
        r = await client.get("/api/v1/status")
        assert r.status_code == 200
        r = await client.get("/api/v1/messages")
        assert r.status_code == 200
    await engine.dispose()


@pytest.mark.asyncio
async def test_session_detail_endpoint(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    # seed a session row
    sid = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) VALUES "
                "(:id, 'exploring', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
        await conn.commit()
    async with _client(app) as client:
        r = await client.get(f"/api/v1/sessions/{sid}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == str(sid)
        assert data["state"] == "exploring"
        assert isinstance(data["events"], list)
        # unknown session -> 404
        r = await client.get(f"/api/v1/sessions/{uuid.uuid4()}")
        assert r.status_code == 404
    await engine.dispose()


@pytest.mark.asyncio
async def test_message_ttl_expires(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    mid = uuid.uuid4()
    past = datetime.now(UTC) - timedelta(seconds=5)
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO messages (id, sender, body, priority, state, expires_at) "
                "VALUES (:id, 'owner', 'привет', 0, 'created', :exp)"
            ),
            {"id": mid, "exp": past},
        )
        await conn.commit()
    async with _client(app) as client:
        r = await client.get("/api/v1/messages")
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert any(m["id"] == str(mid) and m["state"] == "expired" for m in msgs)
    await engine.dispose()


@pytest.mark.asyncio
async def test_main_and_session_pages_render(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    sid = uuid.uuid4()
    async with _client(app) as client:
        r = await client.get("/")
        assert r.status_code == 200
        assert "NOEZEMA" in r.text
        assert "/api/v1/status" in r.text
        r = await client.get(f"/session/{sid}")
        assert r.status_code == 200
        assert str(sid) in r.text
    await engine.dispose()


@pytest.mark.asyncio
async def test_sse_timeline_streams(migrated_db, tmp_path: Path) -> None:
    scratch_url, _ = migrated_db
    app, engine = await _make(scratch_url, tmp_path / "host", tmp_path / "unit-state.json")
    _healthy_host(tmp_path / "host", tmp_path / "unit-state.json")
    sid = uuid.uuid4()
    aid = uuid.uuid4()
    async with engine.connect() as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) VALUES "
                "(:id, 'exploring', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
        await conn.execute(
            text(
                "INSERT INTO audit_events (id, session_id, sequence, type, public_summary, payload) "
                "VALUES (:id, :s, 1, 'session_state_changed', 'phase', '{}'::jsonb)"
            ),
            {"id": aid, "s": sid},
        )
        await conn.execute(
            text(
                "INSERT INTO outbox_events (id, audit_event_id, topic, payload) "
                "VALUES (:id, :a, 'timeline', '{}'::jsonb)"
            ),
            {"id": uuid.uuid4(), "a": aid},
        )
        await conn.commit()
    # a committed outbox event is streamed as a timeline event; max_events
    # bounds the stream so it terminates
    async with _client(app) as client:
        got_timeline = False
        async with client.stream(
            "GET", f"/api/v1/timeline/sse?session_id={sid}&poll_ms=60&max_events=1"
        ) as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            async for line in resp.aiter_lines():
                if "event: timeline" in line:
                    got_timeline = True
        assert got_timeline
    await engine.dispose()
