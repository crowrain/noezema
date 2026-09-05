from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from uuid import uuid4

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.web import FixedWindowRateLimiter, WebSecurityConfig, create_app
from apps.web.models import DegradedReason, HostStatusProjection
from packages.domain import OperatorCommandState
from packages.persistence.models import (
    MessageRecord,
    OperatorCommandRecord,
    QuestionRecord,
    RuntimeControlRecord,
)
from tests.unit.web.conftest import TEST_ORIGIN, TEST_PASSWORD

NOW = datetime(2030, 8, 20, 12, 0, tzinfo=UTC)


def _run(scenario):
    return asyncio.run(scenario())


async def _client(app):
    transport = httpx.ASGITransport(app=app)
    return httpx.AsyncClient(
        transport=transport,
        base_url=TEST_ORIGIN,
    )


async def _login(client: httpx.AsyncClient) -> httpx.Response:
    return await client.post(
        "/api/auth/login",
        json={"password": TEST_PASSWORD},
        headers={"Origin": TEST_ORIGIN},
    )


def test_owner_console_and_assets_have_a_hardened_static_boundary(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    app = create_app(session_factory, security=web_security, clock=lambda: NOW)

    async def scenario() -> None:
        async with await _client(app) as client:
            page = await client.get("/")
            stylesheet = await client.get("/assets/app.css")
            script = await client.get("/assets/app.js")

            assert page.status_code == stylesheet.status_code == script.status_code == 200
            assert page.headers["cache-control"] == "no-store"
            assert "default-src 'self'" in page.headers["content-security-policy"]
            assert "script-src 'self'" in page.headers["content-security-policy"]
            assert "'unsafe-inline'" not in page.headers["content-security-policy"]
            assert page.headers["x-frame-options"] == "DENY"
            assert page.headers["x-content-type-options"] == "nosniff"
            assert stylesheet.headers["content-type"].startswith("text/css")
            assert script.headers["content-type"].startswith("text/javascript")

            assert '<script src="/assets/app.js" defer></script>' in page.text
            assert '<link rel="stylesheet" href="/assets/app.css">' in page.text
            assert "<style" not in page.text
            assert "<script>" not in page.text
            assert ".innerHTML" not in script.text
            assert "localStorage" not in script.text
            assert 'new EventSource("/api/timeline/stream?after=0"' in script.text
            assert 'headers.set("X-CSRF-Token"' in script.text
            assert "crypto.randomUUID()" in script.text

    _run(scenario)


def test_owner_login_protects_query_api_and_sets_hardened_cookie(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    app = create_app(session_factory, security=web_security, clock=lambda: NOW)

    async def scenario() -> None:
        async with await _client(app) as client:
            denied = await client.get("/api/status")
            assert denied.status_code == 401
            assert denied.headers["cache-control"] == "no-store"
            assert (await client.get("/api/timeline/stream")).status_code == 401

            login = await _login(client)
            assert login.status_code == 200
            assert login.json()["subject"] == "owner"
            cookie = login.headers["set-cookie"].lower()
            assert "httponly" in cookie
            assert "secure" in cookie
            assert "samesite=strict" in cookie

            status = await client.get("/api/status")
            assert status.status_code == 200
            assert status.json() == {
                "mode": "normal",
                "command_api_enabled": True,
                "reasons": [],
                "host": {
                    "snapshot_state": "current",
                    "snapshot_observed_at": "2030-08-20T12:00:00Z",
                    "snapshot_age_seconds": 0.0,
                    "boot_id": "00000000-0000-0000-0000-000000000000",
                    "target": {
                        "name": "noezema-runtime.target",
                        "active_state": "active",
                        "sub_state": "active",
                        "result": "success",
                    },
                    "members": [
                        {
                            "name": "noezema-orchestrator.service",
                            "active_state": "active",
                            "sub_state": "running",
                            "result": "success",
                        }
                    ],
                    "maintenance_active": False,
                    "host_transition_active": False,
                    "host_policy_change_active": False,
                    "reasons": [],
                },
                "operational": {
                    "node_state": "sleeping",
                    "activity": "idle",
                    "active_session": None,
                    "scheduler": {
                        "busy": False,
                        "wake_generation": 0,
                        "handled_wake_generation": 0,
                        "next_scheduled_at": None,
                        "backoff_until": None,
                        "consecutive_failures": 0,
                        "last_session_id": None,
                        "last_terminal_state": None,
                        "last_error_class": None,
                    },
                    "queued_questions": 0,
                    "pending_messages": 0,
                    "pending_commands": 0,
                    "observed_at": "2030-08-20T12:00:00Z",
                },
                "observed_at": "2030-08-20T12:00:00Z",
            }
            assert (await client.get("/api/timeline?limit=0")).status_code == 422
            assert (await client.get("/api/timeline?cursor=broken!")).status_code == 400
            invalid_stream = await client.get(
                "/api/timeline/stream",
                headers={"Origin": TEST_ORIGIN, "Last-Event-ID": "broken"},
            )
            assert invalid_stream.status_code == 400
            future_stream = await client.get(
                "/api/timeline/stream?after=1",
                headers={"Origin": TEST_ORIGIN},
            )
            assert future_stream.status_code == 400
            foreign_stream = await client.get(
                "/api/timeline/stream",
                headers={"Origin": "https://attacker.example"},
            )
            assert foreign_stream.status_code == 403
            missing = await client.get("/api/sessions/00000000-0000-0000-0000-000000000001")
            assert missing.status_code == 404

    _run(scenario)


def test_degraded_host_keeps_status_available_and_disables_command_api(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    class _DegradedHostReader:
        def status(self, *, observed_at: datetime) -> HostStatusProjection:
            return HostStatusProjection(
                snapshot_state="missing",
                snapshot_observed_at=None,
                snapshot_age_seconds=None,
                boot_id=None,
                target=None,
                members=(),
                maintenance_active=True,
                host_transition_active=False,
                host_policy_change_active=False,
                reasons=(
                    DegradedReason.UNIT_STATE_MISSING,
                    DegradedReason.MAINTENANCE_ACTIVE,
                ),
            )

    app = create_app(
        session_factory,
        security=web_security,
        clock=lambda: NOW,
        host_status_reader=_DegradedHostReader(),
    )

    async def scenario() -> None:
        async with await _client(app) as client:
            login = await _login(client)
            status = await client.get("/api/status")
            assert status.status_code == 200
            assert status.json()["mode"] == "degraded"
            assert status.json()["operational"]["node_state"] == "sleeping"
            assert status.json()["command_api_enabled"] is False

            rejected = await client.post(
                "/api/messages",
                json={
                    "idempotency_key": str(uuid4()),
                    "body": "Must not be queued during maintenance",
                },
                headers={
                    "Origin": TEST_ORIGIN,
                    "X-CSRF-Token": login.json()["csrf_token"],
                },
            )
            assert rejected.status_code == 503
            assert rejected.json()["detail"] == {
                "code": "runtime_unavailable",
                "reasons": ["unit_state_missing", "maintenance_active"],
            }

            logout = await client.post(
                "/api/auth/logout",
                headers={
                    "Origin": TEST_ORIGIN,
                    "X-CSRF-Token": login.json()["csrf_token"],
                },
            )
            assert logout.status_code == 204

    _run(scenario)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MessageRecord)) == 0


def test_database_outage_keeps_host_only_status_available(
    web_security: WebSecurityConfig,
) -> None:
    def unavailable_session() -> Session:
        raise OperationalError("database unavailable", {}, ConnectionError())

    app = create_app(
        unavailable_session,
        security=web_security,
        clock=lambda: NOW,
    )

    async def scenario() -> None:
        async with await _client(app) as client:
            liveness = await client.get("/api/health/live")
            assert liveness.status_code == 200
            assert liveness.json() == {"status": "ok"}
            login = await _login(client)
            status = await client.get("/api/status")
            assert status.status_code == 200
            assert status.json()["mode"] == "degraded"
            assert status.json()["reasons"] == ["database_unavailable"]
            assert status.json()["operational"] is None

            rejected = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "wake_now",
                    "reason": "database is down",
                },
                headers={
                    "Origin": TEST_ORIGIN,
                    "X-CSRF-Token": login.json()["csrf_token"],
                },
            )
            assert rejected.status_code == 503
            assert rejected.json()["detail"]["code"] == "runtime_unavailable"

    _run(scenario)


