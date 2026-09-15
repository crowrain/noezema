"""T5.5 (stage 4): the untrusted extraction profile (§11.2).

Additive:

1. ``sessions.extraction`` (JSONB) + ``sessions.extraction_sha256``
   (TEXT) — the extraction records: documents processed by the
   profile, each with host-computed provenance (path, document
   sha256, per-chunk verbatim quote sha256). The raw document
   content is NEVER stored here. NULL = nothing was extracted.
2. ``config_snapshots.extraction`` (JSONB, nullable) — the
   extraction section of the payload (mode "off" | "llm",
   min_document_bytes, max_chunks), mirroring the planning/
   verification/repetition pattern: the bootstrap row is backfilled
   from the current payload; older DBs keep NULL until the next
   online change (the orchestrator treats a NULL section as "off").

Revision ID: 0016_extraction
Revises: 0015_repetition
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

revision: str = "0016_extraction"
down_revision: str | None = "0015_repetition"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("sessions", sa.Column("extraction", JSONB, nullable=True))
    op.add_column("sessions", sa.Column("extraction_sha256", sa.Text(), nullable=True))
    op.execute("ALTER TABLE config_snapshots ADD COLUMN IF NOT EXISTS extraction jsonb")
    # the JSON section is passed as a bind parameter (colons in JSON would
    # be parsed as bind markers by op.execute)
    extraction = canonical_json_bytes(BOOTSTRAP_PAYLOAD["extraction"]).decode("utf-8")
    op.get_bind().execute(
        text(
            "UPDATE config_snapshots SET extraction = CAST(:v AS jsonb) "
            "WHERE activation_mode = 'bootstrap'"
        ),
        {"v": extraction},
    )


def downgrade() -> None:
    op.drop_column("sessions", "extraction_sha256")
    op.drop_column("sessions", "extraction")
    op.execute("ALTER TABLE config_snapshots DROP COLUMN IF EXISTS extraction")
