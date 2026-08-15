"""Add durable human-message and typed operator-command inboxes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_human_input_and_operator_inbox"
down_revision: str | None = "0008_session_budgets_and_controls"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MESSAGE_STATES = (
    "created",
    "queued",
    "delivered",
    "acknowledged",
    "answered",
    "expired",
)
OPERATOR_COMMAND_TYPES = (
    "pause",
    "resume",
    "wake_now",
    "stop_gracefully",
    "abort_session",
    "set_budget",
    "set_access_profile",
    "restore_checkpoint",
)
OPERATOR_COMMAND_STATES = (
    "accepted",
    "rejected",
    "waiting_safe_boundary",
    "executing",
    "completed",
    "failed",
)
AUDIT_EVENT_TYPES_V3 = (
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
)
AUDIT_EVENT_TYPES_V4 = (
    *AUDIT_EVENT_TYPES_V3,
    "MessageQueued",
    "MessageDelivered",
    "MessageAcknowledged",
    "MessageAnswered",
    "MessageExpired",
    "OperatorCommandAccepted",
    "OperatorCommandStateChanged",
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "runtime_controls",
        sa.Column("scope", sa.String(length=16), nullable=False),
        sa.Column("node_state", sa.String(length=16), nullable=False),
        sa.Column("wake_generation", sa.BigInteger(), nullable=False),
        sa.Column("next_global_audit_sequence", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope = 'global'", name="scope_global"),
        sa.CheckConstraint("node_state IN ('sleeping', 'paused')", name="node_state_allowed"),
        sa.CheckConstraint("wake_generation >= 0", name="wake_generation_nonnegative"),
        sa.CheckConstraint(
            "next_global_audit_sequence >= 1",
            name="next_global_audit_sequence_positive",
        ),
        sa.PrimaryKeyConstraint("scope"),
    )
    op.execute(
        sa.text(
            "INSERT INTO runtime_controls "
            "(scope, node_state, wake_generation, next_global_audit_sequence, updated_at) "
            "VALUES ('global', 'sleeping', 0, 1, CURRENT_TIMESTAMP)"
        )
    )
    op.create_table(
        "messages",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sender", sa.String(length=256), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("response_audit_event_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("length(sender) BETWEEN 1 AND 256", name="sender_length"),
        sa.CheckConstraint("length(body) BETWEEN 1 AND 4096", name="body_length"),
        sa.CheckConstraint("priority BETWEEN -1000 AND 1000", name="priority_range"),
        sa.CheckConstraint(f"state IN ({_in(MESSAGE_STATES)})", name="state_allowed"),
        sa.CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        sa.CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        sa.ForeignKeyConstraint(["delivered_session_id"], ["sessions.id"]),
        sa.ForeignKeyConstraint(["question_id"], ["questions.id"]),
        sa.ForeignKeyConstraint(["response_audit_event_id"], ["audit_events.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
        sa.UniqueConstraint("question_id"),
    )
    op.create_index(
        "ix_messages_delivery",
        "messages",
        ["state", "priority", "created_at", "id"],
        unique=False,
    )
    op.create_table(
        "operator_commands",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "arguments",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("reason", sa.String(length=256), nullable=False),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(actor_id) BETWEEN 1 AND 256", name="actor_id_length"),
        sa.CheckConstraint("length(reason) BETWEEN 1 AND 256", name="reason_length"),
        sa.CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        sa.CheckConstraint(f"type IN ({_in(OPERATOR_COMMAND_TYPES)})", name="type_allowed"),
        sa.CheckConstraint(
            f"state IN ({_in(OPERATOR_COMMAND_STATES)})",
            name="state_allowed",
        ),
        sa.CheckConstraint(
            "(type IN ('stop_gracefully', 'abort_session') AND session_id IS NOT NULL) OR "
            "(type NOT IN ('stop_gracefully', 'abort_session') AND session_id IS NULL)",
            name="session_target_shape",
        ),
        sa.ForeignKeyConstraint(["session_id"], ["sessions.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("idempotency_key"),
    )
    op.create_index(
        "ix_operator_commands_dispatch",
        "operator_commands",
        ["state", "created_at", "id"],
        unique=False,
    )
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V4)})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V3)})",
    )
    op.drop_index("ix_operator_commands_dispatch", table_name="operator_commands")
    op.drop_table("operator_commands")
    op.drop_index("ix_messages_delivery", table_name="messages")
    op.drop_table("messages")
    op.drop_table("runtime_controls")