def test_timeline_stream_has_eventsource_headers_and_stops_at_auth_expiry(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    clock_calls = 0

    def expiring_clock() -> datetime:
        nonlocal clock_calls
        clock_calls += 1
        if clock_calls <= 2:
            return NOW
        return NOW.replace(hour=13)

    app = create_app(session_factory, security=web_security, clock=expiring_clock)

    async def scenario() -> None:
        async with await _client(app) as client:
            assert (await _login(client)).status_code == 200
            response = await client.get(
                "/api/timeline/stream",
                headers={"Origin": TEST_ORIGIN},
            )
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            assert response.headers["cache-control"] == "no-cache, no-transform"
            assert response.headers["x-accel-buffering"] == "no"
            assert response.text == "retry: 2000\n\n"

    _run(scenario)


def test_message_command_requires_csrf_and_is_idempotently_queued(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    current = [NOW]
    app = create_app(session_factory, security=web_security, clock=lambda: current[0])
    key = str(uuid4())

    async def scenario() -> None:
        async with await _client(app) as client:
            login = await _login(client)
            csrf = login.json()["csrf_token"]
            payload = {
                "idempotency_key": key,
                "body": "Исследуй журнал ошибок",
                "priority": 5,
                "expires_in_seconds": 3600,
            }

            missing_csrf = await client.post("/api/messages", json=payload)
            assert missing_csrf.status_code == 403, missing_csrf.text
            wrong_origin = await client.post(
                "/api/messages",
                json=payload,
                headers={
                    "Origin": "https://attacker.example",
                    "X-CSRF-Token": csrf,
                },
            )
            assert wrong_origin.status_code == 403

            headers = {"Origin": TEST_ORIGIN, "X-CSRF-Token": csrf}
            accepted = await client.post("/api/messages", json=payload, headers=headers)
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["state"] == "queued"
            assert accepted.json()["newly_created"] is True

            current[0] = NOW.replace(minute=1)
            retry = await client.post("/api/messages", json=payload, headers=headers)
            assert retry.status_code == 202
            assert retry.json()["id"] == accepted.json()["id"]
            assert retry.json()["newly_created"] is False

            conflict = await client.post(
                "/api/messages",
                json={**payload, "body": "Другой запрос"},
                headers=headers,
            )
            assert conflict.status_code == 409

    _run(scenario)
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MessageRecord)) == 1
        assert db.scalar(select(func.count()).select_from(QuestionRecord)) == 1
        message = db.scalar(select(MessageRecord))
        assert message is not None and message.sender == "owner"


