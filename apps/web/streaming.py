"""Replayable public timeline projection backed by the committed outbox."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.web.models import TimelineStreamEventProjection
from apps.web.queries import ProjectionInvariantError
from packages.domain import EventType
from packages.persistence import sequence_pending_outbox
from packages.persistence.models import AuditEventRecord, OutboxEventRecord, RuntimeControlRecord

MAX_STREAM_SEQUENCE = 2**63 - 1


class InvalidStreamPositionError(ValueError):
    """A reconnect cursor is malformed, conflicting or outside durable history."""


class TimelineStreamService:
    """Sequence committed outbox rows and expose only allow-listed audit fields."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def current_position(self) -> int:
        """Return the greatest assigned position without publishing pending rows."""

        with self._session_factory() as db:
            runtime = db.get(RuntimeControlRecord, "global")
            if runtime is None or not 1 <= runtime.next_outbox_sequence <= MAX_STREAM_SEQUENCE + 1:
                raise ProjectionInvariantError("global outbox stream position is invalid")
            return runtime.next_outbox_sequence - 1

    def poll(
        self,
        *,
        after_sequence: int,
        limit: int = 100,
    ) -> tuple[TimelineStreamEventProjection, ...]:
        """Publish one pending batch and read public projections after a position."""

        if not 0 <= after_sequence <= MAX_STREAM_SEQUENCE:
            raise InvalidStreamPositionError("invalid timeline stream position")
        if not 1 <= limit <= 1000:
            raise ValueError("timeline stream limit must be between 1 and 1000")
        published_at = _clock_time(self._clock())
        with self._session_factory.begin() as db:
            sequence_pending_outbox(db, occurred_at=published_at, limit=limit)
            rows = db.execute(
                select(
                    OutboxEventRecord.stream_sequence,
                    AuditEventRecord.id,
                    AuditEventRecord.session_id,
                    AuditEventRecord.sequence,
                    AuditEventRecord.type,
                    AuditEventRecord.schema_version,
                    AuditEventRecord.occurred_at,
                    AuditEventRecord.actor,
                    AuditEventRecord.public_summary,
                )
                .join(
                    AuditEventRecord,
                    AuditEventRecord.id == OutboxEventRecord.audit_event_id,
                )
                .where(
                    OutboxEventRecord.stream_sequence > after_sequence,
                    AuditEventRecord.visibility == "public",
                )
                .order_by(OutboxEventRecord.stream_sequence)
                .limit(limit)
            ).all()
            try:
                projections = tuple(
                    TimelineStreamEventProjection(
                        stream_sequence=stream_sequence,
                        id=event_id,
                        session_id=session_id,
                        sequence=event_sequence,
                        type=EventType(event_type),
                        schema_version=schema_version,
                        occurred_at=_database_time(occurred_at),
                        actor=actor,
                        summary=public_summary,
                    )
                    for (
                        stream_sequence,
                        event_id,
                        session_id,
                        event_sequence,
                        event_type,
                        schema_version,
                        occurred_at,
                        actor,
                        public_summary,
                    ) in rows
                )
            except (TypeError, ValueError) as exc:
                raise ProjectionInvariantError("public outbox event is invalid") from exc
        return projections


def parse_stream_position(last_event_id: str | None, after: int | None) -> int:
    """Resolve browser reconnect state and reject ambiguous cursor sources."""

    header_position = None
    if last_event_id is not None:
        if not last_event_id or not last_event_id.isascii() or not last_event_id.isdecimal():
            raise InvalidStreamPositionError("invalid Last-Event-ID")
        header_position = int(last_event_id)
        if header_position > MAX_STREAM_SEQUENCE:
            raise InvalidStreamPositionError("invalid Last-Event-ID")
    if after is not None and not 0 <= after <= MAX_STREAM_SEQUENCE:
        raise InvalidStreamPositionError("invalid timeline stream position")
    if header_position is not None and after is not None and header_position != after:
        raise InvalidStreamPositionError("conflicting timeline stream positions")
    return header_position if header_position is not None else (after or 0)


def encode_timeline_sse(event: TimelineStreamEventProjection) -> str:
    """Encode one newline-safe typed timeline event for EventSource clients."""

    return f"id: {event.stream_sequence}\nevent: timeline\ndata: {event.model_dump_json()}\n\n"


def _clock_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timeline stream clock must return a timezone-aware datetime")
    return value


def _database_time(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
