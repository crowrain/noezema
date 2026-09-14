"""Bootstrap: hash-pinned config snapshot + global runtime head (T1.3, §14.1).

Single transaction:
  1. system_constants — pins the UUIDv5 namespace for invalid-assessment
     question IDs (immutable system identity, §8.7.1).
  2. config_snapshots — schema; seeds exactly one bootstrap row
     (activation_mode='bootstrap', base NULL, activation_state='active')
     after re-computing and verifying its payload_sha256 (fail-closed).
  3. runtime_config_heads — exactly one scope='global' row pointing at the
     bootstrap snapshot; effective config is determined only by pointer
     equality (§14.1).

The seed payload and its expected hash are immutable literals from
``packages.domain.config``; a mismatch aborts the migration.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0001_bootstrap"
down_revision = None
branch_labels = None
depends_on = None

from packages.domain.canonical import canonical_sha256  # noqa: E402
from packages.domain.config import (  # noqa: E402
    BOOTSTRAP_PAYLOAD,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_SNAPSHOT_ID,
    QUESTION_UUID5_NAMESPACE,
    bootstrap_payload_bytes,
    bootstrap_snapshot_sha256,
)


def _verify_bootstrap_payload() -> None:
    computed = canonical_sha256(BOOTSTRAP_PAYLOAD)
    if computed != BOOTSTRAP_PAYLOAD_SHA256:
        raise RuntimeError(
            f"bootstrap payload hash mismatch: expected {BOOTSTRAP_PAYLOAD_SHA256}, computed {computed}"
        )


def upgrade() -> None:
    _verify_bootstrap_payload()
    conn = op.get_bind()

    op.execute(
        """
        CREATE TABLE system_constants (
            key         text PRIMARY KEY,
            value       text NOT NULL,
            created_at  timestamptz NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE config_snapshots (
            id                              uuid PRIMARY KEY,
            base_snapshot_id                uuid REFERENCES config_snapshots(id),
            payload_sha256                  text NOT NULL,
            sha256                          text NOT NULL,
            activation_mode                 text NOT NULL,
            activation_state                text NOT NULL,
            activation_cursor               bigint,
            activation_manifest_hash        text,
            activation_cohort_revision      bigint,
            activation_expected_head_count  bigint,
            activation_verified_head_count  bigint,
            activation_heads_sha256         text,
            activation_verified_at          timestamptz,
            post_publish_manifest_hash      text,
            post_publish_cursor             bigint,
            post_publish_attempts           integer,
            post_publish_next_attempt_at    timestamptz,
            post_publish_started_at         timestamptz,
            post_publish_blocked_at         timestamptz,
            post_publish_last_error         text,
            model                           jsonb NOT NULL,
            embeddings                      jsonb NOT NULL,
            prompts                         jsonb NOT NULL,
            policy                          jsonb NOT NULL,
            curiosity                       jsonb NOT NULL,
            token_budgets                   jsonb NOT NULL,
            session_limits                  jsonb NOT NULL,
            activation_limits               jsonb NOT NULL,
            claim_type_rules                jsonb NOT NULL,
            created_at                      timestamptz NOT NULL DEFAULT now(),
            CHECK (activation_mode IN ('bootstrap', 'offline', 'online')),
            CHECK (activation_state IN ('draft', 'preparing_heads', 'ready', 'publishing',
                                        'post_publish', 'post_publish_blocked', 'active',
                                        'superseded', 'failed')),
            -- bootstrap is immutable: no base, always active (§14.1)
            CHECK ((activation_mode = 'bootstrap' AND base_snapshot_id IS NULL AND activation_state = 'active')
                   OR activation_mode <> 'bootstrap')
        )
        """
    )

    # One unfinished offline candidate per (base, payload); audited retry
    # after terminal 'failed' stays possible (§8.7.1).
    op.execute(
        """
        CREATE UNIQUE INDEX uq_config_snapshots_offline_candidate
            ON config_snapshots (base_snapshot_id, payload_sha256)
            WHERE activation_mode = 'offline' AND activation_state <> 'failed'
        """
    )

    op.execute(
        """
        CREATE TABLE runtime_config_heads (
            scope                          text PRIMARY KEY,
            active_config_snapshot_id      uuid NOT NULL REFERENCES config_snapshots(id),
            activating_config_snapshot_id  uuid REFERENCES config_snapshots(id),
            activation_fence               bigint NOT NULL DEFAULT 0,
            activation_lease_owner         text,
            activation_lease_expires_at    timestamptz,
            updated_at                     timestamptz NOT NULL DEFAULT now(),
            -- slot invariants (§14.1): activating slot and lease fields move together
            CHECK ((activating_config_snapshot_id IS NULL) = (activation_lease_owner IS NULL)),
            CHECK ((activating_config_snapshot_id IS NULL) = (activation_lease_expires_at IS NULL))
        )
        """
    )

    payload_json = bootstrap_payload_bytes().decode("utf-8")
    conn.execute(
        text(
            """
            INSERT INTO config_snapshots (
                id, base_snapshot_id, payload_sha256, sha256,
                activation_mode, activation_state,
                model, embeddings, prompts, policy, curiosity,
                token_budgets, session_limits, activation_limits, claim_type_rules
            ) VALUES (
                :id, NULL, :payload_sha256, :sha256,
                'bootstrap', 'active',
                (:payload_json)::jsonb->'model',
                (:payload_json)::jsonb->'embeddings',
                (:payload_json)::jsonb->'prompts',
                (:payload_json)::jsonb->'policy',
                (:payload_json)::jsonb->'curiosity',
                (:payload_json)::jsonb->'token_budgets',
                (:payload_json)::jsonb->'session_limits',
                (:payload_json)::jsonb->'activation_limits',
                (:payload_json)::jsonb->'claim_type_rules'
            )
            """
        ),
        {
            "id": str(BOOTSTRAP_SNAPSHOT_ID),
            "payload_sha256": BOOTSTRAP_PAYLOAD_SHA256,
            "sha256": bootstrap_snapshot_sha256(),
            "payload_json": payload_json,
        },
    )

    conn.execute(
        text(
            "INSERT INTO runtime_config_heads (scope, active_config_snapshot_id, activating_config_snapshot_id) "
            "VALUES ('global', :active_id, NULL)"
        ),
        {"active_id": str(BOOTSTRAP_SNAPSHOT_ID)},
    )

    conn.execute(
        text("INSERT INTO system_constants (key, value) VALUES ('question_uuid5_namespace', :ns)"),
        {"ns": QUESTION_UUID5_NAMESPACE},
    )


def downgrade() -> None:
    # The bootstrap payload is immutable system identity; downgrade is a
    # fresh-install teardown only.
    op.execute("DROP TABLE IF EXISTS runtime_config_heads")
    op.execute("DROP TABLE IF EXISTS config_snapshots")
    op.execute("DROP TABLE IF EXISTS system_constants")
