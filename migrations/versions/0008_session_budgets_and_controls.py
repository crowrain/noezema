"""Add immutable session budgets and durable stop/abort intents."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_session_budgets_and_controls"
down_revision: str | None = "0007_durable_orchestrator_turns"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

DEFAULT_SESSION_BUDGET = {
    "cognitive_duration_seconds": 1500,
    "cognitive_reserve_input_tokens": 16384,
    "cognitive_reserve_model_turns": 2,
    "cognitive_reserve_output_tokens": 4096,
    "host_reserve_seconds": 300,
    "max_input_tokens": 131072,
    "max_model_turns": 32,
    "max_output_tokens": 32768,
    "max_tool_actions": 24,
}
DEFAULT_SESSION_BUDGET_JSON = (
    '{"cognitive_duration_seconds":1500,"cognitive_reserve_input_tokens":16384,'
    '"cognitive_reserve_model_turns":2,"cognitive_reserve_output_tokens":4096,'
    '"host_reserve_seconds":300,"max_input_tokens":131072,"max_model_turns":32,'
    '"max_output_tokens":32768,"max_tool_actions":24}'
)
# ``sa.text`` treats every ``:name`` fragment as a bind parameter, even inside
# the JSON string used by a DDL default. Escape the separators before handing
# the literal to SQLAlchemy; the compiler removes the escape characters when
# it renders the PostgreSQL statement.
DEFAULT_SESSION_BUDGET_SQL = DEFAULT_SESSION_BUDGET_JSON.replace(":", r"\:")
DEFAULT_SESSION_BUDGET_SHA256 = "2542123f9263cc1e3deace9a7fd28f2170364d798ab635ccba2fd9eeeab2d8a3"

AUDIT_EVENT_TYPES_V2 = (
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
)
AUDIT_EVENT_TYPES_V3 = (
    *AUDIT_EVENT_TYPES_V2,
    "SessionStopRequested",
    "SessionAbortRequested",
    "SessionBudgetExhausted",
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    # Alembic creates ``version_num`` as VARCHAR(32), while this revision and
    # several later descriptive identifiers are longer than 32 characters.
    # Widen it before Alembic records this revision after ``upgrade`` returns.
    op.alter_column(
        "alembic_version",
        "version_num",
        existing_type=sa.String(length=32),
        type_=sa.String(length=64),
        existing_nullable=False,
    )
    op.add_column(
        "sessions",
        sa.Column(
            "budget",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text(f"'{DEFAULT_SESSION_BUDGET_SQL}'::jsonb"),
        ),
    )
    op.add_column(
        "sessions",
        sa.Column(
            "budget_sha256",
            sa.String(length=64),
            nullable=False,
            server_default=DEFAULT_SESSION_BUDGET_SHA256,
        ),
    )
    op.add_column(
        "sessions",
        sa.Column("cognitive_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("host_deadline_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("stop_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("abort_requested_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("soft_exhausted_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("soft_exhaustion_reason", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("termination_reason", sa.String(length=256), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE sessions SET "
            "cognitive_deadline_at = created_at + INTERVAL '1500 seconds', "
            "host_deadline_at = created_at + INTERVAL '1800 seconds'"
        )
    )
    op.alter_column("sessions", "cognitive_deadline_at", nullable=False)
    op.alter_column("sessions", "host_deadline_at", nullable=False)
    op.alter_column("sessions", "budget", server_default=None)
    op.alter_column("sessions", "budget_sha256", server_default=None)
    op.create_check_constraint(
        "budget_sha256_length",
        "sessions",
        "length(budget_sha256) = 64",
    )
    op.create_check_constraint(
        "deadline_order",
        "sessions",
        "cognitive_deadline_at < host_deadline_at",
    )
    op.create_check_constraint(
        "soft_exhaustion_tuple_complete",
        "sessions",
        "(soft_exhausted_at IS NULL) = (soft_exhaustion_reason IS NULL)",
    )
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V3)})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V2)})",
    )
    op.drop_constraint(
        op.f("ck_sessions_soft_exhaustion_tuple_complete"),
        "sessions",
        type_="check",
    )
    op.drop_constraint(op.f("ck_sessions_deadline_order"), "sessions", type_="check")
    op.drop_constraint(op.f("ck_sessions_budget_sha256_length"), "sessions", type_="check")
    op.drop_column("sessions", "termination_reason")
    op.drop_column("sessions", "soft_exhaustion_reason")
    op.drop_column("sessions", "soft_exhausted_at")
    op.drop_column("sessions", "abort_requested_at")
    op.drop_column("sessions", "stop_requested_at")
    op.drop_column("sessions", "host_deadline_at")
    op.drop_column("sessions", "cognitive_deadline_at")
    op.drop_column("sessions", "budget_sha256")
    op.drop_column("sessions", "budget")
