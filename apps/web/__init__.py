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
from apps.web.streaming import (
    InvalidStreamPositionError,
    TimelineStreamService,
    encode_timeline_sse,
    parse_stream_position,
)

__all__ = [
    "AuthManager",
    "CommandService",
    "FixedWindowRateLimiter",
    "InvalidCursorError",
    "InvalidStreamPositionError",
    "ProjectionInvariantError",
    "QueryService",
    "TimelineStreamService",
    "WebSecurityConfig",
    "create_app",
    "create_environment_app",
    "encode_timeline_sse",
    "hash_admin_password",
    "parse_stream_position",
]
