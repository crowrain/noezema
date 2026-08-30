"""Owner-facing web boundary with a read-only Query API."""

from apps.web.app import create_app, create_environment_app
from apps.web.queries import InvalidCursorError, ProjectionInvariantError, QueryService

__all__ = [
    "InvalidCursorError",
    "ProjectionInvariantError",
    "QueryService",
    "create_app",
    "create_environment_app",
]
