"""SQLAlchemy records for the first operational PostgreSQL schema slice."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain import (
    ActionState,
    EventType,
    IdempotencyClass,
    PolicyDecision,
    QuestionOrigin,
    QuestionState,
    SessionState,
)
from packages.persistence.base import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")


def _allowed_values(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{value}'" for value in values)
    return f"{column} IN ({quoted})"


class ConfigSnapshotRecord(Base):
    __tablename__ = "config_snapshots"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("activation_mode", ["bootstrap", "offline", "online"]),
            name="activation_mode_allowed",
        ),
        CheckConstraint(
            _allowed_values(
                "activation_state",
                [
                    "draft",
                    "preparing_heads",
                    "ready",
                    "publishing",
                    "post_publish",
                    "post_publish_blocked",
                    "active",
                    "superseded",
                    "failed",
                ],
            ),
            name="activation_state_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    base_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id"), nullable=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    payload_sha256: Mapped[str] = mapped_column(String(64))
    sha: Mapped[str] = mapped_column(String(64), unique=True)
    activation_mode: Mapped[str] = mapped_column(String(16))
    activation_state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuntimeConfigHeadRecord(Base):
    __tablename__ = "runtime_config_heads"
    __table_args__ = (
        CheckConstraint("scope = 'global'", name="scope_global"),
        CheckConstraint("activation_fence >= 0", name="activation_fence_nonnegative"),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="lease_tuple_complete",
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    active_config_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id")
    )
    activating_config_snapshot_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id"), nullable=True
    )
    activation_fence: Mapped[int] = mapped_column(BigInteger, default=0)
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SystemConstantRecord(Base):
    __tablename__ = "system_constants"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class DomainRevisionRecord(Base):
    __tablename__ = "domain_revisions"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("scope", ["knowledge", "dependency_graph"]),
            name="scope_allowed",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class QuestionRecord(Base):
    __tablename__ = "questions"
    __table_args__ = (
        CheckConstraint("length(text) BETWEEN 1 AND 4096", name="text_length"),
        CheckConstraint(
            _allowed_values("origin", [origin.value for origin in QuestionOrigin]),
            name="origin_allowed",
        ),
        CheckConstraint(
            _allowed_values("state", [state.value for state in QuestionState]),
            name="state_allowed",
        ),
        CheckConstraint("priority BETWEEN -1000 AND 1000", name="priority_range"),
        Index("ix_questions_fifo", "state", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(32))
    origin_config_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id")
    )
    state: Mapped[str] = mapped_column(String(16))
    priority: Mapped[int] = mapped_column(Integer, default=0)
    parent_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("questions.id"), nullable=True
    )
    score_components: Mapped[dict[str, Any]] = mapped_column(JsonType)
    embedding_fingerprint: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionRecord(Base):
    __tablename__ = "sessions"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("state", [state.value for state in SessionState]),
            name="state_allowed",
        ),
        CheckConstraint("fence >= 0", name="fence_nonnegative"),
        CheckConstraint("next_audit_sequence >= 1", name="next_audit_sequence_positive"),
        CheckConstraint(
            "(lease_owner IS NULL) = (lease_expires_at IS NULL)",
            name="lease_tuple_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    config_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id")
    )
    question_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("questions.id"), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    next_audit_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ModelRunRecord(Base):
    __tablename__ = "model_runs"
    __table_args__ = (UniqueConstraint("session_id", "turn_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    turn_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    phase: Mapped[str] = mapped_column(String(64))
    model_fingerprint: Mapped[str] = mapped_column(String(64))
    context_manifest_sha256: Mapped[str] = mapped_column(String(64))
    context_manifest: Mapped[dict[str, Any]] = mapped_column(JsonType)
    prompt_version: Mapped[str] = mapped_column(String(128))
    tool_schema_sha256: Mapped[str] = mapped_column(String(64))
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer)
    finish_reason: Mapped[str | None] = mapped_column(String(64), nullable=True)
    output_schema_valid: Mapped[bool] = mapped_column()
    raw_response_artifact: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ActionRecord(Base):
    __tablename__ = "actions"
    __table_args__ = (
        UniqueConstraint("model_run_id"),
        UniqueConstraint("session_id", "idempotency_key"),
        CheckConstraint(
            _allowed_values("idempotency_class", [item.value for item in IdempotencyClass]),
            name="idempotency_class_allowed",
        ),
        CheckConstraint(
            _allowed_values("policy_decision", [item.value for item in PolicyDecision]),
            name="policy_decision_allowed",
        ),
        CheckConstraint(
            _allowed_values("state", [item.value for item in ActionState]),
            name="state_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    model_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("model_runs.id"))
    idempotency_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    idempotency_class: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(128))
    arguments_sha256: Mapped[str] = mapped_column(String(64))
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    state: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)


class AuditEventRecord(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        CheckConstraint("sequence >= 1", name="sequence_positive"),
        CheckConstraint(
            _allowed_values("type", [item.value for item in EventType]),
            name="type_allowed",
        ),
        CheckConstraint(
            _allowed_values("visibility", ["public", "private"]), name="visibility_allowed"
        ),
        Index(
            "uq_audit_events_session_sequence",
            "session_id",
            "sequence",
            unique=True,
            postgresql_where=text("session_id IS NOT NULL"),
            sqlite_where=text("session_id IS NOT NULL"),
        ),
        Index(
            "uq_audit_events_global_sequence",
            "sequence",
            unique=True,
            postgresql_where=text("session_id IS NULL"),
            sqlite_where=text("session_id IS NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id"), nullable=True
    )
    sequence: Mapped[int] = mapped_column(BigInteger)
    type: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(128))
    public_summary: Mapped[str] = mapped_column(Text)
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    visibility: Mapped[str] = mapped_column(String(16))


class OutboxEventRecord(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (CheckConstraint("attempts >= 0", name="attempts_nonnegative"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    audit_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("audit_events.id"), unique=True
    )
    topic: Mapped[str] = mapped_column(String(128))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