def test_operator_command_is_durable_intent_without_direct_effect(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    app = create_app(session_factory, security=web_security, clock=lambda: NOW)

    async def scenario() -> None:
        async with await _client(app) as client:
            login = await _login(client)
            headers = {
                "Origin": TEST_ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
            }
            accepted = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "pause",
                    "reason": "maintenance",
                },
                headers=headers,
            )
            assert accepted.status_code == 202, accepted.text
            assert accepted.json()["state"] == "accepted"

            invalid = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "abort_session",
                    "reason": "missing target",
                },
                headers=headers,
            )
            assert invalid.status_code == 422

            oversized = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "set_budget",
                    "reason": "oversized",
                    "arguments": {"payload": "x" * (17 * 1024)},
                },
                headers=headers,
            )
            assert oversized.status_code == 422

    _run(scenario)
    with session_factory() as db:
        command = db.scalar(select(OperatorCommandRecord))
        runtime = db.get(RuntimeControlRecord, "global")
        assert command is not None
        assert command.actor_id == "owner"
        assert command.state == OperatorCommandState.ACCEPTED.value
        assert runtime is not None and runtime.node_state == "sleeping"


def test_logout_requires_csrf_and_clears_session(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    app = create_app(session_factory, security=web_security, clock=lambda: NOW)

    async def scenario() -> None:
        async with await _client(app) as client:
            login = await _login(client)
            csrf = login.json()["csrf_token"]
            assert (await client.post("/api/auth/logout")).status_code == 403
            logout = await client.post(
                "/api/auth/logout",
                headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": csrf},
            )
            assert logout.status_code == 204
            assert (await client.get("/api/status")).status_code == 401

    _run(scenario)


def test_login_and_command_rate_limits_are_separate(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    limited = WebSecurityConfig(
        admin_password_hash=web_security.admin_password_hash,
        session_secret=web_security.session_secret,
        allowed_origins=web_security.allowed_origins,
        cookie_secure=True,
        session_ttl_seconds=web_security.session_ttl_seconds,
        login_attempt_limit=2,
        command_request_limit=1,
        rate_window_seconds=60,
    )
    app = create_app(
        session_factory,
        security=limited,
        clock=lambda: NOW,
        rate_limiter=FixedWindowRateLimiter(monotonic=lambda: 10.0),
    )

    async def scenario() -> None:
        async with await _client(app) as client:
            for _ in range(2):
                rejected = await client.post(
                    "/api/auth/login",
                    json={"password": "wrong"},
                    headers={"Origin": TEST_ORIGIN},
                )
                assert rejected.status_code == 401
            limited_login = await _login(client)
            assert limited_login.status_code == 429
            assert limited_login.headers["retry-after"] == "60"

        second_app = create_app(
            session_factory,
            security=limited,
            clock=lambda: NOW,
            rate_limiter=FixedWindowRateLimiter(monotonic=lambda: 10.0),
        )
        async with await _client(second_app) as client:
            login = await _login(client)
            headers = {
                "Origin": TEST_ORIGIN,
                "X-CSRF-Token": login.json()["csrf_token"],
            }
            first = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "wake_now",
                    "reason": "test",
                },
                headers=headers,
            )
            assert first.status_code == 202, first.text
            second = await client.post(
                "/api/operator-commands",
                json={
                    "idempotency_key": str(uuid4()),
                    "type": "wake_now",
                    "reason": "test",
                },
                headers=headers,
            )
            assert second.status_code == 429

    _run(scenario)


def test_openapi_does_not_publish_internal_or_staging_fields(
    session_factory: sessionmaker[Session],
    web_security: WebSecurityConfig,
) -> None:
    app = create_app(session_factory, security=web_security)
    document = app.openapi()
    serialized = str(document).lower()

    assert "raw_response_artifact" not in serialized
    assert "context_manifest" not in serialized
    assert "chain_of_thought" not in serialized
    assert "session_staging" not in serialized
    assert "arguments_json" not in serialized
    assert "request_sha256" not in serialized

    mutation_methods = {
        (route.path, method)
        for route in app.routes
        for method in (getattr(route, "methods", None) or set())
        if method in {"POST", "PUT", "PATCH", "DELETE"}
    }
    assert mutation_methods == {
        ("/api/auth/login", "POST"),
        ("/api/auth/logout", "POST"),
        ("/api/messages", "POST"),
        ("/api/operator-commands", "POST"),
    }
