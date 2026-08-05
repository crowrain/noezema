"""Transactional MVP wake admission and FIFO question binding."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.orchestrator.models import (
    SessionStarted,
    WakeResult,
    WakeSkipped,
    WakeSkipReason,
)
from packages.cognition import select_fifo_question_for_update
from packages.domain import (
    ConfigSnapshotId,
    EventType,
    QuestionId,
    QuestionOrigin,
    SessionId,
    SessionState,
)
from packages.persistence import append_session_audit, create_session_with_audit
from packages.persistence.models import RuntimeConfigHeadRecord, SessionRecord

_TERMINAL_SESSION_STATES = tuple(state.value for state in SessionState if state.is_terminal)


class InconsistentRuntimeStateError(RuntimeError):
    """The required singleton runtime configuration head is absent or invalid."""


def start_next_session(
    db: Session,
    *,
    session_id: SessionId,
    occurred_at: datetime | None = None,
) -> WakeResult:
    """Admit one session and bind the oldest eligible question atomically.

    The caller owns the transaction. The runtime-head lock serializes session
    creation; content and audit writes therefore become visible together.
    """

    timestamp = occurred_at or datetime.now(UTC)
    runtime_head = db.scalar(
        select(RuntimeConfigHeadRecord)
        .where(RuntimeConfigHeadRecord.scope == "global")
        .with_for_update()
    )
    if runtime_head is None:
        raise InconsistentRuntimeStateError("global runtime configuration head is missing")
    if runtime_head.activating_config_snapshot_id is not None:
        return WakeSkipped(reason=WakeSkipReason.CONFIG_ACTIVATION_IN_PROGRESS)

    active_session = db.scalar(
        select(SessionRecord.id)
        .where(SessionRecord.state.not_in(_TERMINAL_SESSION_STATES))
        .limit(1)
        .with_for_update(of=SessionRecord)
    )
    if active_session is not None:
        return WakeSkipped(reason=WakeSkipReason.ACTIVE_SESSION)

    question = select_fifo_question_for_update(db)
    if question is None:
        return WakeSkipped(reason=WakeSkipReason.NO_ELIGIBLE_QUESTION)

    question_id = QuestionId(root=question.id)
    config_snapshot_id = ConfigSnapshotId(root=runtime_head.active_config_snapshot_id)
    create_session_with_audit(
        db,
        session_id=session_id,
        config_snapshot_id=config_snapshot_id,
        question_id=question_id,
        occurred_at=timestamp,
    )
    append_session_audit(
        db,
        session_id=session_id,
        type=EventType.QUESTION_SELECTED,
        occurred_at=timestamp,
        actor="orchestrator",
        public_summary="Research question selected",
        topic="audit.question_selected.v1",
        payload={
            "question_id": str(question_id),
            "origin": question.origin,
            "fifo": True,
        },
    )

    return SessionStarted(
        session_id=session_id,
        question_id=question_id,
        question_text=question.text,
        question_origin=QuestionOrigin(question.origin),
        config_snapshot_id=config_snapshot_id,
    )
