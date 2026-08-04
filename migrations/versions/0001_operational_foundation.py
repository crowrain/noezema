"""Create the operational foundation and immutable bootstrap configuration."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001_operational_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# These literals intentionally live inside the immutable migration. Never import
# current application code here: old migrations must remain reproducible.
BOOTSTRAP_CONFIG_SNAPSHOT_ID = "9c791cea-c197-564d-9a86-9a0e0e648cd6"
INVALID_QUESTION_NAMESPACE = "adda0065-869e-58bb-8bc6-f95e6603bc24"
BOOTSTRAP_PAYLOAD_JSON = (
    '{"activation_limits":{},"claim_type_rules":{},"curiosity":{"selector":"fifo"},'
    '"embeddings":null,"model":null,"policy":{"version":"bootstrap/deny-all"},'
    '"prompts":{},"schema_version":"runtime-config/v1","session_limits":{},'
    '"token_budgets":{}}'
)
BOOTSTRAP_PAYLOAD_SHA256 = "137d82fbff349a45354e14525b4dcbbf8bf90d2bd9f0e75d773e0b9746b8dc7e"
BOOTSTRAP_REVISION_SHA256 = "c6db1c1af4a656477402514f67f32e0882e3aa577e427b4ec81749a9ef5c41c6"

SESSION_STATES = (
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
)
EVENT_TYPES = (
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


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _validate_bootstrap_literals() -> None:
    payload_sha = hashlib.sha256(BOOTSTRAP_PAYLOAD_JSON.encode("utf-8")).hexdigest()
    if payload_sha != BOOTSTRAP_PAYLOAD_SHA256:
        raise RuntimeError("migration bootstrap payload SHA-256 literal is invalid")

    revision_json = json.dumps(
        {"base_snapshot_id": None, "payload_sha256": payload_sha},
        separators=(",", ":"),
        sort_keys=True,
    )
    revision_sha = hashlib.sha256(revision_json.encode("utf-8")).hexdigest()
    if revision_sha != BOOTSTRAP_REVISION_SHA256:
        raise RuntimeError("migration bootstrap revision SHA-256 literal is invalid")


def upgrade() -> None:
    _validate_bootstrap_literals()

    op.create_table(
        "config_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "base_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            nullable=True,
        ),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("payload_sha256", sa.String(length=64), nullable=False),
        sa.Column("sha", sa.String(length=64), nullable=False),
        sa.Column("activation_mode", sa.String(length=16), nullable=False),
        sa.Column("activation_state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "activation_mode IN ('bootstrap', 'offline', 'online')",
            name="activation_mode_allowed",
        ),
        sa.CheckConstraint(
            "activation_state IN ('draft', 'preparing_heads', 'ready', 'publishing', "
            "'post_publish', 'post_publish_blocked', 'active', 'superseded', 'failed')",
            name="activation_state_allowed",
        ),
        sa.UniqueConstraint("sha", name="uq_config_snapshots_sha"),
    )
    op.create_table(
        "runtime_config_heads",
        sa.Column("scope", sa.String(length=16), primary_key=True),
        sa.Column(
            "active_config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            nullable=False,
        ),
        sa.Column(
            "activating_config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            nullable=True,
        ),
        sa.Column("activation_fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("scope = 'global'", name="scope_global"),
        sa.CheckConstraint("activation_fence >= 0", name="activation_fence_nonnegative"),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="lease_tuple_complete",
        ),
    )
    op.create_table(
        "system_constants",
        sa.Column("key", sa.String(length=128), primary_key=True),
        sa.Column("value", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "domain_revisions",
        sa.Column("scope", sa.String(length=32), primary_key=True),
        sa.Column("revision", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "scope IN ('knowledge', 'dependency_graph')",
            name="scope_allowed",
        ),
        sa.CheckConstraint(
            "revision >= 0",
            name="revision_nonnegative",
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column(
            "config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            nullable=False,
        ),
        sa.Column("lease_owner", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("next_audit_sequence", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("terminal_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            f"state IN ({_in(SESSION_STATES)})",
            name="state_allowed",
        ),
        sa.CheckConstraint("fence >= 0", name="fence_nonnegative"),
        sa.CheckConstraint("next_audit_sequence >= 1", name="next_audit_sequence_positive"),
        sa.CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="lease_tuple_complete",
        ),
    )
    op.create_table(
        "model_runs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("phase", sa.String(length=64), nullable=False),
        sa.Column("model_fingerprint", sa.String(length=64), nullable=False),
        sa.Column("context_manifest_sha256", sa.String(length=64), nullable=False),
        sa.Column("context_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("prompt_version", sa.String(length=128), nullable=False),
        sa.Column("tool_schema_sha256", sa.String(length=64), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=False),
        sa.Column("finish_reason", sa.String(length=64), nullable=True),
        sa.Column("output_schema_valid", sa.Boolean(), nullable=False),
        sa.Column("raw_response_artifact", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("session_id", "turn_id", name="uq_model_runs_session_id_turn_id"),
    )
    op.create_table(
        "actions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "model_run_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("model_runs.id"),
            nullable=False,
        ),
        sa.Column("idempotency_key", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("idempotency_class", sa.String(length=32), nullable=False),
        sa.Column("tool", sa.String(length=128), nullable=False),
        sa.Column("arguments_sha256", sa.String(length=64), nullable=False),
        sa.Column("policy_decision", sa.String(length=32), nullable=True),
        sa.Column("state", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.CheckConstraint(
            "idempotency_class IN ('pure', 'observation', 'idempotent', 'non_idempotent')",
            name="idempotency_class_allowed",
        ),
        sa.CheckConstraint(
            "policy_decision IN ('allow', 'deny', 'require_operator')",
            name="policy_decision_allowed",
        ),
        sa.CheckConstraint(
            "state IN ('proposed', 'policy_evaluated', 'accepted', 'started', 'completed', "
            "'failed', 'outcome_unknown')",
            name="state_allowed",
        ),
        sa.UniqueConstraint("model_run_id", name="uq_actions_model_run_id"),
        sa.UniqueConstraint(
            "session_id", "idempotency_key", name="uq_actions_session_id_idempotency_key"
        ),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=True,
        ),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("type", sa.String(length=64), nullable=False),
        sa.Column("schema_version", sa.Integer(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column("public_summary", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="sequence_positive"),
        sa.CheckConstraint(
            f"type IN ({_in(EVENT_TYPES)})",
            name="type_allowed",
        ),
        sa.CheckConstraint(
            "visibility IN ('public', 'private')",
            name="visibility_allowed",
        ),
    )
    op.create_index(
        "uq_audit_events_session_sequence",
        "audit_events",
        ["session_id", "sequence"],
        unique=True,
        postgresql_where=sa.text("session_id IS NOT NULL"),
    )
    op.create_index(
        "uq_audit_events_global_sequence",
        "audit_events",
        ["sequence"],
        unique=True,
        postgresql_where=sa.text("session_id IS NULL"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "audit_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_events.id"),
            nullable=False,
        ),
        sa.Column("topic", sa.String(length=128), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.CheckConstraint("attempts >= 0", name="attempts_nonnegative"),
        sa.UniqueConstraint("audit_event_id", name="uq_outbox_events_audit_event_id"),
    )

    # The following inserts are inside the migration transaction. A database can
    # never observe the schema without its active bootstrap configuration head.
    op.execute(
        sa.text(
            "INSERT INTO config_snapshots "
            "(id, base_snapshot_id, payload, payload_sha256, sha, activation_mode, "
            "activation_state, created_at) VALUES "
            f"('{BOOTSTRAP_CONFIG_SNAPSHOT_ID}'::uuid, NULL, "
            f"CAST(:bootstrap_payload AS jsonb), '{BOOTSTRAP_PAYLOAD_SHA256}', "
            f"'{BOOTSTRAP_REVISION_SHA256}', 'bootstrap', 'active', CURRENT_TIMESTAMP)"
        ).bindparams(
            sa.bindparam("bootstrap_payload", value=BOOTSTRAP_PAYLOAD_JSON, type_=sa.String())
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO runtime_config_heads "
            "(scope, active_config_snapshot_id, activating_config_snapshot_id, "
            "activation_fence, lease_owner, lease_expires_at, updated_at) VALUES "
            f"('global', '{BOOTSTRAP_CONFIG_SNAPSHOT_ID}'::uuid, NULL, 0, NULL, NULL, "
            "CURRENT_TIMESTAMP)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO system_constants (key, value, created_at) VALUES "
            f"('invalid_question_uuid5_namespace', '{INVALID_QUESTION_NAMESPACE}', "
            "CURRENT_TIMESTAMP)"
        )
    )
    op.execute(
        sa.text(
            "INSERT INTO domain_revisions (scope, revision, updated_at) VALUES "
            "('knowledge', 0, CURRENT_TIMESTAMP), "
            "('dependency_graph', 0, CURRENT_TIMESTAMP)"
        )
    )


def downgrade() -> None:
    op.drop_table("outbox_events")
    op.drop_index("uq_audit_events_global_sequence", table_name="audit_events")
    op.drop_index("uq_audit_events_session_sequence", table_name="audit_events")
    op.drop_table("audit_events")
    op.drop_table("actions")
    op.drop_table("model_runs")
    op.drop_table("sessions")
    op.drop_table("domain_revisions")
    op.drop_table("system_constants")
    op.drop_table("runtime_config_heads")
    op.drop_table("config_snapshots")
