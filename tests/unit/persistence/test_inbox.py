"""Durable inbox idempotency, delivery and transactional audit tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import start_next_session
from packages.domain import (
    EventType,
    InboxIdempotencyKey,
    MessageState,
    OperatorCommandDraft,
    OperatorCommandState,
    OperatorCommandType,
    SessionId,
    UserMessageDraft,
)
from packages.persistence import (
    InboxBindingConflictError,
    acknowledge_session_messages,
    deliver_messages_for_session,
    enqueue_user_message,
    submit_operator_command,
)
from packages.persistence.models import (
    AuditEventRecord,
    MessageRecord,
    OperatorCommandRecord,
    OutboxEventRecord,
    QuestionRecord,
    RuntimeControlRecord,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _message(
    *,
    key: InboxIdempotencyKey | None = None,
    body: str = "Исследуй локальный корпус",
    priority: int = 0,
    created_at: datetime = NOW,
    expires_at: datetime | None = None,
) -> UserMessageDraft:
    return UserMessageDraft.new(
        idempotency_key=key or InboxIdempotencyKey.new(),
        sender="owner",
        body=body,
        priority=priority,
        created_at=created_at,
        expires_at=expires_at or created_at + timedelta(days=1),
    )


def test_message_retry_is_idempotent_and_payload_conflict_is_rejected(
    session_factory: sessionmaker[Session],
) -> None:
    key = InboxIdempotencyKey.new()
    first_draft = _message(key=key)
    exact_retry = _message(key=key)

    with session_factory.begin() as db:
        first = enqueue_user_message(db, first_draft)
        repeated = enqueue_user_message(db, exact_retry)

    assert first.newly_created is True
    assert repeated.newly_created is False
    assert repeated.id == first.id
    assert repeated.question_id == first.question_id
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(MessageRecord)) == 1
        assert db.scalar(select(func.count()).select_from(QuestionRecord)) == 1
        event = db.scalar(select(AuditEventRecord))
        assert event is not None and event.type == EventType.MESSAGE_QUEUED.value
        assert event.session_id is None and event.sequence == 1
        assert db.scalar(select(func.count()).select_from(OutboxEventRecord)) == 1
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None and runtime.next_global_audit_sequence == 2

    with pytest.raises(InboxBindingConflictError), session_factory.begin() as db:
        enqueue_user_message(db, _message(key=key, body="Другой текст"))


def test_delivery_is_priority_ordered_replayable_and_acknowledged_once(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        low = enqueue_user_message(db, _message(body="low", priority=-1))
        high = enqueue_user_message(
            db,
            _message(body="high", priority=10, created_at=NOW + timedelta(seconds=1)),
        )
        started = start_next_session(
            db,
            session_id=SessionId.new(),
            occurred_at=NOW + timedelta(seconds=2),
        )
    assert hasattr(started, "session_id")
    session_id = started.session_id  # type: ignore[union-attr]

    with session_factory.begin() as db:
        delivered = deliver_messages_for_session(
            db,
            session_id=session_id,
            occurred_at=NOW + timedelta(seconds=3),
        )
    assert [item.body for item in delivered] == ["high", "low"]

    with session_factory.begin() as db:
        replayed = deliver_messages_for_session(
            db,
            session_id=session_id,
            occurred_at=NOW + timedelta(seconds=4),
        )
        acknowledged = acknowledge_session_messages(
            db,
            session_id=session_id,
            message_ids=tuple(item.id for item in replayed),
            occurred_at=NOW + timedelta(seconds=5),
        )
        repeated = acknowledge_session_messages(
            db,
            session_id=session_id,
            message_ids=acknowledged.message_ids,
            occurred_at=NOW + timedelta(seconds=6),
        )

    assert set(acknowledged.message_ids) == {low.id, high.id}
    assert repeated == acknowledged
    with session_factory() as db:
        states = tuple(db.scalars(select(MessageRecord.state).order_by(MessageRecord.body)))
        assert states == (MessageState.ACKNOWLEDGED.value,) * 2
        ack_events = db.scalar(
            select(func.count())
            .select_from(AuditEventRecord)
            .where(AuditEventRecord.type == EventType.MESSAGE_ACKNOWLEDGED.value)
        )
        assert ack_events == 2


def test_expired_message_discards_its_unbound_fifo_question(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        live = enqueue_user_message(
            db,
            _message(body="live", expires_at=NOW + timedelta(days=1)),
        )
        expired = enqueue_user_message(
            db,
            _message(
                body="expired",
                created_at=NOW + timedelta(seconds=1),
                expires_at=NOW + timedelta(seconds=2),
            ),
        )
        started = start_next_session(
            db,
            session_id=SessionId.new(),
            occurred_at=NOW + timedelta(seconds=3),
        )
    session_id = started.session_id  # type: ignore[union-attr]

    with session_factory.begin() as db:
        delivered = deliver_messages_for_session(
            db,
            session_id=session_id,
            occurred_at=NOW + timedelta(seconds=4),
        )

    assert [item.id for item in delivered] == [live.id]
    with session_factory() as db:
        expired_record = db.get(MessageRecord, expired.id.root)
        expired_question = db.get(QuestionRecord, expired.question_id.root)
        assert expired_record is not None
        assert expired_record.state == MessageState.EXPIRED.value
        assert expired_question is not None and expired_question.state == "discarded"


def test_operator_command_submission_is_idempotent_and_has_no_direct_effect(
    session_factory: sessionmaker[Session],
) -> None:
    key = InboxIdempotencyKey.new()
    first_draft = OperatorCommandDraft.new(
        idempotency_key=key,
        actor_id="owner",
        type=OperatorCommandType.PAUSE,
        reason="maintenance",
        created_at=NOW,
    )
    retry = OperatorCommandDraft.new(
        idempotency_key=key,
        actor_id="owner",
        type=OperatorCommandType.PAUSE,
        reason="maintenance",
        created_at=NOW,
    )
    with session_factory.begin() as db:
        first = submit_operator_command(db, first_draft)
        repeated = submit_operator_command(db, retry)

    assert first.state is OperatorCommandState.ACCEPTED
    assert first.newly_created is True
    assert repeated.id == first.id and repeated.newly_created is False
    with session_factory() as db:
        command = db.get(OperatorCommandRecord, first.id.root)
        runtime = db.get(RuntimeControlRecord, "global")
        assert command is not None and command.state == OperatorCommandState.ACCEPTED.value
        assert runtime is not None and runtime.node_state == "sleeping"
