"""FIFO queue persistence and locking tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session, sessionmaker

from packages.cognition import (
    QuestionIdentityConflictError,
    enqueue_question,
    fifo_question_statement,
    select_fifo_question_for_update,
)
from packages.domain import ConfigSnapshotId, QuestionDraft, QuestionOrigin, SessionId
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, create_session_with_audit
from packages.persistence.models import QuestionRecord, SessionRecord

CONFIG_ID = ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID)
NOW = datetime(2026, 8, 5, 9, 0, tzinfo=UTC)


def _seeded(text: str, created_at: datetime, *, priority: int = 0) -> QuestionDraft:
    return QuestionDraft.seeded(
        text=text,
        origin_config_snapshot_id=CONFIG_ID,
        created_at=created_at,
        priority=priority,
    )


def test_enqueue_is_idempotent_only_for_identical_immutable_content(
    session_factory: sessionmaker[Session],
) -> None:
    draft = _seeded("Проверить очередь", NOW)

    with session_factory.begin() as db:
        enqueue_question(db, draft)
        enqueue_question(db, draft)
        count = db.scalar(select(func.count()).select_from(QuestionRecord))

        assert count == 1
        with pytest.raises(QuestionIdentityConflictError):
            enqueue_question(db, draft.model_copy(update={"text": "Другой вопрос"}))


def test_fifo_orders_by_creation_time_and_id_not_priority(
    session_factory: sessionmaker[Session],
) -> None:
    newer_high_priority = _seeded("Новый", NOW, priority=1000)
    older_low_priority = _seeded("Старый", NOW - timedelta(minutes=1), priority=-1000)

    with session_factory.begin() as db:
        enqueue_question(db, newer_high_priority)
        enqueue_question(db, older_low_priority)
        selected = select_fifo_question_for_update(db)

        assert selected is not None
        assert selected.id == older_low_priority.id.root


def test_fifo_query_uses_postgresql_skip_locked_contract() -> None:
    sql = str(
        fifo_question_statement().compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )

    assert "ORDER BY questions.created_at ASC, questions.id ASC" in sql
    assert "FOR UPDATE OF questions SKIP LOCKED" in sql
    assert "questions.priority" not in sql.split("ORDER BY", maxsplit=1)[1]


def test_question_bound_to_active_session_is_not_eligible(
    session_factory: sessionmaker[Session],
) -> None:
    first = _seeded("Первый", NOW - timedelta(minutes=1))
    second = QuestionDraft.from_message(
        text="Второй",
        origin_config_snapshot_id=CONFIG_ID,
        created_at=NOW,
    )
    with session_factory.begin() as db:
        enqueue_question(db, first)
        enqueue_question(db, second)
        create_session_with_audit(
            db,
            session_id=SessionId.new(),
            config_snapshot_id=CONFIG_ID,
            question_id=first.id,
            occurred_at=NOW,
        )
        selected = select_fifo_question_for_update(db)

        assert selected is not None
        assert selected.id == second.id.root
        assert selected.origin == QuestionOrigin.MESSAGE.value


def test_failed_session_releases_queued_question_for_retry(
    session_factory: sessionmaker[Session],
) -> None:
    draft = _seeded("Повторить после сбоя", NOW)
    session_id = SessionId.new()
    with session_factory.begin() as db:
        enqueue_question(db, draft)
        create_session_with_audit(
            db,
            session_id=session_id,
            config_snapshot_id=CONFIG_ID,
            question_id=draft.id,
            occurred_at=NOW,
        )
        session_record = db.get(SessionRecord, session_id.root)
        assert session_record is not None
        session_record.state = "failed"
        session_record.terminal_at = NOW

    with session_factory() as db:
        selected = select_fifo_question_for_update(db)
        assert selected is not None
        assert selected.id == draft.id.root
