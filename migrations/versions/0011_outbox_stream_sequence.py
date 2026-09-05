"""Add a durable total order for replayable outbox streams."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011_outbox_stream_sequence"
down_revision: str | None = "0010_web_query_indexes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "runtime_controls",
        sa.Column(
            "next_outbox_sequence",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.alter_column("runtime_controls", "next_outbox_sequence", server_default=None)
    op.create_check_constraint(
        "next_outbox_sequence_positive",
        "runtime_controls",
        "next_outbox_sequence >= 1",
    )
    op.add_column(
        "outbox_events",
        sa.Column("stream_sequence", sa.BigInteger(), nullable=True),
    )
    op.create_check_constraint(
        "stream_sequence_positive",
        "outbox_events",
        "stream_sequence IS NULL OR stream_sequence >= 1",
    )
    op.create_index(
        "uq_outbox_events_stream_sequence",
        "outbox_events",
        ["stream_sequence"],
        unique=True,
        postgresql_where=sa.text("stream_sequence IS NOT NULL"),
    )
    op.create_index(
        "ix_outbox_events_unsequenced",
        "outbox_events",
        ["created_at", "id"],
        unique=False,
        postgresql_where=sa.text("stream_sequence IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_outbox_events_unsequenced", table_name="outbox_events")
    op.drop_index("uq_outbox_events_stream_sequence", table_name="outbox_events")
    op.drop_constraint(
        op.f("ck_outbox_events_stream_sequence_positive"),
        "outbox_events",
        type_="check",
    )
    op.drop_column("outbox_events", "stream_sequence")
    op.drop_constraint(
        op.f("ck_runtime_controls_next_outbox_sequence_positive"),
        "runtime_controls",
        type_="check",
    )
    op.drop_column("runtime_controls", "next_outbox_sequence")
