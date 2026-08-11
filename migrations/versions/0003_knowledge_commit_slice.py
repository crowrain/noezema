"""Add the first assessed-knowledge staging and commit slice."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003_knowledge_commit_slice"
down_revision: str | None = "0002_fifo_questions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

CLAIM_TYPES = (
    "local_observation",
    "computed_result",
    "formal_theorem",
    "empirical_conjecture",
    "procedural",
    "external_fact",
    "temporal_fact",
    "self_model",
)
EVIDENCE_KINDS = (
    "source_assertion",
    "quote_integrity",
    "experiment_run",
    "computation",
    "formal_check",
    "local_observation",
)
EPISTEMIC_STATUSES = ("hypothesis", "supported", "disputed", "refuted", "deferred")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.create_table(
        "commit_attempts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("validated_knowledge_revision", sa.BigInteger(), nullable=False),
        sa.Column("validated_dependency_graph_revision", sa.BigInteger(), nullable=False),
        sa.Column("staging_hash", sa.String(length=64), nullable=False),
        sa.Column("terminal_state", sa.String(length=32), nullable=False),
        sa.Column("prepared_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "status IN ('prepared', 'reconciling', 'committed', 'aborted')",
            name="status_allowed",
        ),
        sa.CheckConstraint(
            "validated_knowledge_revision >= 0",
            name="knowledge_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "validated_dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "terminal_state IN ('succeeded', 'succeeded_partial')",
            name="terminal_state_allowed",
        ),
        sa.UniqueConstraint("session_id", name="uq_commit_attempts_session_id"),
    )
    op.add_column(
        "sessions",
        sa.Column("commit_attempt_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_sessions_commit_attempt_id_commit_attempts",
        "sessions",
        "commit_attempts",
        ["commit_attempt_id"],
        ["id"],
    )
    op.create_table(
        "session_staging",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "attempt_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commit_attempts.id"),
            nullable=False,
        ),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("operation", sa.String(length=16), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("validation_status", sa.String(length=16), nullable=False),
        sa.Column("validated_knowledge_revision", sa.BigInteger(), nullable=False),
        sa.Column("validated_dependency_graph_revision", sa.BigInteger(), nullable=False),
        sa.Column("validation_rules_hash", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("aggregate_type = 'knowledge_batch'", name="aggregate_type_allowed"),
        sa.CheckConstraint("operation = 'insert'", name="operation_allowed"),
        sa.CheckConstraint("schema_version = 1", name="schema_version_supported"),
        sa.CheckConstraint("validation_status = 'verified'", name="validation_status_allowed"),
        sa.CheckConstraint(
            "validated_knowledge_revision >= 0",
            name="knowledge_revision_nonnegative",
        ),
        sa.CheckConstraint(
            "validated_dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        sa.UniqueConstraint("attempt_id", name="uq_session_staging_attempt_id"),
    )
    op.create_table(
        "claims",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("claim_type", sa.String(length=32), nullable=False),
        sa.Column("freshness_status", sa.String(length=16), nullable=False),
        sa.Column("as_of", sa.DateTime(timezone=True), nullable=True),
        sa.Column("observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reverify_after", sa.DateTime(timezone=True), nullable=True),
        sa.Column("topic", sa.String(length=256), nullable=False),
        sa.Column(
            "created_in_session",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.CheckConstraint("length(statement) BETWEEN 1 AND 4096", name="statement_length"),
        sa.CheckConstraint(f"claim_type IN ({_in(CLAIM_TYPES)})", name="claim_type_allowed"),
        sa.CheckConstraint(
            "freshness_status IN ('fresh', 'due', 'stale', 'unknown')",
            name="freshness_status_allowed",
        ),
    )
    op.create_table(
        "evidence",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column("relation", sa.String(length=16), nullable=False),
        sa.Column("evidence_kind", sa.String(length=32), nullable=False),
        sa.Column("identity_hash", sa.String(length=64), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("observation", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("covered_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("integrity_checked", sa.Boolean(), nullable=False),
        sa.Column("independence_group", sa.String(length=256), nullable=True),
        sa.Column("successful", sa.Boolean(), nullable=True),
        sa.Column(
            "created_in_session",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.CheckConstraint("relation IN ('support', 'counter')", name="relation_allowed"),
        sa.CheckConstraint(
            f"evidence_kind IN ({_in(EVIDENCE_KINDS)})",
            name="evidence_kind_allowed",
        ),
        sa.UniqueConstraint(
            "claim_id",
            "evidence_kind",
            "identity_hash",
            name="uq_evidence_claim_kind_identity",
        ),
    )
    op.create_table(
        "claim_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            nullable=False,
        ),
        sa.Column("effective_grade", sa.String(length=2), nullable=False),
        sa.Column("epistemic_status", sa.String(length=16), nullable=False),
        sa.Column("rules_version", sa.String(length=256), nullable=False),
        sa.Column("rules_hash", sa.String(length=64), nullable=False),
        sa.Column("evidence_set_hash", sa.String(length=64), nullable=False),
        sa.Column("assessed_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("confidence_basis_points", sa.Integer(), nullable=False),
        sa.Column("valid", sa.Boolean(), nullable=False),
        sa.Column("invalidation_reason", sa.Text(), nullable=True),
        sa.Column(
            "created_in_session",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "effective_grade IN ('E0', 'E1', 'E2', 'E3', 'E4')", name="effective_grade_allowed"
        ),
        sa.CheckConstraint(
            f"epistemic_status IN ({_in(EPISTEMIC_STATUSES)})",
            name="epistemic_status_allowed",
        ),
        sa.CheckConstraint(
            "confidence_basis_points BETWEEN 0 AND 10000",
            name="confidence_range",
        ),
        sa.CheckConstraint(
            "(valid AND invalidation_reason IS NULL) OR "
            "(NOT valid AND invalidation_reason IS NOT NULL)",
            name="validity_tuple_complete",
        ),
    )
    op.create_table(
        "assessment_evidence",
        sa.Column(
            "assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claim_assessments.id"),
            primary_key=True,
        ),
        sa.Column(
            "evidence_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("evidence.id"),
            primary_key=True,
        ),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "role IN ('support', 'counter', 'scope_witness', 'context')",
            name="role_allowed",
        ),
    )
    op.create_table(
        "claim_assessment_heads",
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            primary_key=True,
        ),
        sa.Column(
            "config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            primary_key=True,
        ),
        sa.Column("assessment_state", sa.String(length=16), nullable=False),
        sa.Column(
            "current_assessment_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claim_assessments.id"),
            nullable=True,
        ),
        sa.Column("epistemic_status", sa.String(length=16), nullable=True),
        sa.Column("prepared_by", sa.String(length=32), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "assessment_state IN ('current', 'pending', 'invalid')",
            name="assessment_state_allowed",
        ),
        sa.CheckConstraint(
            f"epistemic_status IN ({_in(EPISTEMIC_STATUSES)})",
            name="epistemic_status_allowed",
        ),
        sa.CheckConstraint(
            "prepared_by IN ('session', 'rules_activation', 'reassessment_worker')",
            name="prepared_by_allowed",
        ),
        sa.CheckConstraint(
            "(assessment_state = 'current' AND current_assessment_id IS NOT NULL "
            "AND epistemic_status IS NOT NULL) OR "
            "(assessment_state IN ('pending', 'invalid') "
            "AND current_assessment_id IS NULL AND epistemic_status IS NULL)",
            name="lifecycle_tuple_complete",
        ),
    )
    op.create_table(
        "checkpoints",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "database_commit_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("commit_attempts.id"),
            nullable=False,
        ),
        sa.Column("knowledge_revision", sa.BigInteger(), nullable=False),
        sa.Column("dependency_graph_revision", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("knowledge_revision >= 0", name="knowledge_revision_nonnegative"),
        sa.CheckConstraint(
            "dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        sa.UniqueConstraint("session_id", name="uq_checkpoints_session_id"),
        sa.UniqueConstraint("database_commit_id", name="uq_checkpoints_database_commit_id"),
    )


def downgrade() -> None:
    op.drop_table("checkpoints")
    op.drop_table("claim_assessment_heads")
    op.drop_table("assessment_evidence")
    op.drop_table("claim_assessments")
    op.drop_table("evidence")
    op.drop_table("claims")
    op.drop_table("session_staging")
    op.drop_constraint(
        "fk_sessions_commit_attempt_id_commit_attempts",
        "sessions",
        type_="foreignkey",
    )
    op.drop_column("sessions", "commit_attempt_id")
    op.drop_table("commit_attempts")
