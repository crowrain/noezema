"""Trusted dispatcher for the durable operator-command inbox."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from apps.orchestrator.runtime import (
    OperatorControlRejectedError,
    request_graceful_stop,
    request_session_abort,
)
from packages.domain import (
    EventType,
    NodeState,
    OperatorCommandId,
    OperatorCommandResult,
    OperatorCommandState,
    OperatorCommandType,
    SessionId,
    SessionState,
)
from packages.persistence import append_global_audit
from packages.persistence.models import (
    OperatorCommandRecord,
    RuntimeControlRecord,
    SessionRecord,
)

_STOP_APPLIED_STATES = {
    SessionState.STOPPING,
    SessionState.CONSOLIDATING,
    SessionState.REPORTING,
    SessionState.SUCCEEDED,
    SessionState.SUCCEEDED_PARTIAL,
}


def dispatch_next_operator_command(
    db: Session,
    *,
    occurred_at: datetime,
    session_id: SessionId | None = None,
) -> OperatorCommandResult | None:
    """Apply the oldest accepted command, optionally restricted to one session."""

    statement = (
        select(OperatorCommandRecord)
        .where(OperatorCommandRecord.state == OperatorCommandState.ACCEPTED.value)
        .order_by(OperatorCommandRecord.created_at, OperatorCommandRecord.id)
        .limit(1)
        .with_for_update(of=OperatorCommandRecord, skip_locked=True)
    )
    if session_id is not None:
        statement = statement.where(OperatorCommandRecord.session_id == session_id.root)
    record = db.scalar(statement)
    if record is None:
        return None
    return _dispatch_locked(db, record=record, occurred_at=occurred_at)


def dispatch_operator_command(
    db: Session,
    *,
    command_id: OperatorCommandId,
    occurred_at: datetime,
) -> OperatorCommandResult:
    record = db.scalar(
        select(OperatorCommandRecord)
        .where(OperatorCommandRecord.id == command_id.root)
        .with_for_update()
    )
    if record is None:
        raise LookupError(f"operator command does not exist: {command_id}")
    if record.state != OperatorCommandState.ACCEPTED.value:
        return _result(record)
    return _dispatch_locked(db, record=record, occurred_at=occurred_at)


def reconcile_operator_commands_for_session(
    db: Session,
    *,
    session_id: SessionId,
    occurred_at: datetime,
) -> tuple[OperatorCommandResult, ...]:
    """Resolve controls after the session crosses their requested safe boundary."""

    session = db.get(SessionRecord, session_id.root)
    if session is None:
        raise LookupError(f"session does not exist: {session_id}")
    state = SessionState(session.state)
    records = tuple(
        db.scalars(
            select(OperatorCommandRecord)
            .where(
                OperatorCommandRecord.session_id == session_id.root,
                OperatorCommandRecord.state == OperatorCommandState.WAITING_SAFE_BOUNDARY.value,
            )
            .order_by(OperatorCommandRecord.created_at, OperatorCommandRecord.id)
            .with_for_update()
        )
    )
    resolved: list[OperatorCommandResult] = []
    for record in records:
        command = OperatorCommandType(record.type)
        if command is OperatorCommandType.STOP_GRACEFULLY and state in _STOP_APPLIED_STATES:
            _transition(
                db,
                record=record,
                target=OperatorCommandState.COMPLETED,
                occurred_at=occurred_at,
                result={"session_state": state.value, "safe_boundary_reached": True},
            )
        elif command is OperatorCommandType.ABORT_SESSION and state is SessionState.CANCELLED:
            _transition(
                db,
                record=record,
                target=OperatorCommandState.COMPLETED,
                occurred_at=occurred_at,
                result={"session_state": state.value, "safe_boundary_reached": True},
            )
        elif state.is_terminal:
            _transition(
                db,
                record=record,
                target=OperatorCommandState.FAILED,
                occurred_at=occurred_at,
                result={"session_state": state.value, "safe_boundary_reached": False},
                error_code="session_terminated_before_control",
            )
        else:
            continue
        resolved.append(_result(record))
    return tuple(resolved)


def _dispatch_locked(
    db: Session,
    *,
    record: OperatorCommandRecord,
    occurred_at: datetime,
) -> OperatorCommandResult:
    _transition(
        db,
        record=record,
        target=OperatorCommandState.EXECUTING,
        occurred_at=occurred_at,
        result=None,
    )
    command = OperatorCommandType(record.type)
    if command in {
        OperatorCommandType.PAUSE,
        OperatorCommandType.RESUME,
        OperatorCommandType.WAKE_NOW,
    }:
        return _dispatch_node_control(db, record=record, command=command, occurred_at=occurred_at)
    if command in {
        OperatorCommandType.STOP_GRACEFULLY,
        OperatorCommandType.ABORT_SESSION,
    }:
        assert record.session_id is not None
        try:
            control = (
                request_graceful_stop(
                    db,
                    session_id=SessionId(root=record.session_id),
                    actor=record.actor_id,
                    requested_at=occurred_at,
                )
                if command is OperatorCommandType.STOP_GRACEFULLY
                else request_session_abort(
                    db,
                    session_id=SessionId(root=record.session_id),
                    actor=record.actor_id,
                    requested_at=occurred_at,
                )
            )
        except (LookupError, OperatorControlRejectedError) as exc:
            _transition(
                db,
                record=record,
                target=OperatorCommandState.REJECTED,
                occurred_at=occurred_at,
                result=None,
                error_code=type(exc).__name__,
            )
            return _result(record)
        _transition(
            db,
            record=record,
            target=OperatorCommandState.WAITING_SAFE_BOUNDARY,
            occurred_at=occurred_at,
            result={
                "session_state": control.state.value,
                "requested_at": control.requested_at.isoformat(),
                "newly_recorded": control.newly_recorded,
            },
        )
        return _result(record)

    _transition(
        db,
        record=record,
        target=OperatorCommandState.REJECTED,
        occurred_at=occurred_at,
        result=None,
        error_code="unsupported_in_mvp",
    )
    return _result(record)


def _dispatch_node_control(
    db: Session,
    *,
    record: OperatorCommandRecord,
    command: OperatorCommandType,
    occurred_at: datetime,
) -> OperatorCommandResult:
    runtime = db.scalar(
        select(RuntimeControlRecord).where(RuntimeControlRecord.scope == "global").with_for_update()
    )
    if runtime is None:
        raise LookupError("global runtime control is missing")
    previous = NodeState(runtime.node_state)
    if command is OperatorCommandType.WAKE_NOW:
        if previous is NodeState.PAUSED:
            _transition(
                db,
                record=record,
                target=OperatorCommandState.REJECTED,
                occurred_at=occurred_at,
                result={"node_state": previous.value},
                error_code="node_paused",
            )
            return _result(record)
        runtime.wake_generation += 1
        result = {"node_state": previous.value, "wake_generation": runtime.wake_generation}
    else:
        target = NodeState.PAUSED if command is OperatorCommandType.PAUSE else NodeState.SLEEPING
        runtime.node_state = target.value
        result = {
            "previous_node_state": previous.value,
            "node_state": target.value,
            "changed": previous is not target,
        }
    runtime.updated_at = occurred_at
    _transition(
        db,
        record=record,
        target=OperatorCommandState.COMPLETED,
        occurred_at=occurred_at,
        result=result,
    )
    return _result(record)


def _transition(
    db: Session,
    *,
    record: OperatorCommandRecord,
    target: OperatorCommandState,
    occurred_at: datetime,
    result: dict[str, object] | None,
    error_code: str | None = None,
) -> None:
    previous = OperatorCommandState(record.state)
    record.state = target.value
    record.result = result
    record.error_code = error_code
    record.updated_at = occurred_at
    record.finished_at = occurred_at if target.is_terminal else None
    append_global_audit(
        db,
        type=EventType.OPERATOR_COMMAND_STATE_CHANGED,
        occurred_at=occurred_at,
        actor="orchestrator",
        public_summary=f"Operator command {record.type}: {target.value}",
        topic="audit.operator_command_state_changed.v1",
        payload={
            "command_id": str(record.id),
            "command": record.type,
            "from": previous.value,
            "to": target.value,
            "error_code": error_code,
        },
    )
    db.flush()


def _result(record: OperatorCommandRecord) -> OperatorCommandResult:
    return OperatorCommandResult(
        id=OperatorCommandId(root=record.id),
        type=OperatorCommandType(record.type),
        state=OperatorCommandState(record.state),
        result=record.result,
    )
