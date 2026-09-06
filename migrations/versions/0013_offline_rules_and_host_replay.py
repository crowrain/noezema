"""Add offline activation seals and idempotent host-event replay."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_offline_rules_and_host_replay"
down_revision: str | None = "0012_autonomous_scheduler"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

AUDIT_EVENT_TYPES_V5 = (
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
    "SchedulerWakeStarted",
    "SchedulerWakeFinished",
)
AUDIT_EVENT_TYPES_V6 = (
    *AUDIT_EVENT_TYPES_V5,
    "HostTransitionStateChanged",
    "ConfigActivated",
    "HostPolicyChanged",
)


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    op.add_column(
        "config_snapshots",
        sa.Column("activation_manifest_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_cohort_revision", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_expected_head_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_verified_head_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_heads_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_invalid_head_count", sa.Integer(), nullable=True),
    )
    op.add_column(
        "config_snapshots",
        sa.Column("activation_verified_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_check_constraint(
        "activation_seal_complete",
        "config_snapshots",
        "(activation_manifest_sha256 IS NULL AND activation_cohort_revision IS NULL "
        "AND activation_expected_head_count IS NULL "
        "AND activation_verified_head_count IS NULL "
        "AND activation_heads_sha256 IS NULL AND activation_invalid_head_count IS NULL "
        "AND activation_verified_at IS NULL) OR "
        "(activation_manifest_sha256 IS NOT NULL AND activation_cohort_revision >= 0 "
        "AND activation_expected_head_count >= 0 "
        "AND activation_verified_head_count = activation_expected_head_count "
        "AND activation_heads_sha256 IS NOT NULL "
        "AND activation_invalid_head_count BETWEEN 0 AND activation_expected_head_count "
        "AND activation_verified_at IS NOT NULL)",
    )
    op.create_index(
        "uq_config_snapshots_unfinished_offline_candidate",
        "config_snapshots",
        ["base_snapshot_id", "payload_sha256"],
        unique=True,
        postgresql_where=sa.text(
            "activation_mode = 'offline' AND activation_state <> 'failed'"
        ),
    )
    op.create_table(
        "config_activation_manifest_claims",
        sa.Column(
            "config_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("config_snapshots.id"),
            primary_key=True,
        ),
        sa.Column(
            "claim_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("claims.id"),
            primary_key=True,
        ),
    )
    op.create_table(
        "host_event_replays",
        sa.Column("attempt_id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("event_seq", sa.BigInteger(), primary_key=True),
        sa.Column(
            "audit_event_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("audit_events.id"),
            nullable=False,
            unique=True,
        ),
        sa.Column("replayed_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("event_seq >= 1", name="event_seq_positive"),
    )
    op.execute(
        """
        CREATE FUNCTION noezema_guard_offline_activation_seal()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            IF OLD.activation_mode = 'offline'
               AND OLD.activation_state IN ('ready', 'active', 'superseded')
               AND (
                    NEW.activation_manifest_sha256 IS DISTINCT FROM OLD.activation_manifest_sha256
                 OR NEW.activation_cohort_revision IS DISTINCT FROM OLD.activation_cohort_revision
                 OR NEW.activation_expected_head_count IS DISTINCT FROM OLD.activation_expected_head_count
                 OR NEW.activation_verified_head_count IS DISTINCT FROM OLD.activation_verified_head_count
                 OR NEW.activation_heads_sha256 IS DISTINCT FROM OLD.activation_heads_sha256
                 OR NEW.activation_invalid_head_count IS DISTINCT FROM OLD.activation_invalid_head_count
                 OR NEW.activation_verified_at IS DISTINCT FROM OLD.activation_verified_at
               )
               AND NOT (
                    OLD.activation_state = 'ready'
                AND NEW.activation_state = 'preparing_heads'
                AND NEW.activation_manifest_sha256 IS NULL
                AND NEW.activation_cohort_revision IS NULL
                AND NEW.activation_expected_head_count IS NULL
                AND NEW.activation_verified_head_count IS NULL
                AND NEW.activation_heads_sha256 IS NULL
                AND NEW.activation_invalid_head_count IS NULL
                AND NEW.activation_verified_at IS NULL
               )
            THEN
                RAISE EXCEPTION 'sealed offline activation metadata is immutable';
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_config_snapshots_offline_seal
        BEFORE UPDATE ON config_snapshots
        FOR EACH ROW EXECUTE FUNCTION noezema_guard_offline_activation_seal()
        """
    )
    op.execute(
        """
        CREATE FUNCTION noezema_guard_offline_shadow_head()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            snapshot_id uuid;
            snapshot_mode text;
            snapshot_state text;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (OLD.config_snapshot_id IS DISTINCT FROM NEW.config_snapshot_id
                    OR OLD.claim_id IS DISTINCT FROM NEW.claim_id) THEN
                RAISE EXCEPTION 'shadow head identity is immutable';
            END IF;
            snapshot_id := CASE WHEN TG_OP = 'DELETE'
                                THEN OLD.config_snapshot_id ELSE NEW.config_snapshot_id END;
            SELECT activation_mode, activation_state
              INTO snapshot_mode, snapshot_state
              FROM config_snapshots WHERE id = snapshot_id;
            IF snapshot_mode = 'offline' AND snapshot_state = 'ready' THEN
                RAISE EXCEPTION 'sealed offline shadow heads are immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_claim_assessment_heads_offline_seal
        BEFORE INSERT OR UPDATE OR DELETE ON claim_assessment_heads
        FOR EACH ROW EXECUTE FUNCTION noezema_guard_offline_shadow_head()
        """
    )
    op.execute(
        """
        CREATE FUNCTION noezema_guard_offline_activation_manifest()
        RETURNS trigger LANGUAGE plpgsql AS $$
        DECLARE
            snapshot_id uuid;
            snapshot_mode text;
            snapshot_state text;
        BEGIN
            IF TG_OP = 'UPDATE'
               AND (OLD.config_snapshot_id IS DISTINCT FROM NEW.config_snapshot_id
                    OR OLD.claim_id IS DISTINCT FROM NEW.claim_id) THEN
                RAISE EXCEPTION 'activation manifest identity is immutable';
            END IF;
            snapshot_id := CASE WHEN TG_OP = 'DELETE'
                                THEN OLD.config_snapshot_id ELSE NEW.config_snapshot_id END;
            SELECT activation_mode, activation_state
              INTO snapshot_mode, snapshot_state
              FROM config_snapshots WHERE id = snapshot_id;
            IF snapshot_mode = 'offline'
               AND snapshot_state IN ('ready', 'active', 'superseded') THEN
                RAISE EXCEPTION 'sealed offline activation manifest is immutable';
            END IF;
            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_config_activation_manifest_offline_seal
        BEFORE INSERT OR UPDATE OR DELETE ON config_activation_manifest_claims
        FOR EACH ROW EXECUTE FUNCTION noezema_guard_offline_activation_manifest()
        """
    )
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V6)})",
    )


def downgrade() -> None:
    op.drop_constraint(op.f("ck_audit_events_type_allowed"), "audit_events", type_="check")
    op.create_check_constraint(
        "type_allowed",
        "audit_events",
        f"type IN ({_in(AUDIT_EVENT_TYPES_V5)})",
    )
    op.execute(
        "DROP TRIGGER trg_config_activation_manifest_offline_seal "
        "ON config_activation_manifest_claims"
    )
    op.execute("DROP FUNCTION noezema_guard_offline_activation_manifest()")
    op.execute(
        "DROP TRIGGER trg_claim_assessment_heads_offline_seal ON claim_assessment_heads"
    )
    op.execute("DROP FUNCTION noezema_guard_offline_shadow_head()")
    op.execute("DROP TRIGGER trg_config_snapshots_offline_seal ON config_snapshots")
    op.execute("DROP FUNCTION noezema_guard_offline_activation_seal()")
    op.drop_table("host_event_replays")
    op.drop_table("config_activation_manifest_claims")
    op.drop_index(
        "uq_config_snapshots_unfinished_offline_candidate",
        table_name="config_snapshots",
    )
    op.drop_constraint(
        op.f("ck_config_snapshots_activation_seal_complete"),
        "config_snapshots",
        type_="check",
    )
    op.drop_column("config_snapshots", "activation_verified_at")
    op.drop_column("config_snapshots", "activation_invalid_head_count")
    op.drop_column("config_snapshots", "activation_heads_sha256")
    op.drop_column("config_snapshots", "activation_verified_head_count")
    op.drop_column("config_snapshots", "activation_expected_head_count")
    op.drop_column("config_snapshots", "activation_cohort_revision")
    op.drop_column("config_snapshots", "activation_manifest_sha256")
