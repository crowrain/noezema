"""Atomic domain/audit/outbox operation tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import ConfigSnapshotId, EventType, SessionId, SessionState
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, create_session_with_audit
from packages.persistence.models import AuditEventRecord, OutboxEventRecord, SessionRecord

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _count(db: Session, record: type[object]) -> int:
    value = db.scalar(select(func.count()).select_from(record))
    assert value is not None
    return value


def test_create_session_stages_domain_audit_and_outbox_atomically(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = SessionId.new()

    with session_factory.begin() as db:
        created = create_session_with_audit(
            db,
            session_id=session_id,
            config_snapshot_id=ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID),
            occurred_at=NOW,
        )

    with session_factory() as db:
        session_record = db.get(SessionRecord, session_id.root)
        audit_record = db.get(AuditEventRecord, created.audit_event.id.root)
        outbox_record = db.get(OutboxEventRecord, created.outbox_event_id.root)

        assert session_record is not None
        assert session_record.state == SessionState.CREATED.value
        assert session_record.config_snapshot_id == BOOTSTRAP_CONFIG_SNAPSHOT_ID
        assert session_record.next_audit_sequence == 2

        assert audit_record is not None
        assert audit_record.session_id == session_id.root
        assert audit_record.sequence == 1
        assert audit_record.type == EventType.SESSION_STATE_CHANGED.value

        assert outbox_record is not None
        assert outbox_record.audit_event_id == audit_record.id
        assert outbox_record.topic == "audit.session_state_changed.v1"
        assert outbox_record.payload["event"]["session_id"] == str(session_id)
        assert outbox_record.published_at is None
        assert outbox_record.attempts == 0


def test_caller_rollback_removes_all_three_records(
    session_factory: sessionmaker[Session],
) -> None:
    class SimulatedFailure(RuntimeError):
        pass

    with pytest.raises(SimulatedFailure), session_factory.begin() as db:
        create_session_with_audit(
            db,
            session_id=SessionId.new(),
            config_snapshot_id=ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID),
            occurred_at=NOW,
        )
        raise SimulatedFailure

    with session_factory() as db:
        assert _count(db, SessionRecord) == 0
        assert _count(db, AuditEventRecord) == 0
        assert _count(db, OutboxEventRecord) == 0
