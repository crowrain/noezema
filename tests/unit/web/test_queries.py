from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import start_next_session
from apps.web.models import NodeActivity
from apps.web.queries import InvalidCursorError, QueryService
from packages.domain import (
    EventType,
    InboxIdempotencyKey,
    NodeState,
    OperatorCommandDraft,
    OperatorCommandType,
    SessionId,
    UserMessageDraft,
)
from packages.persistence import append_global_audit, enqueue_user_message, submit_operator_command
from packages.persistence.models import AuditEventRecord, RuntimeControlRecord

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)


def _message(body: str = "Исследуй локальный корпус") -> UserMessageDraft:
    return UserMessageDraft.new(
        idempotency_key=InboxIdempotencyKey.new(),
        sender="owner",
        body=body,
        priority=7,
        created_at=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def test_status_is_derived_from_durable_runtime_and_active_session(
    session_factory: sessionmaker[Session],
) -> None:
    service = QueryService(session_factory, clock=lambda: NOW)

    initial = service.status()
    assert initial.node_state is NodeState.SLEEPING
    assert initial.activity is NodeActivity.IDLE
    assert initial.active_session is None
    assert initial.queued_questions == 0
    assert initial.scheduler.busy is False
    assert initial.scheduler.wake_generation == 0
    assert initial.scheduler.handled_wake_generation == 0

    with session_factory.begin() as db:
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None
        runtime.scheduler_lease_owner = "supervisor/incarnation-1"
        runtime.scheduler_lease_expires_at = NOW + timedelta(minutes=5)
        runtime.scheduler_next_scheduled_at = NOW + timedelta(hours=1)
        runtime.scheduler_backoff_until = NOW + timedelta(minutes=1)
        runtime.scheduler_consecutive_failures = 2

    scheduled = service.status()
    assert scheduled.scheduler.busy is True
    assert scheduled.scheduler.next_scheduled_at == NOW + timedelta(hours=1)
    assert scheduled.scheduler.backoff_until == NOW + timedelta(minutes=1)
    assert scheduled.scheduler.consecutive_failures == 2

    with session_factory.begin() as db:
        queued = enqueue_user_message(db, _message())
        command = submit_operator_command(
            db,
            OperatorCommandDraft.new(
                idempotency_key=InboxIdempotencyKey.new(),
                actor_id="owner",
                type=OperatorCommandType.WAKE_NOW,
                reason="new message",
                created_at=NOW + timedelta(seconds=1),
            ),
        )

    pending = service.status()
    assert pending.queued_questions == 1
    assert pending.pending_messages == 1
    assert pending.pending_commands == 1

    session_id = SessionId.new()
    with session_factory.begin() as db:
        started = start_next_session(
            db,
            session_id=session_id,
            occurred_at=NOW + timedelta(seconds=2),
        )
    assert hasattr(started, "session_id")

    running = service.status()
    assert running.activity is NodeActivity.RUNNING
    assert running.active_session is not None
    assert running.active_session.id == session_id.root
    assert running.active_session.question_id == queued.question_id.root
    assert running.active_session.question_text == "Исследуй локальный корпус"
    assert running.active_session.usage.model_turns == 0
    assert running.active_session.usage.tool_actions == 0

    messages = service.messages(limit=10)
    commands = service.operator_commands(limit=10)
    assert [item.id for item in messages.items] == [queued.id.root]
    assert messages.items[0].body == "Исследуй локальный корпус"
    assert [item.id for item in commands.items] == [command.id.root]


def test_timeline_is_public_and_cursor_pagination_is_stable_for_equal_timestamps(
    session_factory: sessionmaker[Session],
) -> None:
    event_ids = []
    with session_factory.begin() as db:
        for ordinal in range(4):
            appended = append_global_audit(
                db,
                type=EventType.OPERATOR_COMMAND_STATE_CHANGED,
                occurred_at=NOW,
                actor="operator",
                public_summary=f"event {ordinal}",
                topic="audit.test.v1",
                payload={"ordinal": ordinal},
            )
            event_ids.append(appended.audit_event.id.root)
        hidden = db.get(AuditEventRecord, event_ids[2])
        assert hidden is not None
        hidden.visibility = "private"

    service = QueryService(session_factory, clock=lambda: NOW)
    first = service.timeline(limit=2)
    assert first.next_cursor is not None
    second = service.timeline(limit=2, cursor=first.next_cursor)

    visible_ids = [event_ids[index] for index in (0, 1, 3)]
    expected = sorted(visible_ids, reverse=True)
    actual = [item.id for item in (*first.items, *second.items)]
    assert actual == expected
    assert len(actual) == len(set(actual)) == 3
    assert event_ids[2] not in actual
    assert second.next_cursor is None

    with pytest.raises(InvalidCursorError):
        service.messages(limit=10, cursor=first.next_cursor)
    with pytest.raises(InvalidCursorError):
        service.timeline(limit=10, cursor="not-base64!")


def test_session_filter_cannot_be_changed_between_timeline_pages(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        enqueue_user_message(db, _message())
        started = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)
    assert hasattr(started, "session_id")

    service = QueryService(session_factory, clock=lambda: NOW)
    session_id = started.session_id.root  # type: ignore[union-attr]
    page = service.timeline(limit=1, session_id=session_id)
    assert page.next_cursor is not None

    with pytest.raises(InvalidCursorError):
        service.timeline(limit=1, cursor=page.next_cursor, session_id=uuid4())


def test_timeline_never_returns_private_events_or_untyped_payloads(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        private = append_global_audit(
            db,
            type=EventType.MESSAGE_QUEUED,
            occurred_at=NOW,
            actor="web",
            public_summary="hidden",
            topic="audit.test.v1",
            payload={"chain_of_thought": "must not escape"},
        )
        append_global_audit(
            db,
            type=EventType.MESSAGE_ACKNOWLEDGED,
            occurred_at=NOW + timedelta(seconds=1),
            actor="web",
            public_summary="safe summary",
            topic="audit.test.v1",
            payload={"chain_of_thought": "must not escape even on a public row"},
        )
        record = db.scalar(
            select(AuditEventRecord).where(AuditEventRecord.id == private.audit_event.id.root)
        )
        assert record is not None
        record.visibility = "private"

    items = QueryService(session_factory).timeline(limit=100).items
    assert len(items) == 1
    assert items[0].summary == "safe summary"
    assert not hasattr(items[0], "payload")
