"""T5.4 (stage 4): repetition protection (§9) config section.

Additive: ``config_snapshots.repetition`` (JSONB, nullable) — the
repetition section of the payload (enabled, rephrase_threshold,
plan_cycle_threshold, no_progress_limit), mirroring the
planning/verification pattern: the bootstrap row is backfilled from
the current payload; older DBs keep NULL until the next online change
(the orchestrator treats a NULL section as disabled).

No schema changes to the domain tables: the cycle detector reads the
existing questions/sessions/claims tables.

Revision ID: 0015_repetition
Revises: 0014_verification
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from packages.domain.canonical import canonical_json_bytes
from packages.domain.config import BOOTSTRAP_PAYLOAD

revision: str = "0015_repetition"
down_revision: str | None = "0014_verification"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE config_snapshots ADD COLUMN IF NOT EXISTS repetition jsonb")
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    repetition = canonical_json_bytes(BOOTSTRAP_PAYLOAD["repetition"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET repetition = CAST(:v AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"v": repetition},
    )


def downgrade() -> None:
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS repetition")
