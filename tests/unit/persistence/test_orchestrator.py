"""MVP wake admission and atomic FIFO binding tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import (
    InconsistentRuntimeStateError,
    SessionStarted,
    WakeSkipped,
    WakeSkipReason,
    start_next_session,
)
from packages.cognition import enqueue_question
from packages.domain import ConfigSnapshotId, EventType, QuestionDraft, SessionId
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID
from packages.persistence.models import (
    AuditEventRecord,
    OutboxEventRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)

CONFIG_ID = ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID)
NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def _enqueue_seed(db: Session, text: str = "Исследовать NOEZEMA") -> QuestionDraft:
    draft = QuestionDraft.seeded(
        text=text,
        origin_config_snapshot_id=CONFIG_ID,
        created_at=NOW,
    )
    enqueue_question(db, draft)
    return draft


def _count(db: Session, record: type[object]) -> int:
    count = db.scalar(select(func.count()).select_from(record))
    assert count is not None
    return count


def test_start_binds_fifo_question_config_and_two_ordered_audit_events(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        draft = _enqueue_seed(db)
        result = start_next_session(db, session_id=session_id, occurred_at=NOW)

        assert isinstance(result, SessionStarted)
        assert result.question_id == draft.id
        assert result.config_snapshot_id == CONFIG_ID

    with session_factory() as db:
        session_record = db.get(SessionRecord, session_id.root)
        assert session_record is not None
        assert session_record.question_id == draft.id.root
        assert session_record.config_snapshot_id == BOOTSTRAP_CONFIG_SNAPSHOT_ID
        assert session_record.next_audit_sequence == 3

        events = list(
            db.scalars(
                select(AuditEventRecord)
                .where(AuditEventRecord.session_id == session_id.root)
                .order_by(AuditEventRecord.sequence)
            )
        )
        assert [event.type for event in events] == [
            EventType.SESSION_STATE_CHANGED.value,
            EventType.QUESTION_SELECTED.value,
        ]
        assert events[1].payload == {
            "question_id": str(draft.id),
            "origin": "seeded",
            "fifo": True,
        }
        assert _count(db, OutboxEventRecord) == 2


def test_active_session_blocks_a_second_wake(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        _enqueue_seed(db)
        first = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)
        second = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)

        assert isinstance(first, SessionStarted)
        assert second == WakeSkipped(reason=WakeSkipReason.ACTIVE_SESSION)
        assert _count(db, SessionRecord) == 1


def test_wake_returns_exact_reason_for_empty_queue_or_activation(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        empty = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)
        assert empty == WakeSkipped(reason=WakeSkipReason.NO_ELIGIBLE_QUESTION)

        _enqueue_seed(db)
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        head.activating_config_snapshot_id = BOOTSTRAP_CONFIG_SNAPSHOT_ID
        activating = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)
        assert activating == WakeSkipped(reason=WakeSkipReason.CONFIG_ACTIVATION_IN_PROGRESS)


def test_missing_runtime_head_fails_closed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        db.delete(head)

        with pytest.raises(InconsistentRuntimeStateError, match="head is missing"):
            start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)


def test_caller_rollback_removes_session_audit_and_outbox_but_not_queued_question(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        draft = _enqueue_seed(db)

    with pytest.raises(RuntimeError, match="simulated"), session_factory.begin() as db:
        result = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)
        assert isinstance(result, SessionStarted)
        raise RuntimeError("simulated")

    with session_factory() as db:
        assert _count(db, SessionRecord) == 0
        assert _count(db, AuditEventRecord) == 0
        assert _count(db, OutboxEventRecord) == 0
        question = db.get(QuestionRecord, draft.id.root)
        assert question is not None
        assert question.state == "queued"
