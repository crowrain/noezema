"""Read-only database projections for the owner web interface."""

from __future__ import annotations

import base64
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import Select, func, or_, select
from sqlalchemy.orm import Session

from apps.web.models import (
    MessagePage,
    MessageProjection,
    NodeActivity,
    NodeStatusProjection,
    OperatorCommandPage,
    OperatorCommandProjection,
    SessionPage,
    SessionProjection,
    SessionUsageProjection,
    TimelineEventProjection,
    TimelinePage,
)
from packages.domain import (
    EventType,
    MessageState,
    NodeState,
    OperatorCommandState,
    OperatorCommandType,
    QuestionOrigin,
    QuestionState,
    SessionBudget,
    SessionState,
    canonical_json_sha256,
)
from packages.persistence.models import (
    ActionRecord,
    AuditEventRecord,
    MessageRecord,
    ModelRunRecord,
    OperatorCommandRecord,
    QuestionRecord,
    RuntimeControlRecord,
    SessionRecord,
)

_TERMINAL_SESSION_STATES = tuple(state.value for state in SessionState if state.is_terminal)
_PENDING_MESSAGE_STATES = (
    MessageState.CREATED.value,
    MessageState.QUEUED.value,
    MessageState.DELIVERED.value,
    MessageState.ACKNOWLEDGED.value,
)
_PENDING_COMMAND_STATES = tuple(
    state.value for state in OperatorCommandState if not state.is_terminal
)


class InvalidCursorError(ValueError):
    """The opaque pagination cursor is malformed or belongs to another resource."""


class ProjectionInvariantError(RuntimeError):
    """Durable records cannot be represented by the trusted public contract."""


