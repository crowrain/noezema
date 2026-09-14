"""audit_events + outbox_events ORM models (T1.4, §14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMAuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    occurred_at: Mapped[datetime] = created_at_column()
    actor: Mapped[str | None] = mapped_column(Text)
    public_summary: Mapped[str | None] = mapped_column(Text)
    payload: Mapped[JsonDict | None] = mapped_column(JSONB)
    visibility: Mapped[str] = mapped_column(Text, nullable=False, default="operator")


class ORMOutboxEvent(Base):
    __tablename__ = "outbox_events"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    audit_event_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("audit_events.id"), nullable=False, unique=True
    )
    topic: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
