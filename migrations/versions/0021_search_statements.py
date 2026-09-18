"""T7.7 (EVAL-3, ADR-0006 rev): cross-lingual claim search.

Additive:

``claims.search_statements`` — the model-provided alternative-language
renderings of the statement (MVP: English), indexed for FULL-TEXT
search ONLY. The knowledge text (``statement``) is never replaced:
retrieval ranks ``statement`` with the ``russian`` config and
``search_statements`` with the ``english`` config and takes the
maximum rank, so a query in either language matches the same claim.
EVAL-2 diagnosis (ADR-0006): 0/16 ``memory.search`` calls matched
because the model searches in English while the claims are in Russian
(FTS ``russian`` does not match Latin lexemes).

Default '[]' backfills existing rows — they keep their old behavior
(the russian tsvector alone).

Revision ID: 0021_search_statements
Revises: 0020_evaluation
Create Date: 2026-09-16
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0021_search_statements"
down_revision: str | None = "0020_evaluation"
branch_labels: Sequence[str] | None = None
depends_on: Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE claims
          ADD COLUMN IF NOT EXISTS search_statements jsonb
            NOT NULL DEFAULT '[]'::jsonb
        """
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claims DROP COLUMN IF EXISTS search_statements")
