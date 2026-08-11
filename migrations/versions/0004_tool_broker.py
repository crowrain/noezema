"""Persist replayable Tool Broker inputs and policy snapshots."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

revision: str = "0004_tool_broker"
down_revision: str | None = "0003_knowledge_commit_slice"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    if not context.is_offline_mode():
        action_count = op.get_bind().scalar(sa.text("SELECT count(*) FROM actions"))
        if action_count:
            raise RuntimeError(
                "0004_tool_broker requires an empty pre-MVP actions table because prior rows "
                "did not preserve canonical arguments"
            )

    op.add_column(
        "actions",
        sa.Column(
            "arguments_json",
            sa.Text(),
            nullable=False,
            server_default=sa.text("'{}'"),
        ),
    )
    op.alter_column("actions", "arguments_json", server_default=None)
    op.add_column("actions", sa.Column("policy_version", sa.String(length=256), nullable=True))
    op.add_column("actions", sa.Column("policy_hash", sa.String(length=64), nullable=True))
    op.add_column("actions", sa.Column("policy_reason", sa.String(length=256), nullable=True))
    op.add_column(
        "actions",
        sa.Column(
            "attempt_count",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("0"),
        ),
    )
    op.alter_column("actions", "attempt_count", server_default=None)
    op.create_check_constraint(
        "attempt_count_nonnegative",
        "actions",
        "attempt_count >= 0",
    )
    op.create_check_constraint(
        "policy_snapshot_complete",
        "actions",
        "(policy_version IS NULL AND policy_hash IS NULL AND policy_reason IS NULL) OR "
        "(policy_version IS NOT NULL AND policy_hash IS NOT NULL AND policy_reason IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_actions_policy_snapshot_complete", "actions", type_="check")
    op.drop_constraint("ck_actions_attempt_count_nonnegative", "actions", type_="check")
    op.drop_column("actions", "attempt_count")
    op.drop_column("actions", "policy_reason")
    op.drop_column("actions", "policy_hash")
    op.drop_column("actions", "policy_version")
    op.drop_column("actions", "arguments_json")
