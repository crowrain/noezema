"""Replay, privacy and wire-format tests for the public timeline SSE stream."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from apps.web import (
    InvalidStreamPositionError,
    TimelineStreamService,
    encode_timeline_sse,
    parse_stream_position,
)
from packages.domain import EventType
from packages.persistence import append_global_audit
from packages.persistence.models import AuditEventRecord, OutboxEventRecord

NOW = datetime(2026, 8, 21, 13, 0, tzinfo=UTC)


def test_stream_replays_public_projections_without_outbox_payload(
    session_factory: sessionmaker[Session],
) -> None:
    event_ids = []
    with session_factory.begin() as db:
        for ordinal in range(3):
            appended = append_global_audit(
                db,
                type=EventType.MODEL_RUN_COMPLETED,
                occurred_at=NOW + timedelta(seconds=ordinal),
                actor="orchestrator",
                public_summary=f"safe summary {ordinal}",
                topic="audit.test.v1",
                payload={"chain_of_thought": f"secret {ordinal}"},
            )
            event_ids.append(appended.audit_event.id.root)
        hidden = db.get(AuditEventRecord, event_ids[1])
        assert hidden is not None
        hidden.visibility = "private"

    service = TimelineStreamService(session_factory, clock=lambda: NOW + timedelta(minutes=1))
    first = service.poll(after_sequence=0)

    assert [event.id for event in first] == [event_ids[0], event_ids[2]]
    assert [event.stream_sequence for event in first] == [1, 3]
    assert service.current_position() == 3
    assert not hasattr(first[0], "payload")
    assert "secret" not in str(first)

    resumed = service.poll(after_sequence=first[0].stream_sequence)
    assert [event.id for event in resumed] == [event_ids[2]]
    assert service.poll(after_sequence=3) == ()

    with session_factory() as db:
        records = list(
            db.scalars(select(OutboxEventRecord).order_by(OutboxEventRecord.stream_sequence))
        )
    assert [record.stream_sequence for record in records] == [1, 2, 3]
    assert [record.attempts for record in records] == [1, 1, 1]

    encoded = encode_timeline_sse(first[0])
    lines = encoded.rstrip("\n").splitlines()
    assert lines[:2] == ["id: 1", "event: timeline"]
    wire_payload = json.loads(lines[2].removeprefix("data: "))
    assert set(wire_payload) == {
        "actor",
        "id",
        "occurred_at",
        "schema_version",
        "sequence",
        "session_id",
        "stream_sequence",
        "summary",
        "type",
    }
    assert "chain_of_thought" not in encoded


@pytest.mark.parametrize("value", ["", "-1", "+1", "1.0", "abc", "１２"])
def test_last_event_id_must_be_a_canonical_nonnegative_decimal(value: str) -> None:
    with pytest.raises(InvalidStreamPositionError, match="Last-Event-ID"):
        parse_stream_position(value, None)


def test_resume_position_rejects_conflicting_sources() -> None:
    assert parse_stream_position(None, None) == 0
    assert parse_stream_position("0", None) == 0
    assert parse_stream_position("42", 42) == 42
    assert parse_stream_position(None, 7) == 7
    with pytest.raises(InvalidStreamPositionError, match="conflicting"):
        parse_stream_position("7", 8)
