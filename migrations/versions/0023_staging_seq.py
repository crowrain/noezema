"""T7.24 (EVAL-4 abort 2026-09-21, §5.2.2, §6.5): per-session staging sequence.

Root cause of the EVAL-4 series abort (docs/STATUS.md, session
44bf319e, noezema-eval4): the commit boundary read the session's recorded
staging ops ordered by ``(created_at, id)``. ``created_at`` is
``now()`` — the constant START of the long phase-1 transaction (AGENTS.md
§7), so within one session ALL staging rows share one timestamp and the
effective order is the random UUID ``id`` order. The evidence links of the
curator proposal carry ``claim_index`` = the position in the PROPOSAL
(the order the ops were recorded); when the UUID order did not match the
recording order the claim→evidence mapping was silently scrambled and a
claim received support evidence of a kind its rule does not allow →
``RuleValidationError`` inside the fenced final transaction → the session
was left in ``committing`` with a ``prepared`` attempt and no hostctl
entry point to resolve it.

The fix is a durable per-session sequence assigned at record time
(``StagingService.record``, the only writer, inside the session's own
phase-1 transaction): the commit boundary reads the ops in recording
(proposal) order regardless of UUID/timestamp order.

Backfill: existing rows are numbered in the ``(created_at, id)`` order
the legacy code would have seen — already-applied sessions consumed
exactly that order (their apply is a no-op now), and the recorded rows
of a stuck session are never applied (the reconciler aborts the attempt,
T2.20).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0023_staging_seq"
down_revision: str | None = "0022_session_admissions"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE session_staging ADD COLUMN seq BIGINT NOT NULL DEFAULT 0")
    op.execute(
        """
        UPDATE session_staging s
        SET seq = sub.n
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (PARTITION BY session_id ORDER BY created_at, id) - 1 AS n
            FROM session_staging
        ) sub
        WHERE sub.id = s.id
        """
    )
    op.execute(
        "ALTER TABLE session_staging "
        "ADD CONSTRAINT uq_session_staging_session_seq UNIQUE (session_id, seq)"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE session_staging DROP CONSTRAINT uq_session_staging_session_seq")
    op.execute("ALTER TABLE session_staging DROP COLUMN seq")
