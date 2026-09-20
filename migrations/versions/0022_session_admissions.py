"""T7.20 (EVAL-3d quiesce race, §8.7.2, ADR-0009): session admission records.

The online-activation quiesce check ("no active sessions", §8.7.2 step 3)
is only sound if an in-flight session is VISIBLE to it. A session's phase-1
transaction is long-lived: the ``sessions`` row (and its lease) stays
uncommitted until the session reaches COMMITTING, so a session admitted
before the flip is invisible to the check at the moment of the flip — the
EVAL-3d quiesce race (docs/eval/EVAL-3-freeze.md §10.5: session 6f45deea
committed a claim with a head only on the superseded snapshot).

The fix is a committed admission record written BEFORE the phase-1
transaction opens:

``session_admissions`` — one row per admitted in-flight session
(session_id PK, host-generated; the ``sessions`` row does not exist yet,
so there is no FK in this direction). The row lives until:

* the session's TERMINAL transaction — a DB trigger on ``sessions``
  deletes it on every transition to a terminal state, so no code path
  can miss the release; or
* its lease expires — a crashed session cannot commit knowledge once
  its own (shorter) lease is dead; the activation sweeps expired rows
  at the quiesce check.

The activation's quiesce check counts live admission records as active
sessions. A session whose admission record is lost/expired but which can
still commit is caught by the commit-time backstop in
``packages/domain/services/commit.py`` (pending head + durable
reassessment job on the active snapshot for every claim the commit
creates — the cohort mechanism of §8.7.2).

Revision ID: 0022_session_admissions
Revises: 0021_search_statements
Create Date: 2026-09-19
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_session_admissions"
down_revision: str | None = "0021_search_statements"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

def upgrade() -> None:
    # the carry-over head (T7.20) is prepared by the commit boundary
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier','system:source_graph',"
        "'system:counter_resolution','commit_carryover'))"
    )
    op.execute(
        """
        CREATE TABLE session_admissions (
            session_id uuid PRIMARY KEY,
            node_owner text NOT NULL,
            config_snapshot_id uuid NOT NULL,
            lease_expires_at timestamptz NOT NULL,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp()
        );
        """
    )
    # the terminal state and the admission release commit in ONE
    # transaction (T7.20, §8.7.2): the activation can never observe
    # "terminal session without admission release"
    op.execute(
        """
        CREATE FUNCTION release_session_admission_on_terminal()
        RETURNS trigger AS $$
        BEGIN
            DELETE FROM session_admissions WHERE session_id = NEW.id;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_session_admission_terminal
        AFTER UPDATE OF state ON sessions
        FOR EACH ROW
        WHEN (new.state IN ('succeeded', 'succeeded_partial', 'failed', 'cancelled'))
        EXECUTE FUNCTION release_session_admission_on_terminal()
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_session_admission_terminal ON sessions"
    )
    op.execute("DROP FUNCTION IF EXISTS release_session_admission_on_terminal()")
    op.execute("DROP TABLE IF EXISTS session_admissions")
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "DROP CONSTRAINT claim_assessment_heads_prepared_by_check"
    )
    op.execute(
        "ALTER TABLE claim_assessment_heads "
        "ADD CONSTRAINT claim_assessment_heads_prepared_by_check "
        "CHECK (prepared_by IN ('session','rules_activation','reassessment_worker',"
        "'system:cascade','system:barrier','system:source_graph',"
        "'system:counter_resolution'))"
    )
