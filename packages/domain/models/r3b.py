"""ORM models for stage 3b: reassessment jobs, closure manifests,
dependency barriers, runtime config head, writer admission state.

Spec: ARCHITECTURE.md sec 5.9.1, 8.6, 8.7.2.
"""

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import (
    Base,
    TimestampMixin,
    UUIDPrimaryKeyMixin,
)

# In-flight statuses for the partial unique index below; completed/blocked
# rows are history and never constrain enqueueing a fresh job.
_ACTIVE_JOB_STATUSES = ("queued", "leased", "retry")
_ACTIVE_JOB_PREDICATE = sa.text(
    "status IN ('queued', 'leased', '" + "retry')"
)


class ORMReassessmentJob(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    """Durable reassessment work item, actor system:reassessment.

    One in-flight job per (claim, target config snapshot) pair: the partial
    unique index uq_reassessment_jobs_active covers exactly the active
    statuses (queued, leased, retry). completed/blocked rows are history
    and never block a fresh attempt for the same pair.
    """

    __tablename__ = "reassessment_jobs"
    __table_args__ = (
        sa.Index(
            "uq_reassessment_jobs_active",
            "claim_id",
            "target_config_snapshot_id",
            unique=True,
            postgresql_where=_ACTIVE_JOB_PREDICATE,
            sqlite_where=_ACTIVE_JOB_PREDICATE,
        ),
        sa.Index(
            "ix_reassessment_jobs_runnable",
            "status",
            "priority",
            "next_attempt_at",
        ),
    )

    claim_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("claims.id"), nullable=False
    )
    target_config_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("config_snapshots.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="queued")
    reason: Mapped[str] = mapped_column(
        String(32), nullable=False, default="invalidation"
    )
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=2)
    enqueued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow
    )
    attempts: Mapped[int] = mapped_column("attempts", Integer, default=0)
    max_attempts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error_class: Mapped[str | None] = mapped_column("error_class", String(16), nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(
        "lease_owner", String(128), nullable=True
    )
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        "lease_expires_at", DateTime(timezone=True), nullable=True
    )
    last_error: Mapped[str | None] = mapped_column("last_error", Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column(
        "next_attempt_at", DateTime(timezone=True), nullable=True
    )
    blocked_at: Mapped[datetime | None] = mapped_column(
        "blocked_at", DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        "completed_at", DateTime(timezone=True), nullable=True    )


class ORMClosureManifest(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    """Immutable, content-addressed manifest of a reverse dependency closure.

    Rows are never mutated after creation. Identical content (root claim,
    graph revision, ordered claim ids, aligned ranks) yields the same
    sha256, so manifests are deduplicated by that unique hash.
    """

    __tablename__ = "closure_manifests"

    root_claim_id: Mapped[uuid.UUID] = mapped_column(        "root_claim_id", Uuid, ForeignKey("claims.id"), nullable=False
    )
    graph_rev: Mapped[int] = mapped_column("graph_rev", Integer, nullable=False)
    claim_ids: Mapped[list] = mapped_column("claim_ids", JSON, nullable=False)
    ranks: Mapped[list] = mapped_column("ranks", JSON, nullable=False)
    count: Mapped[int] = mapped_column("count", Integer, nullable=False)
    sha256: Mapped[str] = mapped_column("sha256", String(64), unique=True, nullable=False)


class ORMDependencyBarrier(Base, TimestampMixin, UUIDPrimaryKeyMixin):
    """Durable work item / GC root processing a large closure in batches.

    After a crash the processor scans barriers in discovering|active|closing
    and resumes from the durable cursor next_offset; batch application is
    idempotent, so re-driving an already-applied batch is safe.
    """

    __tablename__ = "dependency_barriers"
    __table_args__ = (
        sa.UniqueConstraint(
            "root_claim_id", "generation",
            name="uq_dependency_barriers_root_claim_generation",
        ),
    )

    root_claim_id: Mapped[uuid.UUID] = mapped_column(
        "root_claim_id", Uuid, ForeignKey("claims.id"), nullable=False
    )
    manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        "manifest_id", Uuid, ForeignKey("closure_manifests.id"), nullable=True
    )
    status: Mapped[str] = mapped_column("status", String(16), default="discovering", nullable=False)
    generation: Mapped[int] = mapped_column("generation", Integer, nullable=False, default=1)
    next_offset: Mapped[int] = mapped_column("next_offset", Integer, nullable=False, default=0)
    total_count: Mapped[int] = mapped_column("total_count", Integer, nullable=False, default=0)
    attempts: Mapped[int] = mapped_column("attempts", Integer, nullable=False, default=0)
    error_class: Mapped[str | None] = mapped_column("error_class", String(16), nullable=True)
    last_error: Mapped[str | None] = mapped_column("last_error", Text, nullable=True)
    next_attempt_at: Mapped[datetime | None] = mapped_column("next_attempt_at",
    DateTime(timezone=True), nullable=True)
    resolved_at: Mapped[datetime | None] = mapped_column("resolved_at",
    DateTime(timezone=True), nullable=True)
    blocked_at: Mapped[datetime | None] = mapped_column("blocked_at",
    DateTime(timezone=True),
    nullable=True)




class ORMRuntimeConfigHead(Base, TimestampMixin):
    """Single-row effective-config pointer (the row is always id=1). A config snapshot is effective only if it equals active_config_snapshot_id. A non-NULL activating_config_snapshot_id means an online activation is in flight; the worker must not start validation batches while it is set.
    """

    __tablename__ = "runtime_config_heads"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(
        "id", Integer, primary_key=True, server_default="1"
    )
    active_config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        "active_config_snapshot_id", Uuid, ForeignKey("config_snapshots.id"), nullable=True
    )
    activating_config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        "activating_config_snapshot_id", Uuid, ForeignKey("config_snapshots.id"), nullable=True
    )
    fence: Mapped[int] = mapped_column(
        "fence", Integer, nullable=False, default=0, server_default="0"
    )


class ORMWriterState(Base, TimestampMixin):
    """Single-row writer admission state (the row is always id=1). Set atomically by a session entering consolidation before heavy validation. The reassessment worker must not start a validation/write batch while a live commit intent exists. A session commit clears it in its terminal transaction.
    """

    __tablename__ = "writer_state"
    __table_args__ = (CheckConstraint("id = 1"),)

    id: Mapped[int] = mapped_column(
        "id", Integer, primary_key=True, server_default="1"
    )
    commit_intent_session_id: Mapped[uuid.UUID | None] = mapped_column(
        "commit_intent_session_id", Uuid, ForeignKey("sessions.id"), nullable=True
    )
    commit_intent_at: Mapped[datetime | None] = mapped_column(
        "commit_intent_at", DateTime(timezone=True)
    )
    commit_intent_lease_expires_at: Mapped[datetime | None] = mapped_column(
        "commit_intent_lease_expires_at", DateTime(timezone=True)
    )
