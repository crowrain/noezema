"""Add the MVP FIFO question registry and immutable session binding."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_fifo_questions"
down_revision: str | None = "0001_operational_foundation"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

QUESTION_ORIGINS = (
    "seeded",
    "message",
    "contradiction",
    "unknown_term",
    "unverified_claim",
    "experiment_result",
    "local_corpus",
    "model",
    "invalid_assessment",
)
QUESTION_STATES = ("queued", "answered", "discarded")
AUDIT_EVENT_TYPES_V1 = (
    "SessionStateChanged",
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
AUDIT_EVENT_TYPES_V2 = ("SessionStateChanged", "QuestionSelected", *AUDIT_EVENT_TYPES_V1[1:])


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "questions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(length=32), nullable=False),
        sa.Column(
            "origin_config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            nullable=False,
        ),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "parent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("questions.id"),
            nullable=True,
        ),
        sa.Column("score_components", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("embedding_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("length(text) BETWEEN 1 AND 4096", name="text_length"),
        sa.CheckConstraint(f"origin IN ({_in(QUESTION_ORIGINS)})", name="origin_allowed"),
        sa.CheckConstraint(f"state IN ({_in(QUESTION_STATES)})", name="state_allowed"),
        sa.CheckConstraint("priority BETWEEN -1000 AND 1000", name="priority_range"),
    )
    op.create_index(
        "ix_questions_fifo",
        "questions",
        ["state", "created_at", "id"],
        unique=False,
    )
    op.add_column(
        "sessions",
        sa.Column("question_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sessions_question_id_questions",
        "sessions",
        "questions",
        ["question_id"],
        ["id"],
    )

    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V2)})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V1)})",
    )

    op.drop_constraint("fk_sessions_question_id_questions", "sessions", type_="foreignkey")
    op.drop_column("sessions", "question_id")
    op.drop_index("ix_questions_fifo", table_name="questions")
    op.drop_table("questions")