class QueryService:
    """Build allow-listed projections without mutating operational state."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def status(self) -> NodeStatusProjection:
        observed_at = _aware(self._clock())
        with self._session_factory() as db:
            runtime = db.get(RuntimeControlRecord, "global")
            if runtime is None:
                raise ProjectionInvariantError("global runtime control is missing")
            active_rows = db.execute(
                self._session_statement()
                .where(SessionRecord.state.not_in(_TERMINAL_SESSION_STATES))
                .order_by(SessionRecord.created_at.desc(), SessionRecord.id.desc())
                .limit(2)
            ).all()
            if len(active_rows) > 1:
                raise ProjectionInvariantError("multiple active sessions violate the MVP invariant")
            active_session = self._session_projection(active_rows[0]) if active_rows else None
            queued_questions = _count_where(
                db, QuestionRecord, QuestionRecord.state == QuestionState.QUEUED.value
            )
            pending_messages = _count_where(
                db, MessageRecord, MessageRecord.state.in_(_PENDING_MESSAGE_STATES)
            )
            pending_commands = _count_where(
                db,
                OperatorCommandRecord,
                OperatorCommandRecord.state.in_(_PENDING_COMMAND_STATES),
            )
            try:
                node_state = NodeState(runtime.node_state)
            except ValueError as exc:
                raise ProjectionInvariantError("runtime node_state is invalid") from exc
            return NodeStatusProjection(
                node_state=node_state,
                activity=(
                    NodeActivity.RUNNING if active_session is not None else NodeActivity.IDLE
                ),
                active_session=active_session,
                queued_questions=queued_questions,
                pending_messages=pending_messages,
                pending_commands=pending_commands,
                observed_at=observed_at,
            )

    def timeline(
        self,
        *,
        limit: int,
        cursor: str | None = None,
        session_id: UUID | None = None,
    ) -> TimelinePage:
        resource = f"timeline:{session_id}" if session_id is not None else "timeline"
        position = _decode_cursor(cursor, expected_resource=resource)
        statement = select(AuditEventRecord).where(AuditEventRecord.visibility == "public")
        if session_id is not None:
            statement = statement.where(AuditEventRecord.session_id == session_id)
        statement = _page_statement(
            statement,
            timestamp_column=AuditEventRecord.occurred_at,
            id_column=AuditEventRecord.id,
            position=position,
            limit=limit,
        )
        with self._session_factory() as db:
            records = list(db.scalars(statement))
        page_records, next_cursor = _finish_page(
            records,
            limit=limit,
            resource=resource,
            timestamp=lambda record: record.occurred_at,
            identity=lambda record: record.id,
        )
        try:
            items = tuple(
                TimelineEventProjection(
                    id=record.id,
                    session_id=record.session_id,
                    sequence=record.sequence,
                    type=EventType(record.type),
                    schema_version=record.schema_version,
                    occurred_at=_aware(record.occurred_at),
                    actor=record.actor,
                    summary=record.public_summary,
                )
                for record in page_records
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionInvariantError("public audit event is invalid") from exc
        return TimelinePage(items=items, next_cursor=next_cursor)

    def sessions(self, *, limit: int, cursor: str | None = None) -> SessionPage:
        position = _decode_cursor(cursor, expected_resource="sessions")
        statement = _page_statement(
            self._session_statement(),
            timestamp_column=SessionRecord.created_at,
            id_column=SessionRecord.id,
            position=position,
            limit=limit,
        )
        with self._session_factory() as db:
            rows = list(db.execute(statement).all())
        page_rows, next_cursor = _finish_page(
            rows,
            limit=limit,
            resource="sessions",
            timestamp=lambda row: row[0].created_at,
            identity=lambda row: row[0].id,
        )
        return SessionPage(
            items=tuple(self._session_projection(row) for row in page_rows),
            next_cursor=next_cursor,
        )

    def session(self, session_id: UUID) -> SessionProjection | None:
        with self._session_factory() as db:
            row = db.execute(
                self._session_statement().where(SessionRecord.id == session_id)
            ).one_or_none()
        return self._session_projection(row) if row is not None else None

    def messages(self, *, limit: int, cursor: str | None = None) -> MessagePage:
        position = _decode_cursor(cursor, expected_resource="messages")
        statement = _page_statement(
            select(MessageRecord),
            timestamp_column=MessageRecord.created_at,
            id_column=MessageRecord.id,
            position=position,
            limit=limit,
        )
        with self._session_factory() as db:
            records = list(db.scalars(statement))
        page_records, next_cursor = _finish_page(
            records,
            limit=limit,
            resource="messages",
            timestamp=lambda record: record.created_at,
            identity=lambda record: record.id,
        )
        try:
            items = tuple(
                MessageProjection(
                    id=record.id,
                    question_id=record.question_id,
                    sender=record.sender,
                    body=record.body,
                    priority=record.priority,
                    state=MessageState(record.state),
                    expires_at=_aware(record.expires_at),
                    created_at=_aware(record.created_at),
                    delivered_session_id=record.delivered_session_id,
                    delivered_at=_optional_aware(record.delivered_at),
                    acknowledged_at=_optional_aware(record.acknowledged_at),
                    answered_at=_optional_aware(record.answered_at),
                    response_audit_event_id=record.response_audit_event_id,
                )
                for record in page_records
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionInvariantError("message record is invalid") from exc
        return MessagePage(items=items, next_cursor=next_cursor)

    def operator_commands(self, *, limit: int, cursor: str | None = None) -> OperatorCommandPage:
        position = _decode_cursor(cursor, expected_resource="operator_commands")
        statement = _page_statement(
            select(OperatorCommandRecord),
            timestamp_column=OperatorCommandRecord.created_at,
            id_column=OperatorCommandRecord.id,
            position=position,
            limit=limit,
        )
        with self._session_factory() as db:
            records = list(db.scalars(statement))
        page_records, next_cursor = _finish_page(
            records,
            limit=limit,
            resource="operator_commands",
            timestamp=lambda record: record.created_at,
            identity=lambda record: record.id,
        )
        try:
            items = tuple(
                OperatorCommandProjection(
                    id=record.id,
                    actor_id=record.actor_id,
                    type=OperatorCommandType(record.type),
                    session_id=record.session_id,
                    state=OperatorCommandState(record.state),
                    reason=record.reason,
                    error_code=record.error_code,
                    created_at=_aware(record.created_at),
                    updated_at=_aware(record.updated_at),
                    finished_at=_optional_aware(record.finished_at),
                )
                for record in page_records
            )
        except (TypeError, ValueError) as exc:
            raise ProjectionInvariantError("operator command record is invalid") from exc
        return OperatorCommandPage(items=items, next_cursor=next_cursor)

    @staticmethod
    def _session_statement() -> Select[Any]:
        model_usage = (
            select(
                ModelRunRecord.session_id.label("session_id"),
                func.count(ModelRunRecord.id).label("model_turns"),
                func.coalesce(func.sum(ModelRunRecord.input_tokens), 0).label("input_tokens"),
                func.coalesce(func.sum(ModelRunRecord.output_tokens), 0).label("output_tokens"),
            )
            .group_by(ModelRunRecord.session_id)
            .subquery()
        )
        action_usage = (
            select(
                ActionRecord.session_id.label("session_id"),
                func.count(ActionRecord.id).label("tool_actions"),
            )
            .group_by(ActionRecord.session_id)
            .subquery()
        )
        return (
            select(
                SessionRecord,
                QuestionRecord,
                func.coalesce(model_usage.c.model_turns, 0),
                func.coalesce(action_usage.c.tool_actions, 0),
                func.coalesce(model_usage.c.input_tokens, 0),
                func.coalesce(model_usage.c.output_tokens, 0),
            )
            .outerjoin(QuestionRecord, QuestionRecord.id == SessionRecord.question_id)
            .outerjoin(model_usage, model_usage.c.session_id == SessionRecord.id)
            .outerjoin(action_usage, action_usage.c.session_id == SessionRecord.id)
        )

    @staticmethod
    def _session_projection(row: Any) -> SessionProjection:
        record, question, model_turns, tool_actions, input_tokens, output_tokens = row
        if record.question_id is not None and question is None:
            raise ProjectionInvariantError("session references a missing question")
        try:
            budget = SessionBudget.model_validate(record.budget)
            if canonical_json_sha256(record.budget) != record.budget_sha256:
                raise ProjectionInvariantError("session budget hash does not match")
            return SessionProjection(
                id=record.id,
                state=SessionState(record.state),
                config_snapshot_id=record.config_snapshot_id,
                question_id=record.question_id,
                question_text=question.text if question is not None else None,
                question_origin=(QuestionOrigin(question.origin) if question is not None else None),
                budget=budget,
                usage=SessionUsageProjection(
                    model_turns=int(model_turns),
                    tool_actions=int(tool_actions),
                    input_tokens=int(input_tokens),
                    output_tokens=int(output_tokens),
                ),
                cognitive_deadline_at=_aware(record.cognitive_deadline_at),
                host_deadline_at=_aware(record.host_deadline_at),
                stop_requested_at=_optional_aware(record.stop_requested_at),
                abort_requested_at=_optional_aware(record.abort_requested_at),
                termination_reason=record.termination_reason,
                created_at=_aware(record.created_at),
                updated_at=_aware(record.updated_at),
                terminal_at=_optional_aware(record.terminal_at),
            )
        except ProjectionInvariantError:
            raise
        except (TypeError, ValueError) as exc:
            raise ProjectionInvariantError("session record is invalid") from exc


def _page_statement(
    statement: Select[Any],
    *,
    timestamp_column: Any,
    id_column: Any,
    position: tuple[datetime, UUID] | None,
    limit: int,
) -> Select[Any]:
    if not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")
    if position is not None:
        timestamp, identity = position
        statement = statement.where(
            or_(
                timestamp_column < timestamp,
                (timestamp_column == timestamp) & (id_column < identity),
            )
        )
    return statement.order_by(timestamp_column.desc(), id_column.desc()).limit(limit + 1)


def _finish_page(
    records: list[Any],
    *,
    limit: int,
    resource: str,
    timestamp: Callable[[Any], datetime],
    identity: Callable[[Any], UUID],
) -> tuple[list[Any], str | None]:
    has_more = len(records) > limit
    page = records[:limit]
    next_cursor = None
    if has_more and page:
        last = page[-1]
        next_cursor = _encode_cursor(resource, timestamp(last), identity(last))
    return page, next_cursor


def _encode_cursor(resource: str, timestamp: datetime, identity: UUID) -> str:
    payload = {
        "v": 1,
        "resource": resource,
        "timestamp": _aware(timestamp).astimezone(UTC).isoformat().replace("+00:00", "Z"),
        "id": str(identity),
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    )
    return encoded.rstrip(b"=").decode("ascii")


def _decode_cursor(cursor: str | None, *, expected_resource: str) -> tuple[datetime, UUID] | None:
    if cursor is None:
        return None
    if not cursor or len(cursor) > 1024:
        raise InvalidCursorError("invalid pagination cursor")
    try:
        padding = "=" * (-len(cursor) % 4)
        raw = base64.b64decode(cursor + padding, altchars=b"-_", validate=True)
        payload = json.loads(raw)
        if not isinstance(payload, dict) or set(payload) != {
            "v",
            "resource",
            "timestamp",
            "id",
        }:
            raise ValueError
        if payload["v"] != 1 or payload["resource"] != expected_resource:
            raise ValueError
        if not isinstance(payload["timestamp"], str) or not isinstance(payload["id"], str):
            raise ValueError
        timestamp = datetime.fromisoformat(payload["timestamp"].replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            raise ValueError
        identity = UUID(payload["id"])
    except (ValueError, TypeError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise InvalidCursorError("invalid pagination cursor") from exc
    return timestamp, identity


def _count_where(db: Session, record_type: Any, predicate: Any) -> int:
    return int(db.scalar(select(func.count()).select_from(record_type).where(predicate)) or 0)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value


def _optional_aware(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None
