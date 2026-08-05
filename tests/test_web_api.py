"""Tests for Web API — FastAPI routes backed by SQLite in-memory."""

from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from packages.domain.models.base import Base
from packages.domain.db_config import Database


# ---------------------------------------------------------------------------
# Test DB helpers — use SQLite in-memory via Database singleton
# ---------------------------------------------------------------------------

async def _init_test_db():
    """Create in-memory SQLite DB and set up via Database singleton."""
    from sqlalchemy import text
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    Database._engine = engine
    Database._session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _cleanup_test_db():
    """Dispose test engine."""
    if Database._engine is not None:
        await Database._engine.dispose()
        Database._engine = None
        Database._session_factory = None


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

async def test_status_page():
    """GET / returns JSON with DB counts."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/")
            assert resp.status_code == 200
            data = resp.json()
            assert data["name"] == "noezema"
            assert data["claims_count"] == 0
            assert data["pending_questions"] == 0
    finally:
        await _cleanup_test_db()


async def test_send_and_list_messages():
    """POST /api/message + GET /api/messages roundtrip."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/message", params={"sender": "alice", "body": "hello"})
            assert resp.status_code == 200
            data = resp.json()
            assert data["state"] == "queued"
            assert "id" in data

            resp = await ac.get("/api/messages")
            assert resp.status_code == 200
            msgs = resp.json()
            assert len(msgs) == 1
            assert msgs[0]["sender"] == "alice"
            assert msgs[0]["body"] == "hello"
    finally:
        await _cleanup_test_db()


async def test_create_and_list_questions():
    """POST /api/questions + GET /api/questions roundtrip."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/questions", params={
                "statement": "Why is the sky blue?",
                "source": "seeded",
            })
            assert resp.status_code == 200
            assert "id" in resp.json()

            resp = await ac.get("/api/questions")
            assert resp.status_code == 200
            qs = resp.json()
            assert len(qs) == 1
            assert qs[0]["statement"] == "Why is the sky blue?"
    finally:
        await _cleanup_test_db()


async def test_timeline_empty():
    """GET /api/timeline returns empty list initially."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/timeline")
            assert resp.status_code == 200
            assert resp.json() == []
    finally:
        await _cleanup_test_db()


async def test_operator_command():
    """POST /api/command logs audit event."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/command", params={"actor": "admin", "type": "wake_now"})
            assert resp.status_code == 200
            assert resp.json()["state"] == "accepted"

            resp = await ac.get("/api/timeline")
            events = resp.json()
            assert len(events) == 1
            assert events[0]["event_type"] == "operator_command"
    finally:
        await _cleanup_test_db()


async def test_unknown_command_rejected():
    """POST /api/command with invalid type returns 400."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post("/api/command", params={"actor": "admin", "type": "explode"})
            assert resp.status_code == 400
    finally:
        await _cleanup_test_db()


async def test_sessions_list_empty():
    """GET /api/sessions returns empty list initially."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/sessions")
            assert resp.status_code == 200
            assert resp.json() == []
    finally:
        await _cleanup_test_db()


async def test_claims_list_empty():
    """GET /api/claims returns empty list initially."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/claims")
            assert resp.status_code == 200
            assert resp.json() == []
    finally:
        await _cleanup_test_db()


async def test_session_not_found():
    """GET /api/sessions/<invalid> returns 404."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/sessions/00000000-0000-0000-0000-000000000000")
            assert resp.status_code == 404
    finally:
        await _cleanup_test_db()


async def test_claim_not_found():
    """GET /api/claims/<invalid> returns 404."""
    from apps.web.app import app
    await _init_test_db()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/claims/00000000-0000-0000-0000-000000000000")
            assert resp.status_code == 404
    finally:
        await _cleanup_test_db()
