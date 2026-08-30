"""FastAPI application factory for the authenticated owner API."""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.web.commands import CommandService
from apps.web.models import (
    AuthSessionResponse,
    LoginRequest,
    MessageAcceptedResponse,
    MessageCreateRequest,
    MessagePage,
    NodeStatusProjection,
    OperatorCommandAcceptedResponse,
    OperatorCommandCreateRequest,
    OperatorCommandPage,
    SessionPage,
    SessionProjection,
    TimelinePage,
)
from apps.web.queries import InvalidCursorError, ProjectionInvariantError, QueryService
from apps.web.security import (
    AuthenticatedPrincipal,
    AuthenticationError,
    AuthManager,
    CsrfValidationError,
    FixedWindowRateLimiter,
    OriginValidationError,
    RateLimitExceeded,
    WebSecurityConfig,
)
from packages.persistence import (
    InboxBindingConflictError,
    InboxStateConflictError,
    create_session_factory,
)

PageLimit = Annotated[int, Query(ge=1, le=100)]
PageCursor = Annotated[str | None, Query(max_length=1024)]


def create_app(
    session_factory: Callable[[], Session],
    *,
    security: WebSecurityConfig,
    clock: Callable[[], datetime] | None = None,
    rate_limiter: FixedWindowRateLimiter | None = None,
) -> FastAPI:
    """Create a fail-closed owner API with separated Query and Command services."""

    queries = QueryService(session_factory, clock=clock)
    commands = CommandService(session_factory, clock=clock)
    auth = AuthManager(security, clock=clock)
    limiter = rate_limiter or FixedWindowRateLimiter()
    app = FastAPI(title="NOEZEMA Owner API", version="1", docs_url="/api/docs")

    @app.middleware("http")
    async def prevent_api_caching(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    def require_principal(request: Request) -> AuthenticatedPrincipal:
        try:
            return auth.authenticate(request.cookies.get(security.cookie_name))
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="authentication required",
            ) from exc

    def require_same_origin(request: Request) -> None:
        try:
            auth.verify_origin(request.headers.get("origin"))
        except OriginValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="request origin is not allowed",
            ) from exc

    def require_mutation(request: Request) -> AuthenticatedPrincipal:
        require_same_origin(request)
        principal = require_principal(request)
        try:
            auth.verify_csrf(principal, request.headers.get("x-csrf-token"))
        except CsrfValidationError as exc:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="invalid CSRF token",
            ) from exc
        try:
            limiter.consume(
                f"command:{principal.subject}:{_client_identity(request)}",
                limit=security.command_request_limit,
                window_seconds=security.rate_window_seconds,
            )
        except RateLimitExceeded as exc:
            raise _rate_limit_error(exc) from exc
        return principal

    @app.post("/api/auth/login", response_model=AuthSessionResponse)
    async def login(
        request: Request,
        response: Response,
        credentials: LoginRequest,
    ) -> AuthSessionResponse:
        require_same_origin(request)
        try:
            limiter.consume(
                f"login:{_client_identity(request)}",
                limit=security.login_attempt_limit,
                window_seconds=security.rate_window_seconds,
            )
        except RateLimitExceeded as exc:
            raise _rate_limit_error(exc) from exc
        try:
            issued = auth.login(credentials.password.get_secret_value())
        except AuthenticationError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid credentials",
            ) from exc
        response.set_cookie(
            key=security.cookie_name,
            value=issued.token,
            max_age=security.session_ttl_seconds,
            expires=issued.principal.expires_at,
            path="/",
            secure=security.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return AuthSessionResponse(
            subject=issued.principal.subject,
            csrf_token=issued.csrf_token,
            issued_at=issued.principal.issued_at,
            expires_at=issued.principal.expires_at,
        )

    @app.get("/api/auth/session", response_model=AuthSessionResponse)
    async def get_auth_session(request: Request) -> AuthSessionResponse:
        principal = require_principal(request)
        return AuthSessionResponse(
            subject=principal.subject,
            csrf_token=auth.csrf_token(principal),
            issued_at=principal.issued_at,
            expires_at=principal.expires_at,
        )

    @app.post("/api/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
    async def logout(request: Request) -> Response:
        require_mutation(request)
        response = Response(status_code=status.HTTP_204_NO_CONTENT)
        response.delete_cookie(
            key=security.cookie_name,
            path="/",
            secure=security.cookie_secure,
            httponly=True,
            samesite="strict",
        )
        return response

    @app.get("/api/status", response_model=NodeStatusProjection)
    async def get_status(request: Request) -> NodeStatusProjection:
        require_principal(request)
        return _project(queries.status)

    @app.get("/api/timeline", response_model=TimelinePage)
    async def get_timeline(
        request: Request,
        limit: PageLimit = 50,
        cursor: PageCursor = None,
        session_id: UUID | None = None,
    ) -> TimelinePage:
        require_principal(request)
        return _project(
            queries.timeline,
            limit=limit,
            cursor=cursor,
            session_id=session_id,
        )

    @app.get("/api/sessions", response_model=SessionPage)
    async def get_sessions(
        request: Request,
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> SessionPage:
        require_principal(request)
        return _project(queries.sessions, limit=limit, cursor=cursor)

    @app.get("/api/sessions/{session_id}", response_model=SessionProjection)
    async def get_session(request: Request, session_id: UUID) -> SessionProjection:
        require_principal(request)
        projection = _project(queries.session, session_id)
        if projection is None:
            raise HTTPException(status_code=404, detail="session not found")
        return projection

    @app.get("/api/messages", response_model=MessagePage)
    async def get_messages(
        request: Request,
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> MessagePage:
        require_principal(request)
        return _project(queries.messages, limit=limit, cursor=cursor)

    @app.post(
        "/api/messages",
        response_model=MessageAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_message(
        request: Request,
        message: MessageCreateRequest,
    ) -> MessageAcceptedResponse:
        principal = require_mutation(request)
        return _command(commands.submit_message, message, actor_id=principal.subject)

    @app.get("/api/operator-commands", response_model=OperatorCommandPage)
    async def get_operator_commands(
        request: Request,
        limit: PageLimit = 50,
        cursor: PageCursor = None,
    ) -> OperatorCommandPage:
        require_principal(request)
        return _project(queries.operator_commands, limit=limit, cursor=cursor)

    @app.post(
        "/api/operator-commands",
        response_model=OperatorCommandAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def post_operator_command(
        request: Request,
        command: OperatorCommandCreateRequest,
    ) -> OperatorCommandAcceptedResponse:
        principal = require_mutation(request)
        return _command(
            commands.submit_operator_command,
            command,
            actor_id=principal.subject,
        )

    return app


def create_environment_app() -> FastAPI:
    """Uvicorn factory using explicit database and local-admin security settings."""

    database_url = os.environ.get("NOEZEMA_DATABASE_URL")
    if not database_url:
        raise RuntimeError("NOEZEMA_DATABASE_URL is required")
    _, session_factory = create_session_factory(database_url)
    return create_app(
        session_factory,
        security=WebSecurityConfig.from_environment(),
    )


def _project(operation: Callable[..., Any], *args: object, **kwargs: object):
    try:
        return operation(*args, **kwargs)
    except InvalidCursorError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except ProjectionInvariantError as exc:
        raise HTTPException(status_code=503, detail="read model is inconsistent") from exc


def _command(operation: Callable[..., Any], *args: object, **kwargs: object):
    try:
        return operation(*args, **kwargs)
    except (InboxBindingConflictError, InboxStateConflictError, IntegrityError) as exc:
        raise HTTPException(
            status_code=409, detail="inbox request conflicts with durable state"
        ) from exc
    except LookupError as exc:
        raise HTTPException(status_code=503, detail="operational state is unavailable") from exc


def _client_identity(request: Request) -> str:
    return request.client.host if request.client is not None else "unknown"


def _rate_limit_error(error: RateLimitExceeded) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="rate limit exceeded",
        headers={"Retry-After": str(error.retry_after_seconds)},
    )
