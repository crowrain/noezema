"""T7.35 (ADR-0019): record the prompt content hash in model_runs.

A payload prompt pin is CONTENT, not a label: the payload carries the
sha256 of the prompt file bytes, the loader verifies it before the
session starts (fail-closed), and every model call now records both
``prompt_version`` (the label) and ``prompt_sha256`` (the content
identity actually sent to the model). Historical rows keep
``prompt_sha256 = NULL`` — they predate the pin; their prompt text is
recovered from git per ADR-0019 §3 (forensics table).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0025_prompt_content_pin"
down_revision: str | None = "0024_freshness_evergreen"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE model_runs ADD COLUMN prompt_sha256 TEXT")


def downgrade() -> None:
    op.execute("ALTER TABLE model_runs DROP COLUMN prompt_sha256")
