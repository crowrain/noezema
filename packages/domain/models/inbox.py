"""messages + operator_commands ORM models (T1.4, §14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMMessage(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    sender: Mapped[str] = mapped_column(Text, nullable=False, default="owner")
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="created")
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    response_audit_event_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)


class ORMOperatorCommand(Base):
    __tablename__ = "operator_commands"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    actor_id: Mapped[str] = mapped_column(Text, nullable=False)
    type: Mapped[str] = mapped_column(Text, nullable=False)
    arguments: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="accepted")
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    reason: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result: Mapped[JsonDict | None] = mapped_column(JSONB)
