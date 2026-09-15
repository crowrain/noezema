"""claim_dependencies kind values per spec (T4.1, §8.6).

The M3 scaffold (0004) allowed ``('evidential','investigative')``; the
spec (§8.6) names the explicit research marker: a hypothesis may be a
*research* dependency only. Align the CHECK constraint with the spec
string; the table is empty in production (no writers existed before
T4.1) and the test databases migrate from scratch.
"""

from __future__ import annotations

from alembic import op

revision = "0006_dependency_kinds"
down_revision = "0005_wake"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE claim_dependencies DROP CONSTRAINT claim_dependencies_kind_check")
    op.execute(
        "ALTER TABLE claim_dependencies "
        "ADD CONSTRAINT claim_dependencies_kind_check "
        "CHECK (kind IN ('evidential','research'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claim_dependencies DROP CONSTRAINT claim_dependencies_kind_check")
    op.execute(
        "ALTER TABLE claim_dependencies "
        "ADD CONSTRAINT claim_dependencies_kind_check "
        "CHECK (kind IN ('evidential','investigative'))"
    )
