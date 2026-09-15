"""T6.1 (stage 5): the research proxy section (§5.12).

Additive:

1. ``config_snapshots.research_proxy`` (JSONB, nullable) — the
   research proxy section of the payload (mode sealed|curated|open_lab,
   max_response_bytes, max_redirects, timeout_seconds, user_agent,
   private_allowlist), mirroring the planning/verification/repetition/
   extraction pattern: the bootstrap row is backfilled from the
   current payload; older DBs keep NULL until the next online change
   (the proxy treats a NULL section as "sealed" — fail-closed, no
   egress).

Revision ID: 0017_research_proxy
Revises: 0016_extraction
Create Date: 2026-09-15
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op
from sqlalchemy import text

from packages.domain.canonical import canonical_json_bytes
from packages.domain.config import BOOTSTRAP_PAYLOAD

revision: str = "0017_research_proxy"
down_revision: str | None = "0016_extraction"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE config_snapshots ADD COLUMN IF NOT EXISTS research_proxy jsonb"
    )
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    research_proxy = canonical_json_bytes(
        BOOTSTRAP_PAYLOAD["research_proxy"]
    ).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET research_proxy = CAST(:v AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"v": research_proxy},
    )


def downgrade() -> None:
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS research_proxy")
