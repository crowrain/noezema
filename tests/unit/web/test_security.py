from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.web.security import (
    AuthenticationError,
    AuthManager,
    CsrfValidationError,
    FixedWindowRateLimiter,
    OriginValidationError,
    RateLimitExceeded,
    WebSecurityConfig,
)
from tests.unit.web.conftest import TEST_ORIGIN, TEST_PASSWORD

NOW = datetime(2030, 8, 20, 12, 0, tzinfo=UTC)


def test_signed_session_rejects_wrong_password_tampering_and_expiry(
    web_security: WebSecurityConfig,
) -> None:
    current = [NOW]
    auth = AuthManager(web_security, clock=lambda: current[0])

    with pytest.raises(AuthenticationError, match="invalid credentials"):
        auth.login("not the password")

    issued = auth.login(TEST_PASSWORD)
    principal = auth.authenticate(issued.token)
    assert principal.subject == "owner"
    assert issued.csrf_token == auth.csrf_token(principal)

    payload, signature = issued.token.split(".")
    replacement = "A" if signature[-1] != "A" else "B"
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        auth.authenticate(f"{payload}.{signature[:-1]}{replacement}")

    current[0] = NOW + timedelta(seconds=web_security.session_ttl_seconds + 1)
    with pytest.raises(AuthenticationError, match="invalid or expired"):
        auth.authenticate(issued.token)


def test_csrf_is_bound_to_one_session_and_origin_is_exact(
    web_security: WebSecurityConfig,
) -> None:
    auth = AuthManager(web_security, clock=lambda: NOW)
    first = auth.login(TEST_PASSWORD)
    second = auth.login(TEST_PASSWORD)

    auth.verify_csrf(first.principal, first.csrf_token)
    with pytest.raises(CsrfValidationError):
        auth.verify_csrf(first.principal, second.csrf_token)
    with pytest.raises(CsrfValidationError):
        auth.verify_csrf(first.principal, None)

    auth.verify_origin(TEST_ORIGIN)
    auth.verify_origin(None)
    with pytest.raises(OriginValidationError):
        auth.verify_origin("https://attacker.example")
    with pytest.raises(OriginValidationError):
        auth.verify_origin(f"{TEST_ORIGIN}/unexpected-path")


def test_security_configuration_fails_closed(web_security: WebSecurityConfig) -> None:
    with pytest.raises(ValueError, match="password hash"):
        WebSecurityConfig(
            admin_password_hash="invalid",
            session_secret=web_security.session_secret,
            allowed_origins=(TEST_ORIGIN,),
            cookie_secure=True,
        )

    with pytest.raises(ValueError, match="32 bytes"):
        WebSecurityConfig(
            admin_password_hash=web_security.admin_password_hash,
            session_secret="short",
            allowed_origins=(TEST_ORIGIN,),
            cookie_secure=True,
        )

    with pytest.raises(RuntimeError, match="NOEZEMA_WEB_ADMIN_PASSWORD_SCRYPT"):
        WebSecurityConfig.from_environment({})


def test_fixed_window_rate_limit_resets() -> None:
    now = [10.0]
    limiter = FixedWindowRateLimiter(monotonic=lambda: now[0])

    limiter.consume("login:local", limit=2, window_seconds=60)
    limiter.consume("login:local", limit=2, window_seconds=60)
    with pytest.raises(RateLimitExceeded) as rejected:
        limiter.consume("login:local", limit=2, window_seconds=60)
    assert rejected.value.retry_after_seconds == 60

    now[0] += 60
    limiter.consume("login:local", limit=2, window_seconds=60)
