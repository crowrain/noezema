"""Durable, idempotent inbox operations owned by the trusted host."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from packages.domain import (
    AuditEventId,
    ConfigSnapshotId,
    DeliveredMessage,
    EventType,
    MessageId,
    MessageState,
    OperatorCommandDraft,
    OperatorCommandId,
    OperatorCommandResult,
    OperatorCommandState,
    OperatorCommandType,
    QuestionDraft,
    QuestionId,
    QuestionState,
    QueuedMessage,
    SessionId,
    SessionState,
    UserMessageDraft,
    canonical_json_sha256,
)
from packages.persistence.models import (
    MessageRecord,
    OperatorCommandRecord,
    QuestionRecord,
    RuntimeConfigHeadRecord,
    SessionRecord,
)
from packages.persistence.operations import append_global_audit

_TERMINAL_SESSION_STATES = tuple(item.value for item in SessionState if item.is_terminal)


class InboxBindingConflictError(RuntimeError):
    """An idempotency key was reused with different immutable input."""


class InboxStateConflictError(RuntimeError):
    """An inbox transition was requested from an incompatible state."""


@dataclass(frozen=True, slots=True)
class AcknowledgedMessages:
    message_ids: tuple[MessageId, ...]


def enqueue_user_message(db: Session, draft: UserMessageDraft) -> QueuedMessage:
    """Atomically create a message, a FIFO question and its audit/outbox event."""

    request_hash = _request_hash(draft)
    existing = db.scalar(
        select(MessageRecord)
        .where(MessageRecord.idempotency_key == draft.idempotency_key.root)
        .with_for_update()
    )
    if existing is not None:
        if existing.request_sha256 != request_hash:
            raise InboxBindingConflictError("message idempotency key is bound to other input")
        return QueuedMessage(
            id=MessageId(root=existing.id),
            question_id=QuestionId(root=existing.question_id),
            state=MessageState(existing.state),
            newly_created=False,
        )

    runtime_head = db.scalar(
        select(RuntimeConfigHeadRecord)
        .where(RuntimeConfigHeadRecord.scope == "global")
        .with_for_update()
    )
    if runtime_head is None:
        raise LookupError("global runtime configuration head is missing")
    if runtime_head.activating_config_snapshot_id is not None:
        raise InboxStateConflictError("message admission is blocked during config activation")
    question = QuestionDraft.from_message(
        text=draft.body,
        origin_config_snapshot_id=ConfigSnapshotId(root=runtime_head.active_config_snapshot_id),
        created_at=draft.created_at,
        priority=draft.priority,
    )
    db.add(
        QuestionRecord(
            id=question.id.root,
            text=question.text,
            origin=question.origin.value,
            origin_config_snapshot_id=question.origin_config_snapshot_id.root,
            state=QuestionState.QUEUED.value,
            priority=question.priority,
            parent_id=None,
            score_components={},
            embedding_fingerprint=None,
            created_at=question.created_at,
        )
    )
    db.flush()
    db.add(
        MessageRecord(
            id=draft.id.root,
            idempotency_key=draft.idempotency_key.root,
            request_sha256=request_hash,
            question_id=question.id.root,
            sender=draft.sender,
            body=draft.body,
            priority=draft.priority,
            state=MessageState.QUEUED.value,
            expires_at=draft.expires_at,
            created_at=draft.created_at,
            delivered_session_id=None,
            delivered_at=None,
            acknowledged_at=None,
            answered_at=None,
            response_audit_event_id=None,
        )
    )
    db.flush()
    append_global_audit(
        db,
        type=EventType.MESSAGE_QUEUED,
        occurred_at=draft.created_at,
        actor=draft.sender,
        public_summary="Human message queued",
        topic="audit.message_queued.v1",
        payload={
            "message_id": str(draft.id),
            "question_id": str(question.id),
            "priority": draft.priority,
        },
    )
    return QueuedMessage(
        id=draft.id,
        question_id=question.id,
        state=MessageState.QUEUED,
        newly_created=True,
    )


def deliver_messages_for_session(
    db: Session,
    *,
    session_id: SessionId,
    occurred_at: datetime,
    limit: int = 32,
) -> tuple[DeliveredMessage, ...]:
    """Deliver bounded messages at-least-once to one live cognitive session."""

    if not 1 <= limit <= 32:
        raise ValueError("message delivery limit must be between 1 and 32")
    session = db.scalar(
        select(SessionRecord).where(SessionRecord.id == session_id.root).with_for_update()
    )
    if session is None:
        raise LookupError(f"session does not exist: {session_id}")
    if SessionState(session.state).is_terminal:
        raise InboxStateConflictError("messages cannot be delivered to a terminal session")

    stale = tuple(
        db.scalars(
            select(MessageRecord)
            .join(SessionRecord, SessionRecord.id == MessageRecord.delivered_session_id)
            .where(
                MessageRecord.state == MessageState.DELIVERED.value,
                SessionRecord.state.in_(_TERMINAL_SESSION_STATES),
            )
            .with_for_update(of=MessageRecord)
        )
    )
    for record in stale:
        question = db.get(QuestionRecord, record.question_id)
        if question is not None and question.state == QuestionState.ANSWERED.value:
            record.state = MessageState.ACKNOWLEDGED.value
            record.acknowledged_at = occurred_at
            append_global_audit(
                db,
                type=EventType.MESSAGE_ACKNOWLEDGED,
                occurred_at=occurred_at,
                actor="orchestrator",
                public_summary="Message acknowledgement recovered from terminal session",
                topic="audit.message_acknowledged.v1",
                payload={
                    "message_id": str(record.id),
                    "session_id": str(record.delivered_session_id),
                    "recovered": True,
                },
            )
        else:
            record.state = MessageState.QUEUED.value
            record.delivered_session_id = None
            record.delivered_at = None
    _expire_queued_messages(db, occurred_at=occurred_at)

    records = tuple(
        db.scalars(
            select(MessageRecord)
            .where(
                or_(
                    MessageRecord.state == MessageState.QUEUED.value,
                    (
                        (MessageRecord.state == MessageState.DELIVERED.value)
                        & (MessageRecord.delivered_session_id == session_id.root)
                    ),
                )
            )
            .order_by(
                MessageRecord.priority.desc(),
                MessageRecord.created_at,
                MessageRecord.id,
            )
            .limit(limit)
            .with_for_update(of=MessageRecord, skip_locked=True)
        )
    )
    delivered: list[DeliveredMessage] = []
    for record in records:
        if record.state == MessageState.QUEUED.value:
            record.state = MessageState.DELIVERED.value
            record.delivered_session_id = session_id.root
            record.delivered_at = occurred_at
            append_global_audit(
                db,
                type=EventType.MESSAGE_DELIVERED,
                occurred_at=occurred_at,
                actor="orchestrator",
                public_summary="Message delivered to cognitive session",
                topic="audit.message_delivered.v1",
                payload={"message_id": str(record.id), "session_id": str(session_id)},
            )
        assert record.delivered_at is not None
        delivered.append(
            DeliveredMessage(
                id=MessageId(root=record.id),
                body=record.body,
                priority=record.priority,
                delivered_at=_aware(record.delivered_at),
            )
        )
    db.flush()
    return tuple(delivered)


def acknowledge_session_messages(
    db: Session,
    *,
    session_id: SessionId,
    message_ids: tuple[MessageId, ...],
    occurred_at: datetime,
) -> AcknowledgedMessages:
    """Acknowledge only messages durably included in a completed model turn."""

    unique_ids = tuple(dict.fromkeys(item.root for item in message_ids))
    if not unique_ids:
        return AcknowledgedMessages(message_ids=())
    records = tuple(
        db.scalars(
            select(MessageRecord)
            .where(MessageRecord.id.in_(unique_ids))
            .order_by(MessageRecord.id)
            .with_for_update()
        )
    )
    if len(records) != len(unique_ids):
        raise LookupError("one or more delivered messages do not exist")
    acknowledged: list[MessageId] = []
    for record in records:
        if record.delivered_session_id != session_id.root:
            raise InboxStateConflictError("message belongs to another delivery session")
        if record.state != MessageState.DELIVERED.value:
            if record.state == MessageState.ACKNOWLEDGED.value:
                acknowledged.append(MessageId(root=record.id))
                continue
            raise InboxStateConflictError(f"message cannot be acknowledged from {record.state}")
        record.state = MessageState.ACKNOWLEDGED.value
        record.acknowledged_at = occurred_at
        message_id = MessageId(root=record.id)
        acknowledged.append(message_id)
        append_global_audit(
            db,
            type=EventType.MESSAGE_ACKNOWLEDGED,
            occurred_at=occurred_at,
            actor="orchestrator",
            public_summary="Message acknowledged by cognitive session",
            topic="audit.message_acknowledged.v1",
            payload={"message_id": str(message_id), "session_id": str(session_id)},
        )
    db.flush()
    return AcknowledgedMessages(message_ids=tuple(acknowledged))


def mark_message_answered(
    db: Session,
    *,
    message_id: MessageId,
    session_id: SessionId,
    response_audit_event_id: AuditEventId,
    occurred_at: datetime,
) -> None:
    """Bind one acknowledged message to the audited ``message.reply`` effect."""

    record = db.scalar(
        select(MessageRecord).where(MessageRecord.id == message_id.root).with_for_update()
    )
    if record is None:
        raise LookupError(f"message does not exist: {message_id}")
    if record.state == MessageState.ANSWERED.value:
        if record.response_audit_event_id != response_audit_event_id.root:
            raise InboxBindingConflictError("message is already bound to another response")
        return
    if (
        record.state != MessageState.ACKNOWLEDGED.value
        or record.delivered_session_id != session_id.root
    ):
        raise InboxStateConflictError("only an acknowledged message can be answered")
    record.state = MessageState.ANSWERED.value
    record.answered_at = occurred_at
    record.response_audit_event_id = response_audit_event_id.root
    append_global_audit(
        db,
        type=EventType.MESSAGE_ANSWERED,
        occurred_at=occurred_at,
        actor="orchestrator",
        public_summary="Message answered",
        topic="audit.message_answered.v1",
        payload={"message_id": str(message_id), "session_id": str(session_id)},
    )
    db.flush()


def submit_operator_command(
    db: Session,
    draft: OperatorCommandDraft,
) -> OperatorCommandResult:
    """Persist a typed command without applying its effect in the web transaction."""

    request_hash = _request_hash(draft)
    existing = db.scalar(
        select(OperatorCommandRecord)
        .where(OperatorCommandRecord.idempotency_key == draft.idempotency_key.root)
        .with_for_update()
    )
    if existing is not None:
        if existing.request_sha256 != request_hash:
            raise InboxBindingConflictError("command idempotency key is bound to other input")
        return _command_result(existing, newly_created=False)
    record = OperatorCommandRecord(
        id=draft.id.root,
        idempotency_key=draft.idempotency_key.root,
        request_sha256=request_hash,
        actor_id=draft.actor_id,
        type=draft.type.value,
        session_id=draft.session_id.root if draft.session_id is not None else None,
        arguments=draft.arguments,
        state=OperatorCommandState.ACCEPTED.value,
        reason=draft.reason,
        result=None,
        error_code=None,
        created_at=draft.created_at,
        updated_at=draft.created_at,
        finished_at=None,
    )
    db.add(record)
    db.flush()
    append_global_audit(
        db,
        type=EventType.OPERATOR_COMMAND_ACCEPTED,
        occurred_at=draft.created_at,
        actor=draft.actor_id,
        public_summary=f"Operator command accepted: {draft.type.value}",
        topic="audit.operator_command_accepted.v1",
        payload={
            "command_id": str(draft.id),
            "command": draft.type.value,
            "session_id": str(draft.session_id) if draft.session_id is not None else None,
        },
    )
    return _command_result(record, newly_created=True)


def operator_command_result(db: Session, command_id: OperatorCommandId) -> OperatorCommandResult:
    record = db.get(OperatorCommandRecord, command_id.root)
    if record is None:
        raise LookupError(f"operator command does not exist: {command_id}")
    return _command_result(record, newly_created=False)


def _expire_queued_messages(db: Session, *, occurred_at: datetime) -> None:
    bound_to_active_session = exists(
        select(SessionRecord.id).where(
            SessionRecord.question_id == MessageRecord.question_id,
            SessionRecord.state.not_in(_TERMINAL_SESSION_STATES),
        )
    )
    expired = tuple(
        db.scalars(
            select(MessageRecord)
            .where(
                MessageRecord.state == MessageState.QUEUED.value,
                MessageRecord.expires_at <= occurred_at,
                ~bound_to_active_session,
            )
            .order_by(MessageRecord.created_at, MessageRecord.id)
            .with_for_update()
        )
    )
    for record in expired:
        record.state = MessageState.EXPIRED.value
        question = db.get(QuestionRecord, record.question_id)
        if question is not None and question.state == QuestionState.QUEUED.value:
            question.state = QuestionState.DISCARDED.value
        append_global_audit(
            db,
            type=EventType.MESSAGE_EXPIRED,
            occurred_at=occurred_at,
            actor="orchestrator",
            public_summary="Queued message expired",
            topic="audit.message_expired.v1",
            payload={"message_id": str(record.id), "question_id": str(record.question_id)},
        )


def _request_hash(draft: UserMessageDraft | OperatorCommandDraft) -> str:
    payload = draft.model_dump(mode="json", exclude={"id"})
    return canonical_json_sha256(payload)


def _command_result(
    record: OperatorCommandRecord,
    *,
    newly_created: bool,
) -> OperatorCommandResult:
    return OperatorCommandResult(
        id=OperatorCommandId(root=record.id),
        type=OperatorCommandType(record.type),
        state=OperatorCommandState(record.state),
        newly_created=newly_created,
        result=record.result,
    )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value
