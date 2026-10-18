"""T7.32 (ADR-0017): the ``evergreen`` freshness status.

The reverify deadline now exists ONLY for a claim about the present
(the question anchors the date relatively): a claim about a fixed
point (explicit question date / dateless question with the model's
as_of) has NO deadline — ``reverify_after`` is NULL and, under the
§8.6/T3.7 rule, its freshness is ``evergreen`` (no deadline by
construction, valid forever — distinct from ``unknown``). The
``claims.freshness_status`` display cache (0004) must accept the new
closed-set member.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0024_freshness_evergreen"
down_revision: str | None = "0023_staging_seq"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE claims DROP CONSTRAINT claims_freshness_status_check")
    op.execute(
        "ALTER TABLE claims "
        "ADD CONSTRAINT claims_freshness_status_check "
        "CHECK (freshness_status IN ('fresh','due','stale','unknown','evergreen'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE claims DROP CONSTRAINT claims_freshness_status_check")
    op.execute(
        "ALTER TABLE claims "
        "ADD CONSTRAINT claims_freshness_status_check "
        "CHECK (freshness_status IN ('fresh','due','stale','unknown'))"
    )
