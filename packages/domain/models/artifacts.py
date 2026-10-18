"""Artifact store + workspace freeze ORM models (T2.11, T2.12, §5.11)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMArtifact(Base):
    __tablename__ = "artifacts"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sha256: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(Text, nullable=False, default="session_workspace")
    trust_class: Mapped[str] = mapped_column(Text, nullable=False, default="untrusted")
    created_at: Mapped[datetime] = created_at_column()


class ORMArtifactChunk(Base):
    __tablename__ = "artifact_chunks"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    artifact_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("artifacts.id", ondelete="CASCADE"), nullable=False
    )
    chunk_id: Mapped[str] = mapped_column(Text, nullable=False)
    byte_range: Mapped[str | None] = mapped_column(Text)
    origin_kind: Mapped[str] = mapped_column(Text, nullable=False)
    source_uri: Mapped[str | None] = mapped_column(Text)
    obtained_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    transform_chain: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    parser_fingerprint: Mapped[str | None] = mapped_column(Text)
    trust_class: Mapped[str] = mapped_column(Text, nullable=False, default="untrusted")
    usage_constraints: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)


class ORMWorkspaceManifest(Base):
    __tablename__ = "workspace_manifests"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("sessions.id", ondelete="SET NULL"))
    root_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    entry_count: Mapped[int] = mapped_column(Integer, nullable=False)
    total_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    frozen_at: Mapped[datetime] = created_at_column()


class ORMWorkspaceEntry(Base):
    __tablename__ = "workspace_entries"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    manifest_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("workspace_manifests.id", ondelete="CASCADE"), nullable=False
    )
    path: Mapped[str] = mapped_column(Text, nullable=False)
    size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)


class ORMStagingOp(Base):
    __tablename__ = "session_staging"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    op: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    payload_hash: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # T7.24 (EVAL-4 abort 2026-09-21): the per-session RECORDING order —
    # created_at is the constant start of the long phase-1 transaction
    # (now()), so only a durable sequence preserves the proposal order
    # the claim_index/evidence_index links refer to
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="recorded")
    created_at: Mapped[datetime] = created_at_column()
