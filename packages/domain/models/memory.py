"""ORM models for the memory model (T3.1, §8, §14).

The lifecycle source of truth is ``claim_assessment_heads``:
``current ⇔ current_assessment_id AND epistemic_status are set``
(§14.1). The ``claims`` row holds only the stable entity + freshness.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMClaim(Base):
    __tablename__ = "claims"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(Text, nullable=False)
    freshness_status: Mapped[str] = mapped_column(Text, nullable=False, default="unknown")
    valid_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reverify_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    dependency_fingerprint: Mapped[str | None] = mapped_column(Text)
    topic: Mapped[str | None] = mapped_column(Text)
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class ORMClaimDependency(Base):
    __tablename__ = "claim_dependencies"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    from_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    to_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    kind: Mapped[str] = mapped_column(Text, nullable=False, default="evidential")
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        UniqueConstraint("from_claim_id", "to_claim_id", "kind"),
        CheckConstraint("from_claim_id <> to_claim_id"),
    )


class ORMEvidence(Base):
    __tablename__ = "evidence"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    evidence_kind: Mapped[str] = mapped_column(Text, nullable=False)
    identity_hash: Mapped[str] = mapped_column(Text, nullable=False)
    scope: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    source_id: Mapped[uuid.UUID | None] = mapped_column()
    chunk_id: Mapped[str | None] = mapped_column(Text)
    observation_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    environment_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("environment_manifests.id", ondelete="SET NULL")
    )
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (UniqueConstraint("claim_id", "evidence_kind", "identity_hash"),)


class ORMClaimRevision(Base):
    __tablename__ = "claim_revisions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    session_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("sessions.id", ondelete="SET NULL"))
    previous_value: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    new_value: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    changed_at: Mapped[datetime] = created_at_column()
    reason_audit_event_id: Mapped[uuid.UUID | None] = mapped_column()


class ORMClaimAssessment(Base):
    __tablename__ = "claim_assessments"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    effective_grade: Mapped[str] = mapped_column(Text, nullable=False)
    epistemic_status: Mapped[str] = mapped_column(Text, nullable=False)
    rules_version: Mapped[str] = mapped_column(Text, nullable=False)
    rules_hash: Mapped[str] = mapped_column(Text, nullable=False)
    source_independence_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    environment_independence_snapshot_id: Mapped[uuid.UUID | None] = mapped_column()
    evidence_set_hash: Mapped[str] = mapped_column(Text, nullable=False)
    assessed_scope: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    confidence: Mapped[float] = mapped_column(nullable=False)
    valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    invalidation_reason: Mapped[str | None] = mapped_column(Text)
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sessions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class ORMClaimAssessmentHead(Base):
    """The lifecycle source of truth for a (claim, config snapshot) pair."""

    __tablename__ = "claim_assessment_heads"

    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"), primary_key=True)
    config_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("config_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    assessment_state: Mapped[str] = mapped_column(Text, nullable=False)
    current_assessment_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("claim_assessments.id", ondelete="SET NULL")
    )
    epistemic_status: Mapped[str | None] = mapped_column(Text)
    prepared_by: Mapped[str] = mapped_column(Text, nullable=False, default="session")
    updated_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint(
            "(assessment_state = 'current' AND current_assessment_id IS NOT NULL"
            " AND epistemic_status IS NOT NULL)"
            " OR (assessment_state IN ('pending','invalid')"
            " AND current_assessment_id IS NULL AND epistemic_status IS NULL)"
        ),
    )


class ORMAssessmentEvidence(Base):
    __tablename__ = "assessment_evidence"

    assessment_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("claim_assessments.id", ondelete="CASCADE"), primary_key=True
    )
    evidence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("evidence.id", ondelete="CASCADE"), primary_key=True
    )
    role: Mapped[str] = mapped_column(Text, nullable=False, primary_key=True)


class ORMOperatorAttestation(Base):
    __tablename__ = "operator_attestations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    supporting_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_column()


class ORMSource(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    source_type: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_uri: Mapped[str | None] = mapped_column(Text)
    retrieved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str | None] = mapped_column(Text)
    # column is named ``metadata`` in the DB; the ORM attribute is ``meta``
    # because ``metadata`` is taken by the SQLAlchemy declarative base
    meta: Mapped[JsonDict] = mapped_column("metadata", JSONB, nullable=False, default=dict)
    parent_source_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("sources.id", ondelete="SET NULL")
    )


class ORMSourceIndependenceSnapshot(Base):
    __tablename__ = "source_independence_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    thresholds: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    psl_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    uri_normalizer_version: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ORMSourceIndependenceMember(Base):
    __tablename__ = "source_independence_members"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("source_independence_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)


class ORMEnvironmentManifest(Base):
    __tablename__ = "environment_manifests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    protocol_hash: Mapped[str | None] = mapped_column(Text)
    implementation_hash: Mapped[str | None] = mapped_column(Text)
    code_lineage: Mapped[str | None] = mapped_column(Text)
    dataset_hash: Mapped[str | None] = mapped_column(Text)
    dataset_lineage: Mapped[str | None] = mapped_column(Text)
    toolchain_hash: Mapped[str | None] = mapped_column(Text)
    dependency_hash: Mapped[str | None] = mapped_column(Text)
    runtime_hash: Mapped[str | None] = mapped_column(Text)
    hardware_hash: Mapped[str | None] = mapped_column(Text)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    data_order_hash: Mapped[str | None] = mapped_column(Text)
    normalizer_version: Mapped[str] = mapped_column(Text, nullable=False, default="env-v1")
    created_at: Mapped[datetime] = created_at_column()

    # T4.6 (§8.7.3): content hash of the FULL field set — content-
    # addressed dedup (uq_environment_manifests_hash)
    manifest_hash: Mapped[str] = mapped_column(Text, nullable=False, default="")


class ORMEnvironmentIndependenceSnapshot(Base):
    __tablename__ = "environment_independence_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    algorithm_version: Mapped[str] = mapped_column(Text, nullable=False)
    rules_hash: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()


class ORMEnvironmentIndependenceMember(Base):
    __tablename__ = "environment_independence_members"

    snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment_independence_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    environment_manifest_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("environment_manifests.id", ondelete="CASCADE"), primary_key=True
    )
    group_id: Mapped[str] = mapped_column(Text, nullable=False)
    relation: Mapped[str] = mapped_column(Text, nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)


class ORMCheckpoint(Base):
    __tablename__ = "checkpoints"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"))
    workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspace_manifests.id", ondelete="SET NULL")
    )
    database_commit_id: Mapped[str | None] = mapped_column(Text)
    knowledge_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    dependency_graph_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (UniqueConstraint("session_id"),)


class ORMClosureManifest(Base):
    """Immutable, content-addressed reverse-closure snapshot (T4.2, §8.6).

    ``id = uuid5(CLOSURE_MANIFEST_UUID5_NAMESPACE, sha256)``; the same
    closure under the same graph revision resolves to the same row, so
    manifest creation is dedup-by-content (UNIQUE sha256).
    """

    __tablename__ = "closure_manifests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    root_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    graph_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    #: ordered (topological rank, claim id) list of the closure members
    claim_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    #: claim id (str) -> depth (direct dependents of the root are depth 1)
    ranks: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False)
    count: Mapped[int] = mapped_column(nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    created_at: Mapped[datetime] = created_at_column()

    __table_args__ = (
        CheckConstraint("count = jsonb_array_length(claim_ids)"),
    )


class ORMDependencyInvalidationBarrier(Base):
    """Durable work item / GC root for a large invalidation closure (T4.2, §8.6).

    The cursor (``next_offset``) moves only in the same transaction as
    the idempotent invalidation batch it advances. Open barriers
    (discovering/active/closing/blocked) keep ancestor protection on
    their closure (§8.6).
    """

    __tablename__ = "dependency_invalidation_barriers"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    root_claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    graph_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    generation: Mapped[int] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    closure_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("closure_manifests.id", ondelete="RESTRICT")
    )
    member_count: Mapped[int] = mapped_column(nullable=False, default=0)
    next_offset: Mapped[int] = mapped_column(nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    updated_at: Mapped[datetime] = created_at_column()
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("root_claim_id", "generation"),
        CheckConstraint("status IN ('discovering','active','closing','resolved','blocked')"),
        CheckConstraint("(closure_manifest_id IS NULL) = (status = 'discovering')"),
        CheckConstraint("(resolved_at IS NULL) = (status <> 'resolved')"),
        CheckConstraint("generation >= 1"),
        CheckConstraint("next_offset >= 0"),
    )


class ORMReassessmentJob(Base):
    """Durable reassessment queue scaffold (T4.2; worker — T4.3, §14.1).

    The cascade enqueues one job per invalidated claim. The partial
    unique index keeps a single active (queued/leased/retry) job per
    (claim, target snapshot); terminal rows stay for history.
    """

    __tablename__ = "reassessment_jobs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    claim_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("claims.id", ondelete="CASCADE"))
    target_config_snapshot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("config_snapshots.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(nullable=False, default=0)
    enqueued_at: Mapped[datetime] = created_at_column()
    attempts: Mapped[int] = mapped_column(nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(nullable=False, default=78)
    error_class: Mapped[str | None] = mapped_column(Text)
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('queued','leased','retry','blocked','completed')"),
        CheckConstraint("(lease_owner IS NULL) = (lease_expires_at IS NULL)"),
        CheckConstraint("(blocked_at IS NULL) = (status <> 'blocked')"),
        CheckConstraint("(completed_at IS NULL) = (status <> 'completed')"),
    )


class ORMBackupManifest(Base):
    __tablename__ = "backup_manifests"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True)
    database_recovery_point: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_inventory_hash: Mapped[str] = mapped_column(Text, nullable=False)
    artifact_inventory_artifact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("artifacts.id", ondelete="SET NULL")
    )
    retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
