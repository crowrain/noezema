"""Add content-addressed artifacts and a transactional workspace manifest."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_transactional_artifacts"
down_revision: str | None = "0004_tool_broker"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "artifact_blobs",
        sa.Column("sha256", sa.String(length=64), primary_key=True),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_key", sa.String(length=256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        sa.CheckConstraint("length(sha256) = 64", name="sha256_length"),
        sa.UniqueConstraint("storage_key", name="uq_artifact_blobs_storage_key"),
    )
    op.create_table(
        "artifacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column(
            "action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actions.id"),
            nullable=False,
        ),
        sa.Column(
            "blob_sha256",
            sa.String(length=64),
            sa.ForeignKey("artifact_blobs.sha256"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("logical_name", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=128), nullable=False),
        sa.Column("encoding", sa.String(length=32), nullable=False),
        sa.Column("safety_status", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("kind IN ('workspace_file', 'standalone')", name="kind_allowed"),
        sa.CheckConstraint(
            "safety_status = 'untrusted_model_output'",
            name="safety_status_allowed",
        ),
        sa.UniqueConstraint("action_id", name="uq_artifacts_action_id"),
    )
    op.create_table(
        "workspace_files",
        sa.Column("path", sa.String(length=1024), primary_key=True),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            sa.ForeignKey("artifact_blobs.sha256"),
            nullable=False,
        ),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "updated_by_action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actions.id"),
            nullable=False,
        ),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        sa.UniqueConstraint(
            "updated_by_action_id",
            name="uq_workspace_files_updated_by_action_id",
        ),
    )
    op.create_table(
        "workspace_versions",
        sa.Column(
            "action_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("actions.id"),
            primary_key=True,
        ),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("path", sa.String(length=1024), nullable=False),
        sa.Column("revision", sa.BigInteger(), nullable=False),
        sa.Column(
            "artifact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("artifacts.id"),
            nullable=False,
        ),
        sa.Column("previous_content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "content_sha256",
            sa.String(length=64),
            sa.ForeignKey("artifact_blobs.sha256"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("revision >= 1", name="revision_positive"),
        sa.UniqueConstraint("path", "revision", name="uq_workspace_versions_path_revision"),
        sa.UniqueConstraint("artifact_id", name="uq_workspace_versions_artifact_id"),
    )


def downgrade() -> None:
    op.drop_table("workspace_versions")
    op.drop_table("workspace_files")
    op.drop_table("artifacts")
    op.drop_table("artifact_blobs")
