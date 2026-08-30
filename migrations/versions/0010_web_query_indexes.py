"""Add covering order indexes for the owner Query API."""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0010_web_query_indexes"
down_revision: str | None = "0009_human_input_and_operator_inbox"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_audit_events_public_timeline",
        "audit_events",
        ["visibility", "occurred_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_sessions_query",
        "sessions",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_messages_query",
        "messages",
        ["created_at", "id"],
        unique=False,
    )
    op.create_index(
        "ix_operator_commands_query",
        "operator_commands",
        ["created_at", "id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_operator_commands_query", table_name="operator_commands")
    op.drop_index("ix_messages_query", table_name="messages")
    op.drop_index("ix_sessions_query", table_name="sessions")
    op.drop_index("ix_audit_events_public_timeline", table_name="audit_events")
