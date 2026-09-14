"""Wake scheduler: config snapshot section + durable state (T3.29, §5.2.1).

Single transaction:
  1. ``config_snapshots.wake_schedule`` — jsonb section (periodic schedule,
     minimum session gap, backoff, admission limits). Backfilled on the
     bootstrap row from the pinned ``BOOTSTRAP_PAYLOAD``: the section is part
     of the payload identity, so the bootstrap row must carry it.
  2. ``wake_scheduler_state`` — durable per-node wake state: consecutive
     failures, backoff window, last session outcome, auto-pause reason.
"""

from __future__ import annotations

from alembic import op
from sqlalchemy import text

revision = "0005_wake"
down_revision = "0004_memory"
branch_labels = None
depends_on = None

from packages.domain.canonical import canonical_json_bytes  # noqa: E402
from packages.domain.config import BOOTSTRAP_PAYLOAD  # noqa: E402


def upgrade() -> None:
    op.execute(
        "ALTER TABLE config_snapshots "
        "ADD COLUMN wake_schedule jsonb NOT NULL DEFAULT '{}'"
    )
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    wake_schedule = canonical_json_bytes(BOOTSTRAP_PAYLOAD["wake_schedule"]).decode("utf-8")
    op.get_bind().execute(
        text("UPDATE config_snapshots SET wake_schedule = CAST(:ws AS jsonb) WHERE activation_mode = 'bootstrap'"),
        {"ws": wake_schedule},
    )

    op.execute(
        """
        CREATE TABLE wake_scheduler_state (
            node_id                 text PRIMARY KEY,
            consecutive_failures    integer NOT NULL DEFAULT 0,
            backoff_until           timestamptz,
            last_session_finished_at timestamptz,
            last_session_state      text,
            last_failure_at         timestamptz,
            paused_reason           text,
            updated_at              timestamptz NOT NULL DEFAULT now()
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS wake_scheduler_state")
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS wake_schedule")
