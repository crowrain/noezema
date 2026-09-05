"""Durable total-order assignment for committed transactional outbox rows."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from packages.persistence.models import OutboxEventRecord, RuntimeControlRecord


@dataclass(frozen=True, slots=True)
class SequencedOutboxBatch:
    """The contiguous stream range assigned by one caller-owned transaction."""

    count: int
    first_sequence: int | None
    last_sequence: int | None


def sequence_pending_outbox(
    db: Session,
    *,
    occurred_at: datetime,
    limit: int = 100,
) -> SequencedOutboxBatch:
    """Assign a total order to committed outbox rows visible to this transaction.

    The caller owns the transaction. The singleton runtime row serializes every
    sequencer, while the initial existence probe avoids taking that lock when the
    stream is idle. A row receives its sequence and publication marker atomically.
    """

    if occurred_at.tzinfo is None:
        raise ValueError("outbox publication time must be timezone-aware")
    if not 1 <= limit <= 1000:
        raise ValueError("outbox sequencing limit must be between 1 and 1000")

    pending_id = db.scalar(
        select(OutboxEventRecord.id)
        .where(OutboxEventRecord.stream_sequence.is_(None))
        .order_by(OutboxEventRecord.created_at, OutboxEventRecord.id)
        .limit(1)
    )
    if pending_id is None:
        return SequencedOutboxBatch(count=0, first_sequence=None, last_sequence=None)

    runtime = db.scalar(
        select(RuntimeControlRecord).where(RuntimeControlRecord.scope == "global").with_for_update()
    )
    if runtime is None:
        raise LookupError("global runtime control is missing")
    if runtime.next_outbox_sequence < 1:
        raise RuntimeError("global outbox stream sequence is invalid")

    records = list(
        db.scalars(
            select(OutboxEventRecord)
            .where(OutboxEventRecord.stream_sequence.is_(None))
            .order_by(OutboxEventRecord.created_at, OutboxEventRecord.id)
            .limit(limit)
            .with_for_update()
        )
    )
    if not records:
        return SequencedOutboxBatch(count=0, first_sequence=None, last_sequence=None)

    first_sequence = runtime.next_outbox_sequence
    for record in records:
        record.stream_sequence = runtime.next_outbox_sequence
        runtime.next_outbox_sequence += 1
        if record.published_at is None:
            record.published_at = occurred_at
            record.attempts += 1
    db.flush()
    return SequencedOutboxBatch(
        count=len(records),
        first_sequence=first_sequence,
        last_sequence=runtime.next_outbox_sequence - 1,
    )
