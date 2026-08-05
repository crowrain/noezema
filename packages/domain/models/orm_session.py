"""ORM model: Session."""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from packages.domain.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ORMQuestion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Research question in the queue."""
    __tablename__ = "questions"

    statement: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(String(32), default="seeded")
    resolved: Mapped[bool] = mapped_column(default=False)

    # Relations
    sessions: Mapped[list["ORMSession"]] = relationship(back_populates="question")


class ORMAction(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """One tool action proposed by the model and executed by orchestrator."""
    __tablename__ = "actions"

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id"))
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    arguments: Mapped[str | None] = mapped_column(Text, nullable=True)
    result: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="action_proposed")
    step: Mapped[int] = mapped_column(default=0)
    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    turn_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    session: Mapped["ORMSession"] = relationship(back_populates="actions")


class ORMModelRun(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Record of one LLM API call."""
    __tablename__ = "model_runs"

    model: Mapped[str] = mapped_column(String(128), nullable=False)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    latency_ms: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class ORMAuditEvent(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Append-only audit event."""
    __tablename__ = "audit_events"

    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    session_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    payload: Mapped[str | None] = mapped_column(Text, nullable=True)


class ORMMessage(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Message from human to the thinker."""
    __tablename__ = "messages"

    sender: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    priority: Mapped[str] = mapped_column(String(16), default="normal")
    state: Mapped[str] = mapped_column(String(16), default="queued")

    # Relations
    session: Mapped["ORMSession"] = relationship(back_populates="messages")


class ORMCommitAttempt(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Commit attempt for reconciliation."""
    __tablename__ = "commit_attempts"

    session_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("sessions.id"))
    status: Mapped[str] = mapped_column(String(32), default="prepared")
    staging_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    session: Mapped["ORMSession"] = relationship(back_populates="commit_attempts")


class ORMSession(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    """Cognitive session — one research cycle."""
    __tablename__ = "sessions"

    state: Mapped[str] = mapped_column(String(32), default="created")
    question_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("questions.id"), nullable=True
    )
    config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_progress_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    phase_deadline: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    abort_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)

    # Relations
    question: Mapped["ORMQuestion | None"] = relationship(back_populates="sessions")
    actions: Mapped[list[ORMAction]] = relationship(back_populates="session")
    commit_attempts: Mapped[list[ORMCommitAttempt]] = relationship(
        back_populates="session"
    )
    messages: Mapped[list[ORMMessage]] = relationship(back_populates="session")
