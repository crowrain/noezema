"""ORM models: Claims, Evidence, Assessments."""

import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.domain.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ORMClaim(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """A verifiable statement."""
    __tablename__ = "claims"

    statement: Mapped[str] = mapped_column(Text, nullable=False)
    claim_type: Mapped[str] = mapped_column(String(32), nullable=False)
    freshness_status: Mapped[str] = mapped_column(String(16), default="fresh")
    valid_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=datetime.utcnow, server_default=None
    )
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reverify_after: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    evidence: Mapped[list["ORMEvidence"]] = relationship(back_populates="claim")
    assessments: Mapped[list["ORMClaimAssessment"]] = relationship(
        back_populates="claim"
    )


class ORMClaimDependency(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Directed edge in the dependency graph."""
    __tablename__ = "claim_dependencies"

    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"))
    depends_on_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"))
    relation: Mapped[str] = mapped_column(String(16), nullable=False)


class ORMCounterevidenceResolution(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Audited record of removing a counterevidence."""
    __tablename__ = "counterevidence_resolutions"

    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"))
    evidence_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("evidence.id"))
    actor: Mapped[str] = mapped_column(String(32), nullable=False)
    basis: Mapped[str] = mapped_column(Text, nullable=False)


class ORMEvidence(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Evidence supporting or countering a claim."""
    __tablename__ = "evidence"

    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"))
    relation: Mapped[str] = mapped_column(String(16), nullable=False)  # supports/counters
    evidence_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    identity_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    scope: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    claim: Mapped[ORMClaim] = relationship(back_populates="evidence")


class ORMClaimAssessment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Deterministic assessment of a claim based on evidence set."""
    __tablename__ = "claim_assessments"

    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"))
    effective_grade: Mapped[str] = mapped_column(String(4), nullable=False)  # E0-E4
    epistemic_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rules_version: Mapped[int] = mapped_column(default=1)
    rules_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    evidence_set_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    valid: Mapped[bool] = mapped_column(default=True)
    created_in_session: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    claim: Mapped[ORMClaim] = relationship(back_populates="assessments")


class ORMClaimAssessmentHead(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Latest active assessment head per claim."""
    __tablename__ = "claim_assessment_heads"

    claim_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("claims.id"), unique=True)
    config_snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    assessment_state: Mapped[str] = mapped_column(String(16), default="current")  # current/pending/invalid
    current_assessment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    epistemic_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prepared_by: Mapped[str | None] = mapped_column(String(32), nullable=True)


class ORMConfigSnapshot(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Snapshot of rules/config at a point in time."""
    __tablename__ = "config_snapshots"

    version: Mapped[int] = mapped_column(nullable=False)
    rules_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    activation_state: Mapped[str] = mapped_column(String(32), default="active")
    activating_candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scope: Mapped[str] = mapped_column(String(32), default="default")


class ORMDomainRevision(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Monotonic revision counter per scope."""
    __tablename__ = "domain_revisions"

    scope: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    value: Mapped[int] = mapped_column(default=0)
