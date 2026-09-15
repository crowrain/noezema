"""T7.3 (stage 7): GC — pinned/legal-retention objects (§15.3, §20.12).

Additive:

1. ``gc_pinned`` — the pinned/legal-retention object registry. An
   operator can pin any object (kind + object id) so the GC sweep never
   deletes it, regardless of retention. §15.3 lists «pinned /
   legal-retention objects» as a root class. The sweep's candidate
   query excludes pinned artifacts; the pin table is the durable
   record of the operator decision (audited via ``gc_sweep`` /
   operator command).

Revision ID: 0019_gc
Revises: 0018_backup_pitr
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0019_gc"
down_revision: str | None = "0018_backup_pitr"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE gc_pinned (
            kind           text PRIMARY KEY
                           CHECK (kind IN (
                               'artifact', 'workspace_manifest',
                               'backup_manifest', 'claim', 'config_snapshot'
                           )),
            object_id      text NOT NULL,
            reason         text NOT NULL,
            pinned_at      timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:  # pragma: no cover
    op.execute("DROP TABLE IF EXISTS gc_pinned")
