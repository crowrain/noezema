"""questions ORM model (T1.4, §14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMQuestion(Base):
    __tablename__ = "questions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    origin: Mapped[str] = mapped_column(Text, nullable=False)
    origin_config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("config_snapshots.id")
    )
    state: Mapped[str] = mapped_column(Text, nullable=False, default="candidate")
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("questions.id"))
    score_components: Mapped[JsonDict | None] = mapped_column(JSONB)
    embedding_fingerprint: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<ORMQuestion {self.state} {self.text[:40]!r}>"
