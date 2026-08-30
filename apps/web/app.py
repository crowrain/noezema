"""FastAPI application factory for the read-only owner Query API."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query
from sqlalchemy.orm import Session

from apps.web.models import (
    MessagePage,
    NodeStatusProjection,
    OperatorCommandPage,
    SessionPage,
    SessionProjection,
    TimelinePage,
)
from apps.web.queries import InvalidCursorError, ProjectionInvariantError, QueryService
from packages.persistence import create_session_factory

PageLimit = Annotated[int, Query(ge=1, le=100)]
PageCursor = Annotated[str | None, Query(max_length=1024)]


def create_app(
    session_factory: Callable[[], Session],
    *,
    clock: Callable[[], datetime] | None = None,
) -> FastAPI:
    """Create an API whose routes only execute allow-listed SELECT projections."""

    service = QueryService(session_factory, clock=clock)
    app = FastAPI(title="NOEZEMA Query API", version="1", docs_url="/api/docs")

    @app.get("/api/status", response_model=NodeStatusProjection)
    async def get_status() -> NodeStatusProjection:
        return _project(service.status)

    @app.get("/api/timeline", response_model=TimelinePage)
    async def get_timeline(
        limit: PageLimit = 50,
        cursor: PageCursor = None,
        session_id: UUID | None = None,
    ) -> TimelinePage:
        return _project(
            service.timeline,
            limit=limit,
            cursor=cursor,
            session_id=session_id,
        )

    @app.get("/api/sessions", response_model=SessionPage)
    async def get_sessions(
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> SessionPage:
        return _project(service.sessions, limit=limit, cursor=cursor)

    @app.get("/api/sessions/{session_id}", response_model=SessionProjection)
    async def get_session(session_id: UUID) -> SessionProjection:
        projection = _project(service.session, session_id)
        if projection is None:
            raise HTTPException(status_code=404, detail="session not found")
        return projection

    @app.get("/api/messages", response_model=MessagePage)
    async def get_messages(
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> MessagePage:
        return _project(service.messages, limit=limit, cursor=cursor)

    @app.get("/api/operator-commands", response_model=OperatorCommandPage)
    async def get_operator_commands(
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> OperatorCommandPage:
        return _project(service.operator_commands, limit=limit, cursor=cursor)

    return app


def create_environment_app() -> FastAPI:
    """Uvicorn factory using the required operational database URL."""

    database_url = os.environ.get("NOEZEMA_DATABASE_URL")
    if not database_url:
        raise RuntimeError("NOEZEMA_DATABASE_URL is required")
    _, session_factory = create_session_factory(database_url)
    return create_app(session_factory)


def _project(operation: Callable[..., object], *args: object, **kwargs: object):
    try:
        return operation(*args, **kwargs)
    except InvalidCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectionInvariantError as exc:
        raise HTTPException(status_code=503, detail="read model is inconsistent") from exc
