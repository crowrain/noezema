"""sessions, model_runs, actions ORM models (T1.4, §14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMSession(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="created")
    lease_owner: Mapped[str | None] = mapped_column(Text)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_progress_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    phase_deadline: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    abort_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commit_intent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    commit_attempt_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    question_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, ForeignKey("questions.id"))
    base_workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    committed_workspace_manifest_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    config_snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("config_snapshots.id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    termination_reason: Mapped[str | None] = mapped_column(Text)
    # T5.2 (stage 4): the structured multi-step plan proposed in the
    # planning phase (observable artifact; NULL = MVP fixed template).
    # Written once per session, never mutated.
    plan: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    plan_sha256: Mapped[str | None] = mapped_column(Text)
    # T5.3 (stage 4): the verifier's structured report (observable
    # artifact; NULL = MVP no-op verifying phase or fallback). Written
    # once per session, never mutated. No grade/confidence by design.
    verification: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    verification_sha256: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_column()

    def __repr__(self) -> str:
        return f"<ORMSession {self.state} {self.id}>"


class ORMModelRun(Base):
    __tablename__ = "model_runs"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    turn_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    phase: Mapped[str | None] = mapped_column(Text)
    model_fingerprint: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    context_manifest_hash: Mapped[str | None] = mapped_column(Text)
    context_manifest_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    prompt_version: Mapped[str | None] = mapped_column(Text)
    tool_schema_hash: Mapped[str | None] = mapped_column(Text)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    latency_ms: Mapped[float] = mapped_column(Numeric(12, 3), nullable=False, default=0)
    finish_reason: Mapped[str | None] = mapped_column(Text)
    output_schema_valid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    raw_response_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    raw_retention_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_column()


class ORMAction(Base):
    __tablename__ = "actions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    session_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False
    )
    model_run_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("model_runs.id"), nullable=False, unique=True)
    idempotency_key: Mapped[str] = mapped_column(Text, nullable=False)
    idempotency_class: Mapped[str] = mapped_column(Text, nullable=False)
    tool: Mapped[str] = mapped_column(Text, nullable=False)
    arguments_hash: Mapped[str] = mapped_column(Text, nullable=False)
    policy_decision: Mapped[str | None] = mapped_column(Text)
    state: Mapped[str] = mapped_column(Text, nullable=False, default="proposed")
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_artifact_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    error_code: Mapped[str | None] = mapped_column(Text)
