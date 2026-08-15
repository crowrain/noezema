"""Operator commands are durable intents applied only by the orchestrator."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import (
    SafeBoundaryKind,
    SessionStarted,
    WakeSkipped,
    WakeSkipReason,
    apply_session_safe_boundary,
    claim_session,
    dispatch_operator_command,
    reconcile_operator_commands_for_session,
    start_next_session,
    transition_session_phase,
)
from packages.cognition import enqueue_question
from packages.domain import (
    ConfigSnapshotId,
    InboxIdempotencyKey,
    NodeState,
    OperatorCommandDraft,
    OperatorCommandState,
    OperatorCommandType,
    QuestionDraft,
    SessionId,
    SessionState,
)
from packages.persistence import BOOTSTRAP_CONFIG_SNAPSHOT_ID, submit_operator_command
from packages.persistence.models import RuntimeControlRecord, SessionRecord

NOW = datetime(2026, 8, 15, 12, 0, tzinfo=UTC)


def _seed_question(db: Session, text: str = "Why does the index lag?") -> None:
    enqueue_question(
        db,
        QuestionDraft.seeded(
            text=text,
            origin_config_snapshot_id=ConfigSnapshotId(root=BOOTSTRAP_CONFIG_SNAPSHOT_ID),
            created_at=NOW,
        ),
    )


def _command(
    type: OperatorCommandType,
    *,
    session_id: SessionId | None = None,
    reason: str = "operator test",
) -> OperatorCommandDraft:
    return OperatorCommandDraft.new(
        idempotency_key=InboxIdempotencyKey.new(),
        actor_id="owner",
        type=type,
        session_id=session_id,
        reason=reason,
        created_at=NOW,
    )


def _advance_to_exploring(
    session_factory: sessionmaker[Session],
    session_id: SessionId,
) -> None:
    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner="worker-a/incarnation-1",
            ttl_seconds=300,
            occurred_at=NOW,
        )
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.ORIENTING,
            reason="wake",
            occurred_at=NOW,
        )
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.PLANNING,
            reason="orient",
            occurred_at=NOW,
        )
        transition_session_phase(
            db,
            lease=claimed.lease,
            target=SessionState.EXPLORING,
            reason="plan",
            occurred_at=NOW,
        )


def test_pause_and_resume_gate_new_session_admission(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        _seed_question(db)
        pause = submit_operator_command(db, _command(OperatorCommandType.PAUSE))
        applied = dispatch_operator_command(db, command_id=pause.id, occurred_at=NOW)
        skipped = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)

    assert applied.state is OperatorCommandState.COMPLETED
    assert isinstance(skipped, WakeSkipped)
    assert skipped.reason is WakeSkipReason.OPERATOR_PAUSED
    with session_factory.begin() as db:
        resume = submit_operator_command(db, _command(OperatorCommandType.RESUME))
        dispatch_operator_command(db, command_id=resume.id, occurred_at=NOW)
        started = start_next_session(db, session_id=SessionId.new(), occurred_at=NOW)

    assert isinstance(started, SessionStarted)
    with session_factory() as db:
        runtime = db.get(RuntimeControlRecord, "global")
        assert runtime is not None and runtime.node_state == NodeState.SLEEPING.value


def test_wake_now_is_rejected_while_paused_and_increments_when_resumed(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        pause = submit_operator_command(db, _command(OperatorCommandType.PAUSE))
        dispatch_operator_command(db, command_id=pause.id, occurred_at=NOW)
        wake = submit_operator_command(db, _command(OperatorCommandType.WAKE_NOW))
        rejected = dispatch_operator_command(db, command_id=wake.id, occurred_at=NOW)
    assert rejected.state is OperatorCommandState.REJECTED
    assert rejected.result == {"node_state": "paused"}

    with session_factory.begin() as db:
        resume = submit_operator_command(db, _command(OperatorCommandType.RESUME))
        dispatch_operator_command(db, command_id=resume.id, occurred_at=NOW)
        wake = submit_operator_command(db, _command(OperatorCommandType.WAKE_NOW))
        completed = dispatch_operator_command(db, command_id=wake.id, occurred_at=NOW)
    assert completed.state is OperatorCommandState.COMPLETED
    assert completed.result is not None and completed.result["wake_generation"] == 1


def test_graceful_stop_waits_for_and_reconciles_the_safe_boundary(
    session_factory: sessionmaker[Session],
) -> None:
    session_id = SessionId.new()
    with session_factory.begin() as db:
        _seed_question(db)
        started = start_next_session(db, session_id=session_id, occurred_at=NOW)
        assert isinstance(started, SessionStarted)
    _advance_to_exploring(session_factory, session_id)

    with session_factory.begin() as db:
        submitted = submit_operator_command(
            db,
            _command(OperatorCommandType.STOP_GRACEFULLY, session_id=session_id),
        )
        waiting = dispatch_operator_command(db, command_id=submitted.id, occurred_at=NOW)
        session = db.get(SessionRecord, session_id.root)
        assert session is not None and session.stop_requested_at is not None
    assert waiting.state is OperatorCommandState.WAITING_SAFE_BOUNDARY

    with session_factory.begin() as db:
        claimed = claim_session(
            db,
            session_id=session_id,
            owner="worker-a/incarnation-1",
            ttl_seconds=300,
            occurred_at=NOW,
        )
        boundary = apply_session_safe_boundary(db, lease=claimed.lease, occurred_at=NOW)
        reconciled = reconcile_operator_commands_for_session(
            db,
            session_id=session_id,
            occurred_at=NOW,
        )

    assert boundary.kind is SafeBoundaryKind.STOPPING
    assert len(reconciled) == 1
    assert reconciled[0].state is OperatorCommandState.COMPLETED
    assert reconciled[0].result == {
        "session_state": SessionState.STOPPING.value,
        "safe_boundary_reached": True,
    }


def test_future_operator_controls_are_typed_but_rejected_by_mvp_dispatcher(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as db:
        submitted = submit_operator_command(db, _command(OperatorCommandType.SET_BUDGET))
        result = dispatch_operator_command(db, command_id=submitted.id, occurred_at=NOW)

    assert result.state is OperatorCommandState.REJECTED
