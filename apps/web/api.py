"""Minimal Query/Command API (T1.17, §13).

M1 slice:
  - GET  /api/v1/status     — node state, config head, active session, counts;
  - GET  /api/v1/timeline   — audit events for a session (sequence order);
  - POST /api/v1/messages   — inbox message (created/queued);
  - POST /api/v1/commands   — closed operator commands with idempotency key.

M1 command semantics: pause/resume (node state), wake_now (run one session
through the attached orchestrator), stop_gracefully / abort_session (set the
active session's request flags; the orchestrator enforces the boundary
between steps). set_budget / set_access_profile / restore_checkpoint are
rejected until their milestones (M2/M7).

Visibility: M1 is single-operator local; the timeline returns all events.
Auth and visibility filtering land with the full web in M7.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.engine import DatabaseSettings
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot, ORMSystemConstant
from packages.domain.models.enums import (
    OperatorCommandState,
    OperatorCommandType,
    SessionState,
)
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.inbox import ORMMessage, ORMOperatorCommand
from packages.domain.models.questions import ORMQuestion
from packages.domain.models.sessions import ORMSession
from packages.domain.repositories.inbox import MessageRepository, OperatorCommandRepository
from packages.domain.repositories.sessions import SessionRepository
from packages.domain.services.config import ConfigError, ConfigService

NODE_STATE_KEY = "node_state"
NODE_STATES = ("idle", "paused", "session_running")


class MessageIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    body: str = Field(min_length=1, max_length=2000)
    priority: int = Field(default=0, ge=-100, le=100)
    sender: str = Field(default="owner", min_length=1, max_length=100)


class CommandIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: OperatorCommandType
    idempotency_key: str = Field(min_length=1, max_length=200)
    arguments: JsonDict = Field(default_factory=dict)
    actor_id: str = Field(default="operator", min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=500)


class _Node:
    """M1 node state: in-memory, persisted to system_constants across
    restarts (the host transition protocol lands in M2)."""

    def __init__(self) -> None:
        self.state: str = "idle"
        self.session_task: asyncio.Task[object] | None = None
        self.last_error: str | None = None
        self._resets: set[asyncio.Task[None]] = set()


def _now() -> datetime:
    return datetime.now(UTC)


async def _load_node_state(db: AsyncSession) -> str:
    stmt = select(ORMSystemConstant).where(ORMSystemConstant.key == NODE_STATE_KEY)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        return "idle"
    return row.value if row.value in NODE_STATES else "idle"


async def _save_node_state(db: AsyncSession, state: str) -> None:
    stmt = select(ORMSystemConstant).where(ORMSystemConstant.key == NODE_STATE_KEY)
    row = (await db.execute(stmt)).scalar_one_or_none()
    if row is None:
        db.add(ORMSystemConstant(key=NODE_STATE_KEY, value=state))
    else:
        row.value = state
    await db.flush()


def create_app(
    db_url: str | None = None,
    orchestrator: Orchestrator | None = None,
    engine: AsyncEngine | None = None,
    factory: async_sessionmaker[AsyncSession] | None = None,
) -> FastAPI:
    owns_engine = engine is None
    if engine is None:
        settings = DatabaseSettings()
        engine = create_async_engine(db_url or settings.database_url)
    if factory is None:
        factory = async_sessionmaker(engine, expire_on_commit=False)
    node = _Node()

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        async with factory() as db:
            node.state = await _load_node_state(db)
        yield
        if node.session_task is not None and not node.session_task.done():
            node.session_task.cancel()
        if owns_engine:
            await engine.dispose()

    app = FastAPI(title="NOEZEMA", version="0.1.0-m1", lifespan=lifespan)
    app.state.engine = engine
    app.state.factory = factory
    app.state.orchestrator = orchestrator
    app.state.node = node
    app.state.owns_engine = owns_engine

    # ── queries ───────────────────────────────────────────────────────────

    @app.get("/api/v1/status")
    async def status() -> JsonDict:
        async with factory() as db:
            try:
                snapshot: ORMConfigSnapshot = await ConfigService.get_effective(db)
            except ConfigError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            active = await SessionRepository.list_nonterminal(db)
            session = active[0] if active else None
            counts = {
                "questions": (await db.execute(select(func.count(ORMQuestion.id)))).scalar_one(),
                "sessions": (await db.execute(select(func.count(ORMSession.id)))).scalar_one(),
                "messages": (await db.execute(select(func.count(ORMMessage.id)))).scalar_one(),
            }
            return {
                "node_state": node.state,
                "last_error": node.last_error,
                "config": {
                    "snapshot_id": str(snapshot.id),
                    "sha256": snapshot.sha256,
                    "payload_sha256": snapshot.payload_sha256,
                },
                "session": (
                    {
                        "id": str(session.id),
                        "state": session.state,
                        "question_id": str(session.question_id) if session.question_id else None,
                    }
                    if session is not None
                    else None
                ),
                "counts": counts,
            }

    @app.get("/api/v1/timeline")
    async def timeline(
        session_id: uuid.UUID | None = None,
        limit: int = 50,
    ) -> JsonDict:
        limit = max(1, min(limit, 500))
        async with factory() as db:
            if session_id is None:
                row = (
                    await db.execute(select(ORMSession).order_by(ORMSession.created_at.desc()).limit(1))
                ).scalar_one_or_none()
                if row is None:
                    return {"session_id": None, "events": []}
                target: uuid.UUID | None = row.id
            else:
                target = session_id
            stmt = (
                select(ORMAuditEvent)
                .where(ORMAuditEvent.session_id == target)
                .order_by(ORMAuditEvent.sequence.asc())
                .limit(limit)
            )
            events = (await db.execute(stmt)).scalars().all()
            return {
                "session_id": str(target),
                "events": [
                    {
                        "sequence": e.sequence,
                        "type": e.type,
                        "occurred_at": e.occurred_at.isoformat(),
                        "actor": e.actor,
                        "public_summary": e.public_summary,
                        "payload": e.payload,
                        "visibility": e.visibility,
                    }
                    for e in events
                ],
            }

    # ── commands ──────────────────────────────────────────────────────────

    @app.post("/api/v1/messages", status_code=201)
    async def post_message(body: MessageIn) -> JsonDict:
        async with factory() as db, transaction(db):
            message = ORMMessage(
                sender=body.sender, body=body.body, priority=body.priority
            )
            await MessageRepository.create(db, message)
            return {"id": str(message.id), "state": message.state, "priority": message.priority}

    @app.post("/api/v1/commands", status_code=202)
    async def post_command(body: CommandIn) -> JsonDict:
        async with factory() as db, transaction(db):
            command = ORMOperatorCommand(
                actor_id=body.actor_id,
                type=body.type.value,
                arguments=body.arguments,
                state=OperatorCommandState.ACCEPTED.value,
                idempotency_key=body.idempotency_key,
                reason=body.reason,
            )
            command, created = await OperatorCommandRepository.create(db, command)
            if not created:
                return {
                    "id": str(command.id),
                    "type": command.type,
                    "state": command.state,
                    "replayed": True,
                    "result": command.result,
                }
            state, result = await _apply_command(db, command, body)
            command.state = state.value
            command.result = result
            if state in (OperatorCommandState.COMPLETED, OperatorCommandState.REJECTED):
                command.finished_at = _now()
            await db.flush()
            return {
                "id": str(command.id),
                "type": command.type,
                "state": command.state,
                "replayed": False,
                "result": result,
            }

    async def _apply_command(
        db: AsyncSession, command: ORMOperatorCommand, body: CommandIn
    ) -> tuple[OperatorCommandState, JsonDict]:
        ctype = OperatorCommandType(command.type)

        if ctype is OperatorCommandType.PAUSE:
            if node.state in ("paused",):
                return OperatorCommandState.COMPLETED, {"node_state": "paused", "noop": True}
            if node.state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "session is running; stop_gracefully instead"}
            node.state = "paused"
            await _save_node_state(db, node.state)
            return OperatorCommandState.COMPLETED, {"node_state": "paused"}

        if ctype is OperatorCommandType.RESUME:
            if node.state != "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is not paused"}
            node.state = "idle"
            await _save_node_state(db, node.state)
            return OperatorCommandState.COMPLETED, {"node_state": "idle"}

        if ctype is OperatorCommandType.WAKE_NOW:
            if node.state == "paused":
                return OperatorCommandState.REJECTED, {"reason": "node is paused"}
            if node.state == "session_running":
                return OperatorCommandState.REJECTED, {"reason": "a session is already running"}
            if orchestrator is None:
                return OperatorCommandState.REJECTED, {"reason": "orchestrator not attached"}
            node.state = "session_running"
            await _save_node_state(db, node.state)

            def _reset(task: asyncio.Task[object]) -> None:
                # runs on the event loop after the session task settles
                if not task.cancelled() and task.exception() is not None:
                    node.last_error = str(task.exception())[:500]
                reset_task = asyncio.ensure_future(_reset_node_state())
                node._resets.add(reset_task)
                reset_task.add_done_callback(node._resets.discard)

            node.session_task = asyncio.create_task(orchestrator.run_session())
            node.session_task.add_done_callback(_reset)
            return OperatorCommandState.COMPLETED, {"node_state": "session_running"}

        if ctype in (OperatorCommandType.STOP_GRACEFULLY, OperatorCommandType.ABORT_SESSION):
            active = await SessionRepository.list_nonterminal(db)
            if not active:
                return OperatorCommandState.REJECTED, {"reason": "no active session"}
            session = active[0]
            if ctype is OperatorCommandType.STOP_GRACEFULLY:
                session.stop_requested_at = _now()
            else:
                session.abort_requested_at = _now()
            await db.flush()
            return OperatorCommandState.COMPLETED, {"session_id": str(session.id)}

        # set_budget / set_access_profile / restore_checkpoint
        return OperatorCommandState.REJECTED, {
            "reason": "not available in M1 (config snapshot / checkpoints land later)"
        }

    async def _reset_node_state() -> None:
        async with factory() as db, transaction(db):
            node.state = "idle"
            await _save_node_state(db, node.state)

    return app


def build_standalone_app() -> FastAPI:
    """Entry helper: build the app with an orchestrator from env config."""
    from pathlib import Path

    from apps.orchestrator.executor import StubToolExecutor
    from packages.llm_gateway.client import LLMMiddleware
    from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile

    settings = DatabaseSettings()
    llm_config = LLMGatewayConfig()
    # app owns the single engine; the orchestrator shares its factory
    app = create_app(db_url=settings.database_url)
    factory = app.state.factory
    gateway = LLMMiddleware(llm_config)
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias=llm_config.model, backend_name="local"),
        executor=StubToolExecutor(Path("/var/lib/noezema/workspace")),
    )
    app.state.orchestrator = orchestrator
    return app


# keep SessionState referenced for API consumers typing session.state
_ = SessionState
