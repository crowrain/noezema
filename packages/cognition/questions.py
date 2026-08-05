"""FIFO question queue used by the MVP curiosity baseline."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Select, exists, select
from sqlalchemy.orm import Session

from packages.domain import QuestionDraft, QuestionId, QuestionState, SessionState
from packages.persistence.models import QuestionRecord, SessionRecord

_TERMINAL_SESSION_STATES = tuple(state.value for state in SessionState if state.is_terminal)


class QuestionIdentityConflictError(RuntimeError):
    """A retry reused a question ID for different immutable content."""


def _canonical_timestamp(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat()


def _matches_draft(record: QuestionRecord, draft: QuestionDraft) -> bool:
    return (
        record.text == draft.text
        and record.origin == draft.origin.value
        and record.origin_config_snapshot_id == draft.origin_config_snapshot_id.root
        and record.state == QuestionState.QUEUED.value
        and record.priority == draft.priority
        and record.parent_id == (draft.parent_id.root if draft.parent_id is not None else None)
        and record.score_components == {}
        and record.embedding_fingerprint is None
        and _canonical_timestamp(record.created_at) == _canonical_timestamp(draft.created_at)
    )


def enqueue_question(db: Session, draft: QuestionDraft) -> QuestionId:
    """Insert an immutable queued question; an exact ID retry is idempotent."""

    existing = db.get(QuestionRecord, draft.id.root)
    if existing is not None:
        if not _matches_draft(existing, draft):
            raise QuestionIdentityConflictError(
                f"question ID is already bound to different content: {draft.id}"
            )
        return draft.id

    db.add(
        QuestionRecord(
            id=draft.id.root,
            text=draft.text,
            origin=draft.origin.value,
            origin_config_snapshot_id=draft.origin_config_snapshot_id.root,
            state=QuestionState.QUEUED.value,
            priority=draft.priority,
            parent_id=draft.parent_id.root if draft.parent_id is not None else None,
            score_components={},
            embedding_fingerprint=None,
            created_at=draft.created_at,
        )
    )
    db.flush()
    return draft.id


def fifo_question_statement() -> Select[tuple[QuestionRecord]]:
    """Build the deterministic first-eligible query with a PostgreSQL queue lock."""

    bound_to_active_session = exists(
        select(SessionRecord.id).where(
            SessionRecord.question_id == QuestionRecord.id,
            SessionRecord.state.not_in(_TERMINAL_SESSION_STATES),
        )
    )
    return (
        select(QuestionRecord)
        .where(
            QuestionRecord.state == QuestionState.QUEUED.value,
            ~bound_to_active_session,
        )
        .order_by(QuestionRecord.created_at.asc(), QuestionRecord.id.asc())
        .limit(1)
        .with_for_update(of=QuestionRecord, skip_locked=True)
    )


def select_fifo_question_for_update(db: Session) -> QuestionRecord | None:
    """Lock and return the oldest eligible queued question."""

    return db.scalar(fifo_question_statement())
