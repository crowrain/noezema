"""Contracts keep human text separate from typed operator authority."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from packages.domain import (
    InboxIdempotencyKey,
    OperatorCommandDraft,
    OperatorCommandState,
    OperatorCommandType,
    SessionId,
    UserMessageDraft,
)

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def test_message_contract_requires_bounded_future_expiry() -> None:
    draft = UserMessageDraft.new(
        idempotency_key=InboxIdempotencyKey.new(),
        sender="owner",
        body="Проверь, почему локальный индекс не обновился.",
        created_at=NOW,
        expires_at=NOW + timedelta(hours=24),
        priority=25,
    )

    assert str(draft.id) != str(draft.idempotency_key)
    assert draft.priority == 25

    with pytest.raises(ValidationError, match="expiry must be after creation"):
        UserMessageDraft.new(
            idempotency_key=InboxIdempotencyKey.new(),
            sender="owner",
            body="Too late",
            created_at=NOW,
            expires_at=NOW,
        )


def test_stop_and_abort_require_an_explicit_session_target() -> None:
    session_id = SessionId.new()
    command = OperatorCommandDraft.new(
        idempotency_key=InboxIdempotencyKey.new(),
        actor_id="owner",
        type=OperatorCommandType.STOP_GRACEFULLY,
        session_id=session_id,
        reason="Need the GPU",
        created_at=NOW,
    )

    assert command.session_id == session_id

    with pytest.raises(ValidationError, match="session target"):
        OperatorCommandDraft.new(
            idempotency_key=InboxIdempotencyKey.new(),
            actor_id="owner",
            type=OperatorCommandType.ABORT_SESSION,
            reason="Emergency",
            created_at=NOW,
        )
    with pytest.raises(ValidationError, match="session target"):
        OperatorCommandDraft.new(
            idempotency_key=InboxIdempotencyKey.new(),
            actor_id="owner",
            type=OperatorCommandType.PAUSE,
            session_id=session_id,
            reason="Pause future wakes",
            created_at=NOW,
        )


def test_only_terminal_operator_command_states_are_terminal() -> None:
    assert not OperatorCommandState.ACCEPTED.is_terminal
    assert not OperatorCommandState.WAITING_SAFE_BOUNDARY.is_terminal
    assert OperatorCommandState.REJECTED.is_terminal
    assert OperatorCommandState.COMPLETED.is_terminal
    assert OperatorCommandState.FAILED.is_terminal
