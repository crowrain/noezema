from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session, sessionmaker

from apps.web import create_app

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _get(app, path: str) -> httpx.Response:
    async def request() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://testserver",
        ) as client:
            return await client.get(path)

    return asyncio.run(request())


def test_query_api_exposes_strict_read_only_routes(
    session_factory: sessionmaker[Session],
) -> None:
    app = create_app(session_factory, clock=lambda: NOW)

    status = _get(app, "/api/status")
    assert status.status_code == 200
    assert status.json() == {
        "node_state": "sleeping",
        "activity": "idle",
        "active_session": None,
        "queued_questions": 0,
        "pending_messages": 0,
        "pending_commands": 0,
        "observed_at": "2026-08-20T12:00:00Z",
    }
    assert _get(app, "/api/timeline?limit=0").status_code == 422
    assert _get(app, "/api/timeline?cursor=broken!").status_code == 400
    assert _get(app, "/api/sessions/00000000-0000-0000-0000-000000000001").status_code == 404

    api_methods = {
        method
        for route in app.routes
        if route.path.startswith("/api/")
        for method in (route.methods or set())
    }
    assert api_methods <= {"GET", "HEAD"}


def test_openapi_does_not_publish_internal_or_staging_fields(
    session_factory: sessionmaker[Session],
) -> None:
    document = create_app(session_factory).openapi()
    serialized = str(document).lower()

    assert "raw_response_artifact" not in serialized
    assert "context_manifest" not in serialized
    assert "chain_of_thought" not in serialized
    assert "session_staging" not in serialized
    assert "arguments_json" not in serialized
    assert "idempotency_key" not in serialized
