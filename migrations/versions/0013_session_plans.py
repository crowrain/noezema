"""T5.2 (stage 4): the session plan as an observable artifact.

Additive:

1. ``sessions.plan`` (JSONB) + ``sessions.plan_sha256`` (TEXT) — the
   structured multi-step plan proposed in the planning phase
   (observations that can change confidence, stopping criteria,
   assessment methods). NULL = the MVP fixed-template plan (planning
   mode "template" or the LLM proposal fell back to the template).
   The plan is written once per session and never mutated; the sha256
   is the canonical hash of the stored plan object.
2. ``config_snapshots.planning`` (JSONB, nullable) — the planning
   section of the payload (mode "template" | "llm", max_steps),
   mirroring the wake_schedule pattern of 0005: the bootstrap row is
   backfilled from the current payload; older DBs keep NULL until the
   next online change (the orchestrator treats a NULL section as
   "template").

Revision ID: 0013_session_plans
Revises: 0012_counter_resolutions
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import JSONB

from packages.domain.canonical import canonical_json_bytes
from packages.domain.config import BOOTSTRAP_PAYLOAD

revision: str = "0013_session_plans"
down_revision: str | None = "0012_counter_resolutions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("plan", JSONB, nullable=True))
    op.add_column("sessions", sa.Column("plan_sha256", sa.Text(), nullable=True))
    op.execute(
        "ALTER TABLE config_snapshots ADD COLUMN IF NOT EXISTS planning jsonb"
    )
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    planning = canonical_json_bytes(BOOTSTRAP_PAYLOAD["planning"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET planning = CAST(:pl AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"pl": planning},
    )


def downgrade() -> None:
    op.drop_column("sessions", "plan_sha256")
    op.drop_column("sessions", "plan")
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS planning")
