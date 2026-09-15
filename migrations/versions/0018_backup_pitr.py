"""T7.2 (stage 7): backup/PITR enrichment (§15.3, §22.1 item 12).

Additive:

1. ``backup_manifests.host_state`` (JSONB, nullable) — the host-contour
   state captured at backup time. §15.3: a backup binds the DB recovery
   point with the content-addressed artifact inventory AND includes the
   host-transition active head, the host-policy-change active head, all
   unresolved current records with their immutable event directories,
   the host policy files with their hashes, and the unfinished /
   retention-window policy event streams — OR explicit evidence of the
   absence of both active operations. The ``host_ops_absent`` boolean is
   that explicit evidence: it must be true exactly when both heads are
   absent and there is no unresolved current record (DB-level CHECK).
   Legacy rows (no host state captured) keep NULL — the restore drill
   refuses to verify them (fail-closed).

Shape (schema_version = 1)::

    {
      "schema_version": 1,
      "transition_head": {...} | null,      # the fsync-safe head document
      "policy_change_head": {...} | null,   # the policy change head document
      "unresolved_current_records": [
        {"attempt_id", "state", "last_event_seq",
         "replayed_through_seq", "events_count"}
      ],
      "policy_files": [{"path", "sha256"}],
      "policy_event_streams": [{"change_id", "event_count", "terminal"}],
      "host_ops_absent": bool
    }

Revision ID: 0018_backup_pitr
Revises: 0017_research_proxy
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018_backup_pitr"
down_revision: str | None = "0017_research_proxy"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE backup_manifests ADD COLUMN host_state jsonb")
    op.execute(
        """
        ALTER TABLE backup_manifests
        ADD CONSTRAINT backup_manifests_host_state_shape_check
        CHECK (
            host_state IS NULL
            OR (
                (host_state ->> 'schema_version') = '1'
                AND jsonb_typeof(COALESCE(host_state -> 'transition_head', 'null'::jsonb))
                    IN ('object', 'null')
                AND jsonb_typeof(COALESCE(host_state -> 'policy_change_head', 'null'::jsonb))
                    IN ('object', 'null')
                AND jsonb_typeof(COALESCE(host_state -> 'unresolved_current_records', '[]'::jsonb))
                    = 'array'
                AND jsonb_typeof(COALESCE(host_state -> 'policy_files', '[]'::jsonb))
                    = 'array'
                AND jsonb_typeof(COALESCE(host_state -> 'policy_event_streams', '[]'::jsonb))
                    = 'array'
                -- explicit evidence of the absence of both active
                -- operations: present ⇔ both heads absent AND no
                -- unresolved current record
                AND jsonb_typeof(host_state -> 'host_ops_absent') = 'boolean'
                AND ((host_state ->> 'host_ops_absent') = 'true') = (
                    jsonb_typeof(COALESCE(host_state -> 'transition_head', 'null'::jsonb))
                        = 'null'
                    AND jsonb_typeof(COALESCE(host_state -> 'policy_change_head', 'null'::jsonb))
                        = 'null'
                    AND jsonb_array_length(
                            COALESCE(host_state -> 'unresolved_current_records', '[]'::jsonb)
                        ) = 0
                )
            )
        )
        """
    )


def downgrade() -> None:  # pragma: no cover
    op.execute(
        "ALTER TABLE backup_manifests DROP CONSTRAINT IF EXISTS backup_manifests_host_state_shape_check"
    )
    op.execute("ALTER TABLE backup_manifests DROP COLUMN IF EXISTS host_state")
