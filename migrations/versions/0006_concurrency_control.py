"""Add revision scopes, session-fenced writer intents and commit fences."""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision: str = "0006_concurrency_control"
down_revision: str | None = "0005_transactional_artifacts"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

REVISION_SCOPES = ("knowledge", "dependency_graph", "workspace", "artifact_store")


def _in(values: Sequence[str]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def upgrade() -> None:
    if not context.is_offline_mode():
        unfinished_count = op.get_bind().scalar(
            sa.text(
                "SELECT count(*) FROM commit_attempts WHERE status IN ('prepared', 'reconciling')"
            )
        )
        if unfinished_count:
            raise RuntimeError(
                "0006_concurrency_control cannot safely synthesize fencing tokens for "
                "unfinished knowledge commits; reconcile or abort them before upgrading"
            )

    op.drop_constraint(op.f("ck_domain_revisions_scope_allowed"), "domain_revisions", type_="check")
    op.create_check_constraint(
        "scope_allowed",
        "domain_revisions",
        f"scope IN ({_in(REVISION_SCOPES)})",
    )
    op.execute(
        sa.text(
            "INSERT INTO domain_revisions (scope, revision, updated_at) VALUES "
            "('workspace', 0, CURRENT_TIMESTAMP), "
            "('artifact_store', 0, CURRENT_TIMESTAMP) "
            "ON CONFLICT (scope) DO NOTHING"
        )
    )
    op.create_table(
        "writer_intents",
        sa.Column(
            "scope",
            sa.String(length=32),
            sa.ForeignKey("domain_revisions.scope"),
            primary_key=True,
        ),
        sa.Column("fence", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column(
            "holder_session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=True,
        ),
        sa.Column("holder_operation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("holder_owner", sa.String(length=128), nullable=True),
        sa.Column("holder_session_fence", sa.BigInteger(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("base_revision", sa.BigInteger(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("fence >= 0", name="fence_nonnegative"),
        sa.CheckConstraint(
            "(holder_session_id IS NULL AND holder_operation_id IS NULL "
            "AND holder_owner IS NULL AND holder_session_fence IS NULL "
            "AND lease_expires_at IS NULL AND base_revision IS NULL) OR "
            "(holder_session_id IS NOT NULL AND holder_operation_id IS NOT NULL "
            "AND holder_owner IS NOT NULL AND holder_session_fence IS NOT NULL "
            "AND holder_session_fence >= 1 AND lease_expires_at IS NOT NULL "
            "AND base_revision IS NOT NULL AND base_revision >= 0)",
            name="holder_tuple_complete",
        ),
    )
    op.execute(
        sa.text(
            "INSERT INTO writer_intents (scope, fence, holder_session_id, "
            "holder_operation_id, holder_owner, holder_session_fence, lease_expires_at, "
            "base_revision, updated_at) "
            "SELECT scope, 0, NULL, NULL, NULL, NULL, NULL, NULL, CURRENT_TIMESTAMP "
            "FROM domain_revisions ON CONFLICT (scope) DO NOTHING"
        )
    )
    for name in (
        "writer_lease_owner",
        "session_fence",
        "knowledge_writer_fence",
        "dependency_writer_fence",
        "writer_lease_expires_at",
    ):
        if name == "writer_lease_owner":
            column = sa.Column(name, sa.String(length=128), nullable=True)
        elif name == "writer_lease_expires_at":
            column = sa.Column(name, sa.DateTime(timezone=True), nullable=True)
        else:
            column = sa.Column(name, sa.BigInteger(), nullable=True)
        op.add_column("commit_attempts", column)
    op.create_check_constraint(
        "writer_fence_tuple_complete",
        "commit_attempts",
        "(writer_lease_owner IS NULL AND session_fence IS NULL "
        "AND knowledge_writer_fence IS NULL AND dependency_writer_fence IS NULL "
        "AND writer_lease_expires_at IS NULL) OR "
        "(writer_lease_owner IS NOT NULL AND session_fence IS NOT NULL "
        "AND session_fence >= 1 AND knowledge_writer_fence IS NOT NULL "
        "AND knowledge_writer_fence >= 1 AND dependency_writer_fence IS NOT NULL "
        "AND dependency_writer_fence >= 1 AND writer_lease_expires_at IS NOT NULL)",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_commit_attempts_writer_fence_tuple_complete"),
        "commit_attempts",
        type_="check",
    )
    op.drop_column("commit_attempts", "writer_lease_expires_at")
    op.drop_column("commit_attempts", "dependency_writer_fence")
    op.drop_column("commit_attempts", "knowledge_writer_fence")
    op.drop_column("commit_attempts", "session_fence")
    op.drop_column("commit_attempts", "writer_lease_owner")
    op.drop_table("writer_intents")
    op.execute(
        sa.text("DELETE FROM domain_revisions WHERE scope IN ('workspace', 'artifact_store')")
    )
    op.drop_constraint(op.f("ck_domain_revisions_scope_allowed"), "domain_revisions", type_="check")
    op.create_check_constraint(
        "scope_allowed",
        "domain_revisions",
        "scope IN ('knowledge', 'dependency_graph')",
    )
