"""Tests for canonical lifecycle enums and audit events."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from packages.domain import AuditEvent, EventType, SessionId, SessionState


def test_session_enum_matches_canonical_architecture_order() -> None:
    assert [state.value for state in SessionState] == [
        "created",
        "waking",
        "orienting",
        "selecting_question",
        "planning",
        "exploring",
        "verifying",
        "stopping",
        "consolidating",
        "reporting",
        "committing",
        "reconciling_commit",
        "aborting",
        "succeeded",
        "succeeded_partial",
        "failed",
        "cancelled",
    ]


@pytest.mark.parametrize(
    "state",
    [
        SessionState.SUCCEEDED,
        SessionState.SUCCEEDED_PARTIAL,
        SessionState.FAILED,
        SessionState.CANCELLED,
    ],
)
def test_only_terminal_session_states_are_marked_terminal(state: SessionState) -> None:
    assert state.is_terminal
    assert not SessionState.RECONCILING_COMMIT.is_terminal


def test_audit_event_requires_positive_sequence_and_aware_time() -> None:
    event = AuditEvent.new(
        session_id=SessionId.new(),
        sequence=1,
        type=EventType.ACTION_PROPOSED,
        occurred_at=datetime.now(UTC),
        actor="tool_broker",
        public_summary="Action accepted for policy evaluation",
        payload={"tool": "workspace.read"},
    )

    assert event.schema_version == 1
    assert event.sequence == 1
    assert event.model_dump(mode="json")["id"] == str(event.id)

    with pytest.raises(ValidationError, match="greater than 0"):
        AuditEvent.new(
            session_id=None,
            sequence=0,
            type=EventType.SESSION_STATE_CHANGED,
            occurred_at=datetime.now(UTC),
            actor="orchestrator",
            public_summary="Invalid event",
        )

    with pytest.raises(ValidationError, match="timezone info"):
        AuditEvent.new(
            session_id=None,
            sequence=1,
            type=EventType.SESSION_STATE_CHANGED,
            occurred_at=datetime.now(),
            actor="orchestrator",
            public_summary="Invalid event",
        )
