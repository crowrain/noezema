"""Transactional persistence operations shared by orchestrator use cases."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.domain import (
    AuditEvent,
    ConfigSnapshotId,
    EventType,
    OutboxEventId,
    QuestionId,
    SessionId,
    SessionState,
)
from packages.domain._base import JsonObject
from packages.persistence.models import AuditEventRecord, OutboxEventRecord, SessionRecord


@dataclass(frozen=True, slots=True)
class CreatedSession:
    """Identifiers emitted by the atomic session creation operation."""

    session_id: SessionId
    audit_event: AuditEvent
    outbox_event_id: OutboxEventId


@dataclass(frozen=True, slots=True)
class AppendedAudit:
    """Audit and outbox identities appended under the session row lock."""

    audit_event: AuditEvent
    outbox_event_id: OutboxEventId


def create_session_with_audit(
    db: Session,
    *,
    session_id: SessionId,
    config_snapshot_id: ConfigSnapshotId,
    question_id: QuestionId | None = None,
    occurred_at: datetime | None = None,
) -> CreatedSession:
    """Stage session state, its audit event and outbox event in one transaction.

    The caller owns the transaction boundary. This function deliberately flushes
    but never commits, so a later failure rolls back all three records together.
    """

    timestamp = occurred_at or datetime.now(UTC)
    event_payload: JsonObject = {"from": None, "to": SessionState.CREATED.value}
    if question_id is not None:
        event_payload["question_id"] = str(question_id)
    event = AuditEvent.new(
        session_id=session_id,
        sequence=1,
        type=EventType.SESSION_STATE_CHANGED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Session created",
        payload=event_payload,
    )
    outbox_event_id = OutboxEventId.new()

    db.add(
        SessionRecord(
            id=session_id.root,
            state=SessionState.CREATED.value,
            config_snapshot_id=config_snapshot_id.root,
            question_id=question_id.root if question_id is not None else None,
            lease_owner=None,
            lease_expires_at=None,
            fence=0,
            next_audit_sequence=2,
            created_at=timestamp,
            updated_at=timestamp,
            terminal_at=None,
        )
    )
    # Explicit flush boundaries make FK ordering independent from optional ORM
    # relationships while all writes still share the caller-owned transaction.
    db.flush()
    db.add(
        AuditEventRecord(
            id=event.id.root,
            session_id=session_id.root,
            sequence=event.sequence,
            type=event.type.value,
            schema_version=event.schema_version,
            occurred_at=event.occurred_at,
            actor=event.actor,
            public_summary=event.public_summary,
            payload=event.payload,
            visibility="public",
        )
    )
    db.flush()
    db.add(
        OutboxEventRecord(
            id=outbox_event_id.root,
            audit_event_id=event.id.root,
            topic="audit.session_state_changed.v1",
            payload={"event": event.model_dump(mode="json")},
            created_at=timestamp,
            published_at=None,
            attempts=0,
        )
    )
    db.flush()

    return CreatedSession(
        session_id=session_id,
        audit_event=event,
        outbox_event_id=outbox_event_id,
    )


def append_session_audit(
    db: Session,
    *,
    session_id: SessionId,
    type: EventType,
    occurred_at: datetime,
    actor: str,
    public_summary: str,
    topic: str,
    payload: JsonObject | None = None,
) -> AppendedAudit:
    """Append one ordered audit/outbox pair while holding the session lock."""

    session_record = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if session_record is None:
        raise LookupError(f"session does not exist: {session_id}")

    event = AuditEvent.new(
        session_id=session_id,
        sequence=session_record.next_audit_sequence,
        type=type,
        occurred_at=occurred_at,
        actor=actor,
        public_summary=public_summary,
        payload=payload,
    )
    outbox_event_id = OutboxEventId.new()
    session_record.next_audit_sequence += 1
    session_record.updated_at = occurred_at

    db.add(
        AuditEventRecord(
            id=event.id.root,
            session_id=session_id.root,
            sequence=event.sequence,
            type=event.type.value,
            schema_version=event.schema_version,
            occurred_at=event.occurred_at,
            actor=event.actor,
            public_summary=event.public_summary,
            payload=event.payload,
            visibility="public",
        )
    )
    db.flush()
    db.add(
        OutboxEventRecord(
            id=outbox_event_id.root,
            audit_event_id=event.id.root,
            topic=topic,
            payload={"event": event.model_dump(mode="json")},
            created_at=occurred_at,
            published_at=None,
            attempts=0,
        )
    )
    db.flush()

    return AppendedAudit(audit_event=event, outbox_event_id=outbox_event_id)
