"""Commit boundary ORM models (T2.15-T2.18, §5.2.2, §14.2)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, created_at_column


class ORMDomainRevision(Base):
    __tablename__ = "domain_revisions"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    updated_at: Mapped[datetime] = created_at_column()


class ORMCommitAttempt(Base):
    __tablename__ = "commit_attempts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    status: Mapped[str] = mapped_column(Text, nullable=False, default="prepared")
    staging_hash: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("workspace_manifests.id")
    )
    base_knowledge_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    base_dependency_graph_revision: Mapped[int] = mapped_column(BigInteger, nullable=False)
    prepared_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ORMKnowledgeWriteGate(Base):
    """The knowledge writer gate (§14.1, T4.4): exactly one scope='global'
    row. Acquired with a single CAS UPDATE (NOWAIT semantics): a held,
    unexpired gate by another owner is a conflict, an expired lease is
    taken over. The holder's lease bounds the crash-recovery window; the
    gate is never held across a long DB transaction."""

    __tablename__ = "knowledge_write_gate"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    owner_kind: Mapped[str | None] = mapped_column(Text)
    owner_id: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[int | None] = mapped_column(Integer)
    acquired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
