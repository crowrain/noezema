"""T5.3 (stage 4): the verifier role and its observable report.

Additive:

1. ``sessions.verification`` (JSONB) +
   ``sessions.verification_sha256`` (TEXT) — the verifier's structured
   report (organized deterministic checks, gaps). NULL = the MVP
   no-op verifying phase (verification mode "off" or the proposal
   fell back). Written once per session, never mutated.
2. ``config_snapshots.verification`` (JSONB, nullable) — the
   verification section of the payload (mode "off" | "llm",
   max_checks), mirroring the planning/wake_schedule pattern: the
   bootstrap row is backfilled from the current payload; older DBs
   keep NULL until the next online change (the orchestrator treats a
   NULL section as "off").

The report carries NO grade/confidence field by construction: the
verifier organizes deterministic checks and interprets their results,
but the claim status is never changed by its judgment (§3.7, §6.4 —
grade/confidence are produced only by the rules engine).

Revision ID: 0014_verification
Revises: 0013_session_plans
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

revision: str = "0014_verification"
down_revision: str | None = "0013_session_plans"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("verification", JSONB, nullable=True))
    op.add_column("sessions", sa.Column("verification_sha256", sa.Text(), nullable=True))
    op.execute("ALTER TABLE config_snapshots ADD COLUMN IF NOT EXISTS verification jsonb")
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    verification = canonical_json_bytes(BOOTSTRAP_PAYLOAD["verification"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET verification = CAST(:v AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"v": verification},
    )


def downgrade() -> None:
    op.drop_column("sessions", "verification_sha256")
    op.drop_column("sessions", "verification")
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS verification")
