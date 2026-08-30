"""Authenticated owner-facing Query and Command API boundary."""

from apps.web.app import create_app, create_environment_app
from apps.web.commands import CommandService
from apps.web.queries import InvalidCursorError, ProjectionInvariantError, QueryService
from apps.web.security import (
    AuthManager,
    FixedWindowRateLimiter,
    WebSecurityConfig,
    hash_admin_password,
)

__all__ = [
    "AuthManager",
    "CommandService",
    "FixedWindowRateLimiter",
    "InvalidCursorError",
    "ProjectionInvariantError",
    "QueryService",
    "WebSecurityConfig",
    "create_app",
    "create_environment_app",
    "hash_admin_password",
]
