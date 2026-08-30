"""Trusted Command API application service for durable inbox admission."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from apps.web.models import (
    MessageAcceptedResponse,
    MessageCreateRequest,
    OperatorCommandAcceptedResponse,
    OperatorCommandCreateRequest,
)
from packages.domain import (
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
    enqueue_user_message,
    submit_operator_command,
)
from packages.persistence.models import MessageRecord, OperatorCommandRecord


class CommandService:
    """Translate authenticated requests into immutable domain inbox drafts."""

    def __init__(
        self,
        session_factory: Callable[[], Session],
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._clock = clock or (lambda: datetime.now(UTC))

    def submit_message(
        self,
        request: MessageCreateRequest,
        *,
        actor_id: str,
    ) -> MessageAcceptedResponse:
        occurred_at = self._now()
        draft = UserMessageDraft.new(
            idempotency_key=InboxIdempotencyKey(root=request.idempotency_key),
            sender=actor_id,
            body=request.body,
            priority=request.priority,
            created_at=occurred_at,
            expires_at=occurred_at + timedelta(seconds=request.expires_in_seconds),
        )
        try:
            with self._session_factory() as db, db.begin():
                existing = self._existing_message(db, request=request, actor_id=actor_id)
                if existing is not None:
                    return existing
                result = enqueue_user_message(db, draft)
        except IntegrityError:
            with self._session_factory() as db:
                raced = self._existing_message(db, request=request, actor_id=actor_id)
                if raced is None:
                    raise
                return raced
        return MessageAcceptedResponse(
            id=result.id.root,
            question_id=result.question_id.root,
            state=result.state,
            newly_created=result.newly_created,
        )

    def submit_operator_command(
        self,
        request: OperatorCommandCreateRequest,
        *,
        actor_id: str,
    ) -> OperatorCommandAcceptedResponse:
        occurred_at = self._now()
        draft = OperatorCommandDraft.new(
            idempotency_key=InboxIdempotencyKey(root=request.idempotency_key),
            actor_id=actor_id,
            type=request.type,
            session_id=(
                SessionId(root=request.session_id) if request.session_id is not None else None
            ),
            arguments=request.arguments,
            reason=request.reason,
            created_at=occurred_at,
        )
        try:
            with self._session_factory() as db, db.begin():
                existing = self._existing_operator_command(
                    db,
                    request=request,
                    actor_id=actor_id,
                )
                if existing is not None:
                    return existing
                result = submit_operator_command(db, draft)
        except IntegrityError:
            with self._session_factory() as db:
                raced = self._existing_operator_command(
                    db,
                    request=request,
                    actor_id=actor_id,
                )
                if raced is None:
                    raise
                return raced
        return OperatorCommandAcceptedResponse(
            id=result.id.root,
            type=result.type,
            state=result.state,
            newly_created=result.newly_created,
        )

    @staticmethod
    def _existing_message(
        db: Session,
        *,
        request: MessageCreateRequest,
        actor_id: str,
    ) -> MessageAcceptedResponse | None:
        record = db.scalar(
            select(MessageRecord).where(MessageRecord.idempotency_key == request.idempotency_key)
        )
        if record is None:
            return None
        lifetime_seconds = int((record.expires_at - record.created_at).total_seconds())
        if (
            record.sender != actor_id
            or record.body != request.body
            or record.priority != request.priority
            or lifetime_seconds != request.expires_in_seconds
        ):
            raise InboxBindingConflictError("message idempotency key is bound to other HTTP input")
        return MessageAcceptedResponse(
            id=record.id,
            question_id=record.question_id,
            state=MessageState(record.state),
            newly_created=False,
        )

    @staticmethod
    def _existing_operator_command(
        db: Session,
        *,
        request: OperatorCommandCreateRequest,
        actor_id: str,
    ) -> OperatorCommandAcceptedResponse | None:
        record = db.scalar(
            select(OperatorCommandRecord).where(
                OperatorCommandRecord.idempotency_key == request.idempotency_key
            )
        )
        if record is None:
            return None
        if (
            record.actor_id != actor_id
            or record.type != request.type.value
            or record.session_id != request.session_id
            or record.arguments != request.arguments
            or record.reason != request.reason
        ):
            raise InboxBindingConflictError("command idempotency key is bound to other HTTP input")
        return OperatorCommandAcceptedResponse(
            id=record.id,
            type=OperatorCommandType(record.type),
            state=OperatorCommandState(record.state),
            newly_created=False,
        )

    def _now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None:
            raise ValueError("command clock must return a timezone-aware datetime")
        return value
