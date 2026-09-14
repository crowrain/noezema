"""0010: environment independence (T4.6, §8.7.3, §14).

- environment_manifests.manifest_hash — content hash of the FULL field set
  (protocol/implementation/lineage/toolchain/runtime/hardware/seed/
  data_order + normalizer version); content-addressed dedup via a unique
  index. Backfilled for MVP rows (there the opaque protocol_hash WAS the
  content hash of the degenerate manifest).
- environment_independence_snapshots / environment_independence_members —
  the versioned environment-independence algorithm (§8.7.3): groups are
  built over (protocol, implementation, dataset lineage); a different
  GPU/backend, seed or data order NEVER creates a group. A claim
  assessment records the snapshot and counts distinct groups, not
  manifest hashes.
"""

from __future__ import annotations

from alembic import op

revision = "0010_env_independence"
down_revision = "0009_online_activation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Two-step add: NOT NULL without a DEFAULT is refused on a table that
    # already has rows (the noezema_mvp evidence DB has MVP manifests).
    op.execute("ALTER TABLE environment_manifests ADD COLUMN manifest_hash text")
    # MVP rows: protocol_hash held the opaque content hash of the
    # degenerate (protocol-only) manifest — carry it over verbatim.
    op.execute(
        """
        UPDATE environment_manifests
        SET manifest_hash = COALESCE(NULLIF(protocol_hash, ''), 'env-legacy:' || id)
        WHERE manifest_hash IS NULL
        """
    )
    op.execute(
        "ALTER TABLE environment_manifests ALTER COLUMN manifest_hash SET NOT NULL"
    )
    op.execute(
        "CREATE UNIQUE INDEX uq_environment_manifests_hash "
        "ON environment_manifests (manifest_hash)"
    )

    op.execute(
        """
        CREATE TABLE environment_independence_snapshots (
            id               uuid PRIMARY KEY,
            algorithm_version text NOT NULL,
            rules_hash        text NOT NULL,
            created_at        timestamptz NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE environment_independence_members (
            snapshot_id           uuid NOT NULL
                                   REFERENCES environment_independence_snapshots(id)
                                   ON DELETE CASCADE,
            environment_manifest_id uuid NOT NULL
                                    REFERENCES environment_manifests(id)
                                    ON DELETE CASCADE,
            group_id              text NOT NULL,
            relation              text NOT NULL,
            basis                 text NOT NULL,
            PRIMARY KEY (snapshot_id, environment_manifest_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_env_indep_members_group "
        "ON environment_independence_members (snapshot_id, group_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS environment_independence_members")
    op.execute("DROP TABLE IF EXISTS environment_independence_snapshots")
    op.execute("DROP INDEX IF EXISTS uq_environment_manifests_hash")
    op.execute("ALTER TABLE environment_manifests DROP COLUMN IF EXISTS manifest_hash")
