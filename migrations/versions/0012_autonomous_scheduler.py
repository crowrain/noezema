"""Add durable single-node scheduler ownership, cadence and backoff state."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0012_autonomous_scheduler"
down_revision: str | None = "0011_outbox_stream_sequence"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_EVENT_TYPES_V4 = (
    "SessionStateChanged",
    "QuestionSelected",
    "ModelRunCompleted",
    "ActionProposed",
    "PolicyEvaluated",
    "ActionAccepted",
    "ActionStarted",
    "ActionCompleted",
    "ActionFailed",
    "ActionOutcomeUnknown",
    "CommitAttemptPrepared",
    "CommitAttemptReconciled",
    "SessionStopRequested",
    "SessionAbortRequested",
    "SessionBudgetExhausted",
    "MessageQueued",
    "MessageDelivered",
    "MessageAcknowledged",
    "MessageAnswered",
    "MessageExpired",
    "OperatorCommandAccepted",
    "OperatorCommandStateChanged",
)
AUDIT_EVENT_TYPES_V5 = (
    *AUDIT_EVENT_TYPES_V4,
    "SchedulerWakeStarted",
    "SchedulerWakeFinished",
)
TERMINAL_SESSION_STATES = ("succeeded", "succeeded_partial", "failed", "cancelled")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_lease_owner", sa.String(length=128), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_lease_expires_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column(
            "scheduler_fence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("runtime_controls", "scheduler_fence", server_default=None)
    op.add_column(
        "runtime_controls",
        sa.Column(
            "scheduler_last_handled_wake_generation",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "runtime_controls",
        "scheduler_last_handled_wake_generation",
        server_default=None,
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_next_scheduled_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_backoff_until", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column(
            "scheduler_consecutive_failures",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column(
        "runtime_controls",
        "scheduler_consecutive_failures",
        server_default=None,
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_last_session_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_last_terminal_state", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "runtime_controls",
        sa.Column("scheduler_last_error_class", sa.String(length=128), nullable=True),
    )
    op.create_check_constraint(
        "scheduler_fence_nonnegative",
        "runtime_controls",
        "scheduler_fence >= 0",
    )
    op.create_check_constraint(
        "scheduler_wake_generation_valid",
        "runtime_controls",
        "scheduler_last_handled_wake_generation >= 0 AND "
        "scheduler_last_handled_wake_generation <= wake_generation",
    )
    op.create_check_constraint(
        "scheduler_failures_nonnegative",
        "runtime_controls",
        "scheduler_consecutive_failures >= 0",
    )
    op.create_check_constraint(
        "scheduler_lease_tuple_complete",
        "runtime_controls",
        "(scheduler_lease_owner IS NULL) = (scheduler_lease_expires_at IS NULL)",
    )
    op.create_check_constraint(
        "scheduler_terminal_state_allowed",
        "runtime_controls",
        "scheduler_last_terminal_state IS NULL OR "
        f"scheduler_last_terminal_state IN ({_in(TERMINAL_SESSION_STATES)})",
    )
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V5)})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V4)})",
    )
    op.drop_constraint(
        op.f("ck_runtime_controls_scheduler_terminal_state_allowed"),
        "runtime_controls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_controls_scheduler_lease_tuple_complete"),
        "runtime_controls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_controls_scheduler_failures_nonnegative"),
        "runtime_controls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_controls_scheduler_wake_generation_valid"),
        "runtime_controls",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_runtime_controls_scheduler_fence_nonnegative"),
        "runtime_controls",
        type_="check",
    )
    op.drop_column("runtime_controls", "scheduler_last_error_class")
    op.drop_column("runtime_controls", "scheduler_last_terminal_state")
    op.drop_column("runtime_controls", "scheduler_last_session_id")
    op.drop_column("runtime_controls", "scheduler_consecutive_failures")
    op.drop_column("runtime_controls", "scheduler_backoff_until")
    op.drop_column("runtime_controls", "scheduler_next_scheduled_at")
    op.drop_column("runtime_controls", "scheduler_last_handled_wake_generation")
    op.drop_column("runtime_controls", "scheduler_fence")
    op.drop_column("runtime_controls", "scheduler_lease_expires_at")
    op.drop_column("runtime_controls", "scheduler_lease_owner")
