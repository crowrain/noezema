"""Core domain tables (T1.4, §14).

questions, sessions, model_runs, actions, audit_events, outbox_events,
messages, operator_commands.

Columns that reference M2 objects (commit_attempts, workspace manifests,
artifacts) are stored as nullable uuids here; their FK constraints are
added by 0003_commit so migration order follows the plan.

CHECK lists are derived from the canonical enums (single source of truth).
"""

from __future__ import annotations

from alembic import op

from packages.domain.models.enums import (
    ActionState,
    IdempotencyClass,
    MessageState,
    OperatorCommandState,
    OperatorCommandType,
    PolicyDecision,
    QuestionOrigin,
    QuestionState,
    SessionState,
)

revision = "0002_core"
down_revision = "0001_bootstrap"
branch_labels = None
depends_on = None


def _in_check(enum_cls: type) -> str:
    return "(" + ", ".join(f"'{e.value}'" for e in enum_cls) + ")"


def upgrade() -> None:
    op.execute(
        f"""
        CREATE TABLE questions (
            id                          uuid PRIMARY KEY,
            text                        text NOT NULL,
            origin                      text NOT NULL CHECK (origin IN {_in_check(QuestionOrigin)}),
            origin_config_snapshot_id   uuid REFERENCES config_snapshots(id),
            state                       text NOT NULL DEFAULT 'candidate'
                                        CHECK (state IN {_in_check(QuestionState)}),
            priority                    integer NOT NULL DEFAULT 0,
            parent_id                   uuid REFERENCES questions(id),
            score_components            jsonb,
            embedding_fingerprint       text,
            created_at                  timestamptz NOT NULL DEFAULT now(),
            UNIQUE (id)
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE sessions (
            id                            uuid PRIMARY KEY,
            state                         text NOT NULL DEFAULT 'created'
                                          CHECK (state IN {_in_check(SessionState)}),
            lease_owner                   text,
            lease_expires_at              timestamptz,
            last_heartbeat_at             timestamptz,
            last_progress_at              timestamptz,
            phase_deadline                timestamptz,
            stop_requested_at             timestamptz,
            abort_requested_at            timestamptz,
            commit_intent_at              timestamptz,
            commit_attempt_id             uuid,               -- FK: 0003_commit
            question_id                   uuid REFERENCES questions(id),
            base_workspace_manifest_id    uuid,               -- FK: 0003_commit
            committed_workspace_manifest_id uuid,             -- FK: 0003_commit
            config_snapshot_id            uuid NOT NULL REFERENCES config_snapshots(id),
            started_at                    timestamptz,
            finished_at                   timestamptz,
            termination_reason            text
        )
        """
    )

    op.execute(
        """
        CREATE TABLE model_runs (
            id                            uuid PRIMARY KEY,
            session_id                    uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            turn_id                       uuid NOT NULL,
            phase                         text,
            model_fingerprint             jsonb NOT NULL,
            context_manifest_hash         text,
            context_manifest_artifact_id  uuid,               -- FK: 0003_commit
            prompt_version                text,
            tool_schema_hash              text,
            input_tokens                  integer NOT NULL DEFAULT 0,
            output_tokens                 integer NOT NULL DEFAULT 0,
            latency_ms                    numeric(12, 3) NOT NULL DEFAULT 0,
            finish_reason                 text,
            output_schema_valid           boolean NOT NULL DEFAULT true,
            raw_response_artifact_id      uuid,               -- FK: 0003_commit
            raw_retention_until           timestamptz,
            created_at                    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (session_id, turn_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_model_runs_session ON model_runs (session_id, created_at)"
    )

    op.execute(
        f"""
        CREATE TABLE actions (
            id                uuid PRIMARY KEY,
            session_id        uuid NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
            model_run_id      uuid NOT NULL REFERENCES model_runs(id),
            idempotency_key   text NOT NULL,
            idempotency_class text NOT NULL CHECK (idempotency_class IN {_in_check(IdempotencyClass)}),
            tool              text NOT NULL,
            arguments_hash    text NOT NULL,
            policy_decision   text CHECK (policy_decision IN {_in_check(PolicyDecision)}),
            state             text NOT NULL DEFAULT 'proposed'
                              CHECK (state IN {_in_check(ActionState)}),
            started_at        timestamptz,
            finished_at       timestamptz,
            result_artifact_id uuid,                          -- FK: 0003_commit
            error_code        text,
            UNIQUE (model_run_id),
            UNIQUE (session_id, idempotency_key)
        )
        """
    )
    op.execute("CREATE INDEX ix_actions_session ON actions (session_id)")

    op.execute(
        """
        CREATE TABLE audit_events (
            id              uuid PRIMARY KEY,
            session_id      uuid,
            sequence        bigint NOT NULL,
            type            text NOT NULL,
            schema_version  integer NOT NULL DEFAULT 1,
            occurred_at     timestamptz NOT NULL DEFAULT now(),
            actor           text,
            public_summary  text,
            payload         jsonb,
            visibility      text NOT NULL DEFAULT 'operator',
            UNIQUE (session_id, sequence)
        )
        """
    )
    op.execute("CREATE INDEX ix_audit_events_session ON audit_events (session_id, sequence)")
    op.execute("CREATE INDEX ix_audit_events_occurred ON audit_events (occurred_at)")

    op.execute(
        """
        CREATE TABLE outbox_events (
            id              uuid PRIMARY KEY,
            audit_event_id  uuid NOT NULL UNIQUE REFERENCES audit_events(id),
            topic           text NOT NULL,
            payload         jsonb NOT NULL,
            created_at      timestamptz NOT NULL DEFAULT now(),
            published_at    timestamptz,
            attempts        integer NOT NULL DEFAULT 0
        )
        """
    )
    op.execute("CREATE INDEX ix_outbox_unpublished ON outbox_events (created_at) WHERE published_at IS NULL")

    op.execute(
        f"""
        CREATE TABLE messages (
            id                      uuid PRIMARY KEY,
            sender                  text NOT NULL DEFAULT 'owner',
            body                    text NOT NULL,
            priority                integer NOT NULL DEFAULT 0,
            state                   text NOT NULL DEFAULT 'created'
                                    CHECK (state IN {_in_check(MessageState)}),
            expires_at              timestamptz,
            created_at              timestamptz NOT NULL DEFAULT now(),
            delivered_at            timestamptz,
            acknowledged_at         timestamptz,
            response_audit_event_id uuid
        )
        """
    )

    op.execute(
        f"""
        CREATE TABLE operator_commands (
            id              uuid PRIMARY KEY,
            actor_id        text NOT NULL,
            type            text NOT NULL CHECK (type IN {_in_check(OperatorCommandType)}),
            arguments       jsonb NOT NULL DEFAULT '{{}}'::jsonb,
            state           text NOT NULL DEFAULT 'accepted'
                            CHECK (state IN {_in_check(OperatorCommandState)}),
            idempotency_key text NOT NULL,
            reason          text,
            created_at      timestamptz NOT NULL DEFAULT now(),
            finished_at     timestamptz,
            result          jsonb,
            UNIQUE (idempotency_key)
        )
        """
    )


def downgrade() -> None:
    for table in (
        "operator_commands",
        "messages",
        "outbox_events",
        "audit_events",
        "actions",
        "model_runs",
        "sessions",
        "questions",
    ):
        op.execute(f"DROP TABLE IF EXISTS {table}")
