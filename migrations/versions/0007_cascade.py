"""Cascade invalidation: closure manifests, barriers, reassessment jobs (T4.2, §8.6).

  1. ``closure_manifests`` — immutable, content-addressed reverse-closure
     snapshots (root, graph revision, ordered claim IDs with topological
     rank, count, sha256). ``id = uuid5(CLOSURE_MANIFEST_UUID5_NAMESPACE,
     sha256)`` — the same closure under the same graph revision always
     resolves to the same row (dedup by content, §8.6).
  2. ``dependency_invalidation_barriers`` — durable work item / GC root
     for a large closure: statuses ``discovering | active | closing |
     resolved | blocked``, immutable manifest reference, durable cursor
     ``next_offset`` (the cursor moves only in the same transaction as
     the idempotent invalidation batch it advances, §14.1).
  3. ``reassessment_jobs`` — durable queue scaffold for the T4.3 worker:
     the cascade enqueues one job per invalidated claim (root included).
     The unique active-job constraint (§14.1) keeps at most one
     ``queued|leased|retry`` job per (claim, target snapshot);
     ``blocked|completed`` rows stay for history.
"""

from __future__ import annotations

from alembic import op

revision = "0007_cascade"
down_revision = "0006_dependency_kinds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # the cascade processors write heads as system actors: extend the
    # M3 closed list (the values are host actors, not LLM output)
    op.execute(
        "ALTER TABLE claim_assessment_heads DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier'))"
    )

    op.execute(
        """
        CREATE TABLE closure_manifests (
            id            uuid PRIMARY KEY,
            root_claim_id uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            graph_revision bigint NOT NULL,
            claim_ids     jsonb NOT NULL,
            ranks         jsonb NOT NULL,
            count         integer NOT NULL CHECK (count >= 0),
            sha256        text NOT NULL,
            created_at    timestamptz NOT NULL DEFAULT now(),
            UNIQUE (sha256),
            CHECK (count = jsonb_array_length(claim_ids))
        )
        """
    )
    op.execute("CREATE INDEX ix_closure_manifests_root ON closure_manifests (root_claim_id)")

    op.execute(
        """
        CREATE TABLE dependency_invalidation_barriers (
            id                  uuid PRIMARY KEY,
            root_claim_id       uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            graph_revision      bigint NOT NULL,
            generation          integer NOT NULL CHECK (generation >= 1),
            status              text NOT NULL
                                CHECK (status IN ('discovering','active','closing','resolved','blocked')),
            closure_manifest_id uuid REFERENCES closure_manifests(id),
            member_count        integer NOT NULL DEFAULT 0 CHECK (member_count >= 0),
            next_offset         integer NOT NULL DEFAULT 0 CHECK (next_offset >= 0),
            last_error          text,
            created_at          timestamptz NOT NULL DEFAULT now(),
            updated_at          timestamptz NOT NULL DEFAULT now(),
            resolved_at         timestamptz,
            UNIQUE (root_claim_id, generation),
            CHECK ((closure_manifest_id IS NULL) = (status = 'discovering')),
            CHECK ((resolved_at IS NULL) = (status <> 'resolved'))
        )
        """
    )
    op.execute(
        "CREATE INDEX ix_dependency_invalidation_barriers_status "
        "ON dependency_invalidation_barriers (status)"
    )

    op.execute(
        """
        CREATE TABLE reassessment_jobs (
            id                       uuid PRIMARY KEY,
            claim_id                 uuid NOT NULL REFERENCES claims(id) ON DELETE CASCADE,
            target_config_snapshot_id uuid NOT NULL REFERENCES config_snapshots(id),
            status                   text NOT NULL
                                     CHECK (status IN ('queued','leased','retry','blocked','completed')),
            reason                   text,
            priority                 integer NOT NULL DEFAULT 0,
            enqueued_at              timestamptz NOT NULL DEFAULT now(),
            attempts                 integer NOT NULL DEFAULT 0,
            max_attempts             integer NOT NULL DEFAULT 78 CHECK (max_attempts > 0),
            error_class              text,
            lease_owner              text,
            lease_expires_at         timestamptz,
            next_attempt_at          timestamptz,
            blocked_at               timestamptz,
            completed_at             timestamptz,
            last_error               text,
            CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL)),
            CHECK ((blocked_at IS NULL) = (status <> 'blocked')),
            CHECK ((completed_at IS NULL) = (status <> 'completed'))
        )
        """
    )
    # one active job per (claim, target snapshot); terminal rows stay for history
    op.execute(
        """
        CREATE UNIQUE INDEX uq_reassessment_jobs_active
        ON reassessment_jobs (claim_id, target_config_snapshot_id)
        WHERE status IN ('queued','leased','retry')
        """
    )
    op.execute(
        "CREATE INDEX ix_reassessment_jobs_claim ON reassessment_jobs (claim_id)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS reassessment_jobs")
    op.execute("DROP TABLE IF EXISTS dependency_invalidation_barriers")
    op.execute("DROP TABLE IF EXISTS closure_manifests")
    op.execute(
        "ALTER TABLE claim_assessment_heads DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker'))"
    )
