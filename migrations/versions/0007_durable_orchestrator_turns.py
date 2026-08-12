"""Add resumable orchestrator model-turn intents and outcomes."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_durable_orchestrator_turns"
down_revision: str | None = "0006_concurrency_control"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TURN_PHASES = ("planning", "exploration", "verification", "consolidation")
TURN_STATUSES = ("prepared", "completed", "failed")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "orchestrator_turns",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("phase", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("lease_owner", sa.String(length=128), nullable=False),
        sa.Column("session_fence", sa.BigInteger(), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("request_sha256", sa.String(length=64), nullable=False),
        sa.Column("response_schema_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_runs.id"),
            nullable=True,
        ),
        sa.Column(
            "result",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        sa.CheckConstraint("session_fence >= 1", name="session_fence_positive"),
        sa.CheckConstraint(f"phase IN ({_in(TURN_PHASES)})", name="phase_allowed"),
        sa.CheckConstraint(f"status IN ({_in(TURN_STATUSES)})", name="status_allowed"),
        sa.CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        sa.CheckConstraint(
            "length(response_schema_sha256) = 64",
            name="schema_sha256_length",
        ),
        sa.CheckConstraint(
            "(status = 'prepared' AND model_run_id IS NULL AND result IS NULL "
            "AND error_code IS NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND model_run_id IS NOT NULL AND result IS NOT NULL "
            "AND error_code IS NULL AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND model_run_id IS NULL AND result IS NULL "
            "AND error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="outcome_tuple_complete",
        ),
        sa.UniqueConstraint(
            "session_id",
            "ordinal",
            name="uq_orchestrator_turns_session_id_ordinal",
        ),
        sa.UniqueConstraint("model_run_id", name="uq_orchestrator_turns_model_run_id"),
    )
    op.create_index(
        "ix_orchestrator_turns_session_status",
        "orchestrator_turns",
        ["session_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("orchestrator_turns")
