"""Committed outbox total-order assignment tests."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from packages.domain import EventType
from packages.persistence import append_global_audit, sequence_pending_outbox
from packages.persistence.models import OutboxEventRecord, RuntimeControlRecord

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)


def _append(db: Session, ordinal: int, *, created_at: datetime) -> None:
    append_global_audit(
        db,
        type=EventType.OPERATOR_COMMAND_STATE_CHANGED,
        occurred_at=created_at,
        actor="test",
        public_summary=f"event {ordinal}",
        topic="audit.test.v1",
        payload={"ordinal": ordinal},
    )


def test_committed_outbox_receives_one_contiguous_total_order(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        _append(db, 3, created_at=NOW + timedelta(seconds=3))
        _append(db, 1, created_at=NOW + timedelta(seconds=1))
        _append(db, 2, created_at=NOW + timedelta(seconds=2))

    publication_time = NOW + timedelta(minutes=1)
    with session_factory.begin() as db:
        first = sequence_pending_outbox(db, occurred_at=publication_time, limit=2)
    with session_factory.begin() as db:
        second = sequence_pending_outbox(db, occurred_at=publication_time, limit=2)
        empty = sequence_pending_outbox(db, occurred_at=publication_time, limit=2)

    assert first.count == 2
    assert (first.first_sequence, first.last_sequence) == (1, 2)
    assert second.count == 1
    assert (second.first_sequence, second.last_sequence) == (3, 3)
    assert empty.count == 0
    assert empty.first_sequence is empty.last_sequence is None

    with session_factory() as db:
        records = list(
            db.scalars(select(OutboxEventRecord).order_by(OutboxEventRecord.stream_sequence))
        )
        runtime = db.get(RuntimeControlRecord, "global")
    assert [record.payload["event"]["payload"]["ordinal"] for record in records] == [1, 2, 3]
    assert [record.stream_sequence for record in records] == [1, 2, 3]
    assert [record.published_at.replace(tzinfo=UTC) for record in records] == [publication_time] * 3
    assert [record.attempts for record in records] == [1, 1, 1]
    assert runtime is not None and runtime.next_outbox_sequence == 4


def test_sequence_assignment_rolls_back_with_the_caller_transaction(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        _append(db, 1, created_at=NOW)

    with pytest.raises(RuntimeError, match="simulated failure"):
        with session_factory.begin() as db:
            batch = sequence_pending_outbox(db, occurred_at=NOW + timedelta(seconds=1))
            assert batch.count == 1
            raise RuntimeError("simulated failure")

    with session_factory() as db:
        record = db.scalar(select(OutboxEventRecord))
        runtime = db.get(RuntimeControlRecord, "global")
    assert record is not None
    assert record.stream_sequence is None
    assert record.published_at is None
    assert record.attempts == 0
    assert runtime is not None and runtime.next_outbox_sequence == 1


def test_sequence_assignment_validates_its_bounded_contract(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        with pytest.raises(ValueError, match="timezone-aware"):
            sequence_pending_outbox(db, occurred_at=NOW.replace(tzinfo=None))
        with pytest.raises(ValueError, match="between 1 and 1000"):
            sequence_pending_outbox(db, occurred_at=NOW, limit=0)
