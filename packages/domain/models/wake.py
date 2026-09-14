"""wake_scheduler_state ORM model (T3.29, §5.2.1).

Durable per-node wake state: consecutive failed sessions, the exponential
backoff window, the last session outcome, and the auto-pause reason. The
operator-visible pause itself lives in ``system_constants.node_state``; this
row carries the bookkeeping that produces and explains it.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, created_at_column


class ORMWakeSchedulerState(Base):
    __tablename__ = "wake_scheduler_state"

    node_id: Mapped[str] = mapped_column(Text, primary_key=True)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    backoff_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_session_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_session_state: Mapped[str | None] = mapped_column(Text)
    last_failure_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 'operator' | 'consecutive_failures' | None (set only while paused)
    paused_reason: Mapped[str | None] = mapped_column(Text)
    updated_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<ORMWakeSchedulerState {self.node_id} fails={self.consecutive_failures}>"
