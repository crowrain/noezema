"""Writer admission (§5.9.1, §14.1, T4.4): the knowledge_write_gate table
in its spec shape + the reassessment_admission config section.

Single transaction:
  1. ``config_snapshots.reassessment_admission`` — jsonb section
     (T_escalate, T_worker_admission, queue SLO; §5.9.1: the thresholds are
     pinned in the config snapshot). Backfilled on the bootstrap row from
     the pinned ``BOOTSTRAP_PAYLOAD`` (the section is part of the payload
     identity), same pattern as ``wake_schedule`` (0005).
  2. ``knowledge_write_gate`` — rebuilt to the §14.1 shape
     (scope, owner_kind, owner_id, priority, lease_expires_at, acquired_at).
     The MVP shape (0003: single row id=1, owner, intent_kind,
     intent_expires_at) was an early draft; the protocol (T4.4) needs the
     owner kind/id + priority + lease, and the row is lock-protected
     anyway (single row, CAS update). The table holds no application data
     beyond the current holder, so drop+recreate is safe.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0008_writer_admission"
down_revision = "0007_cascade"
branch_labels = None
depends_on = None

from packages.domain.canonical import canonical_json_bytes  # noqa: E402
from packages.domain.config import BOOTSTRAP_PAYLOAD  # noqa: E402


def upgrade() -> None:
    op.execute(
        "ALTER TABLE config_snapshots "
        "ADD COLUMN reassessment_admission jsonb NOT NULL DEFAULT '{}'"
    )
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    admission = canonical_json_bytes(BOOTSTRAP_PAYLOAD["reassessment_admission"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET reassessment_admission = CAST(:ra AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"ra": admission},
    )

    op.execute("DROP TABLE IF EXISTS knowledge_write_gate")
    op.execute(
        """
        CREATE TABLE knowledge_write_gate (
            scope             text PRIMARY KEY CHECK (scope = 'global'),
            owner_kind        text,
            owner_id          text,
            priority          integer,
            acquired_at       timestamptz,
            lease_expires_at  timestamptz,
            CHECK ((owner_kind IS NULL) = (owner_id IS NULL)),
            CHECK ((owner_kind IS NULL) = (acquired_at IS NULL)),
            CHECK ((owner_kind IS NULL) = (lease_expires_at IS NULL)),
            CHECK (priority IS NULL OR priority >= 0)
        )
        """
    )
    op.execute("INSERT INTO knowledge_write_gate (scope) VALUES ('global')")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_write_gate")
    op.execute(
        """
        CREATE TABLE knowledge_write_gate (
            id                integer PRIMARY KEY CHECK (id = 1),
            owner             text,
            intent_kind       text,
            acquired_at       timestamptz,
            intent_expires_at timestamptz
        )
        """
    )
    op.execute("INSERT INTO knowledge_write_gate (id) VALUES (1)")
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS reassessment_admission")
