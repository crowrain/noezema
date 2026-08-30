"""Security fixtures shared by owner API tests."""

from __future__ import annotations

import pytest

from apps.web import WebSecurityConfig, hash_admin_password

TEST_PASSWORD = "correct horse battery staple"
TEST_ORIGIN = "https://testserver"


@pytest.fixture(scope="session")
def web_security() -> WebSecurityConfig:
    return WebSecurityConfig(
        admin_password_hash=hash_admin_password(
            TEST_PASSWORD,
            salt=b"noezema-test-salt-000001",
        ),
        session_secret="test-session-secret-" * 4,
        allowed_origins=(TEST_ORIGIN,),
        cookie_secure=True,
        session_ttl_seconds=60 * 60,
    )
