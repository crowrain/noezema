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
    ClaimType,
    EpistemicStatus,
    EventType,
    EvidenceGrade,
    EvidenceKind,
    EvidenceUse,
    IdempotencyClass,
    MessageState,
    NodeState,
    OperatorCommandState,
    OperatorCommandType,
    PolicyDecision,
    QuestionOrigin,
    QuestionState,
    RevisionScope,
    SessionState,
)
from packages.persistence.base import Base

JsonType = JSON().with_variant(JSONB(), "postgresql")
NullableJsonType = JSON(none_as_null=True).with_variant(JSONB(none_as_null=True), "postgresql")


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
            _allowed_values("scope", [item.value for item in RevisionScope]),
            name="scope_allowed",
        ),
        CheckConstraint("revision >= 0", name="revision_nonnegative"),
    )

    scope: Mapped[str] = mapped_column(String(32), primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WriterIntentRecord(Base):
    """Stable per-scope row carrying a monotonic fencing token."""

    __tablename__ = "writer_intents"
    __table_args__ = (
        CheckConstraint("fence >= 0", name="fence_nonnegative"),
        CheckConstraint(
            "(holder_session_id IS NULL AND holder_operation_id IS NULL "
            "AND holder_owner IS NULL AND holder_session_fence IS NULL "
            "AND lease_expires_at IS NULL AND base_revision IS NULL) OR "
            "(holder_session_id IS NOT NULL AND holder_operation_id IS NOT NULL "
            "AND holder_owner IS NOT NULL AND holder_session_fence IS NOT NULL "
            "AND holder_session_fence >= 1 AND lease_expires_at IS NOT NULL "
            "AND base_revision IS NOT NULL AND base_revision >= 0)",
            name="holder_tuple_complete",
        ),
    )

    scope: Mapped[str] = mapped_column(
        String(32), ForeignKey("domain_revisions.scope"), primary_key=True
    )
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    holder_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id"), nullable=True
    )
    holder_operation_id: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True), nullable=True)
    holder_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    holder_session_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    base_revision: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RuntimeControlRecord(Base):
    """Singleton operator-plane state and global audit sequence."""

    __tablename__ = "runtime_controls"
    __table_args__ = (
        CheckConstraint("scope = 'global'", name="scope_global"),
        CheckConstraint(
            _allowed_values("node_state", [item.value for item in NodeState]),
            name="node_state_allowed",
        ),
        CheckConstraint("wake_generation >= 0", name="wake_generation_nonnegative"),
        CheckConstraint(
            "next_global_audit_sequence >= 1",
            name="next_global_audit_sequence_positive",
        ),
    )

    scope: Mapped[str] = mapped_column(String(16), primary_key=True)
    node_state: Mapped[str] = mapped_column(String(16))
    wake_generation: Mapped[int] = mapped_column(BigInteger, default=0)
    next_global_audit_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
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


class MessageRecord(Base):
    __tablename__ = "messages"
    __table_args__ = (
        CheckConstraint("length(sender) BETWEEN 1 AND 256", name="sender_length"),
        CheckConstraint("length(body) BETWEEN 1 AND 4096", name="body_length"),
        CheckConstraint("priority BETWEEN -1000 AND 1000", name="priority_range"),
        CheckConstraint(
            _allowed_values("state", [item.value for item in MessageState]),
            name="state_allowed",
        ),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        Index("ix_messages_delivery", "state", "priority", "created_at", "id"),
        Index("ix_messages_query", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    question_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("questions.id"), unique=True
    )
    sender: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    priority: Mapped[int] = mapped_column(Integer, default=0)
    state: Mapped[str] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    delivered_session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id"), nullable=True
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    answered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    response_audit_event_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("audit_events.id"), nullable=True
    )


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
        CheckConstraint("length(budget_sha256) = 64", name="budget_sha256_length"),
        CheckConstraint(
            "cognitive_deadline_at < host_deadline_at",
            name="deadline_order",
        ),
        CheckConstraint(
            "(soft_exhausted_at IS NULL) = (soft_exhaustion_reason IS NULL)",
            name="soft_exhaustion_tuple_complete",
        ),
        Index("ix_sessions_query", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    state: Mapped[str] = mapped_column(String(32))
    config_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id")
    )
    question_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("questions.id"), nullable=True
    )
    commit_attempt_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("commit_attempts.id"), nullable=True
    )
    lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    fence: Mapped[int] = mapped_column(BigInteger, default=0)
    next_audit_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    budget: Mapped[dict[str, Any]] = mapped_column(JsonType)
    budget_sha256: Mapped[str] = mapped_column(String(64))
    cognitive_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    host_deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    stop_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    abort_requested_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    soft_exhausted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    soft_exhaustion_reason: Mapped[str | None] = mapped_column(String(32), nullable=True)
    termination_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    terminal_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class OperatorCommandRecord(Base):
    __tablename__ = "operator_commands"
    __table_args__ = (
        CheckConstraint("length(actor_id) BETWEEN 1 AND 256", name="actor_id_length"),
        CheckConstraint("length(reason) BETWEEN 1 AND 256", name="reason_length"),
        CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        CheckConstraint(
            _allowed_values("type", [item.value for item in OperatorCommandType]),
            name="type_allowed",
        ),
        CheckConstraint(
            _allowed_values("state", [item.value for item in OperatorCommandState]),
            name="state_allowed",
        ),
        CheckConstraint(
            "(type IN ('stop_gracefully', 'abort_session') AND session_id IS NOT NULL) OR "
            "(type NOT IN ('stop_gracefully', 'abort_session') AND session_id IS NULL)",
            name="session_target_shape",
        ),
        Index("ix_operator_commands_dispatch", "state", "created_at", "id"),
        Index("ix_operator_commands_query", "created_at", "id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    idempotency_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), unique=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    actor_id: Mapped[str] = mapped_column(String(256))
    type: Mapped[str] = mapped_column(String(32))
    session_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("sessions.id"), nullable=True
    )
    arguments: Mapped[dict[str, Any]] = mapped_column(JsonType)
    state: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str] = mapped_column(String(256))
    result: Mapped[dict[str, Any] | None] = mapped_column(NullableJsonType, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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


class OrchestratorTurnRecord(Base):
    """Durable intent and outcome for one resumable model turn."""

    __tablename__ = "orchestrator_turns"
    __table_args__ = (
        UniqueConstraint("session_id", "ordinal"),
        UniqueConstraint("model_run_id"),
        Index("ix_orchestrator_turns_session_status", "session_id", "status"),
        CheckConstraint("ordinal >= 1", name="ordinal_positive"),
        CheckConstraint("session_fence >= 1", name="session_fence_positive"),
        CheckConstraint(
            _allowed_values(
                "phase",
                ["planning", "exploration", "verification", "consolidation"],
            ),
            name="phase_allowed",
        ),
        CheckConstraint(
            _allowed_values("status", ["prepared", "completed", "failed"]),
            name="status_allowed",
        ),
        CheckConstraint("length(request_sha256) = 64", name="request_sha256_length"),
        CheckConstraint("length(response_schema_sha256) = 64", name="schema_sha256_length"),
        CheckConstraint(
            "(status = 'prepared' AND model_run_id IS NULL AND result IS NULL "
            "AND error_code IS NULL AND completed_at IS NULL) OR "
            "(status = 'completed' AND model_run_id IS NOT NULL AND result IS NOT NULL "
            "AND error_code IS NULL AND completed_at IS NOT NULL) OR "
            "(status = 'failed' AND model_run_id IS NULL AND result IS NULL "
            "AND error_code IS NOT NULL AND completed_at IS NOT NULL)",
            name="outcome_tuple_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    ordinal: Mapped[int] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    lease_owner: Mapped[str] = mapped_column(String(128))
    session_fence: Mapped[int] = mapped_column(BigInteger)
    request: Mapped[dict[str, Any]] = mapped_column(JsonType)
    request_sha256: Mapped[str] = mapped_column(String(64))
    response_schema_sha256: Mapped[str] = mapped_column(String(64))
    model_run_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("model_runs.id"), nullable=True
    )
    result: Mapped[dict[str, Any] | None] = mapped_column(NullableJsonType, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


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
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint(
            "(policy_version IS NULL AND policy_hash IS NULL AND policy_reason IS NULL) OR "
            "(policy_version IS NOT NULL AND policy_hash IS NOT NULL "
            "AND policy_reason IS NOT NULL)",
            name="policy_snapshot_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    model_run_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("model_runs.id"))
    idempotency_key: Mapped[UUID] = mapped_column(Uuid(as_uuid=True))
    idempotency_class: Mapped[str] = mapped_column(String(32))
    tool: Mapped[str] = mapped_column(String(128))
    arguments_json: Mapped[str] = mapped_column(Text)
    arguments_sha256: Mapped[str] = mapped_column(String(64))
    policy_decision: Mapped[str | None] = mapped_column(String(32), nullable=True)
    policy_version: Mapped[str | None] = mapped_column(String(256), nullable=True)
    policy_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    policy_reason: Mapped[str | None] = mapped_column(String(256), nullable=True)
    state: Mapped[str] = mapped_column(String(32))
    attempt_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)
    error: Mapped[dict[str, Any] | None] = mapped_column(JsonType, nullable=True)


class ArtifactBlobRecord(Base):
    """Immutable physical content; reachability is controlled by metadata rows."""

    __tablename__ = "artifact_blobs"
    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
        CheckConstraint("length(sha256) = 64", name="sha256_length"),
        UniqueConstraint("storage_key"),
    )

    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ArtifactRecord(Base):
    """One durable logical artifact created by exactly one completed action."""

    __tablename__ = "artifacts"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("kind", ["workspace_file", "standalone"]),
            name="kind_allowed",
        ),
        CheckConstraint(
            "safety_status = 'untrusted_model_output'",
            name="safety_status_allowed",
        ),
        UniqueConstraint("action_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    action_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("actions.id"))
    blob_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("artifact_blobs.sha256"))
    kind: Mapped[str] = mapped_column(String(32))
    logical_name: Mapped[str] = mapped_column(String(1024))
    media_type: Mapped[str] = mapped_column(String(128))
    encoding: Mapped[str] = mapped_column(String(32))
    safety_status: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceFileRecord(Base):
    """The canonical mutable workspace manifest; content remains immutable."""

    __tablename__ = "workspace_files"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("size_bytes >= 0", name="size_nonnegative"),
    )

    path: Mapped[str] = mapped_column(String(1024), primary_key=True)
    artifact_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("artifacts.id"))
    content_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("artifact_blobs.sha256"))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger)
    updated_by_action_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actions.id"), unique=True
    )
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkspaceVersionRecord(Base):
    """Append-only COW history for reconciliation and checkpoint snapshots."""

    __tablename__ = "workspace_versions"
    __table_args__ = (
        CheckConstraint("revision >= 1", name="revision_positive"),
        UniqueConstraint("path", "revision"),
        UniqueConstraint("artifact_id"),
    )

    action_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("actions.id"), primary_key=True
    )
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    path: Mapped[str] = mapped_column(String(1024))
    revision: Mapped[int] = mapped_column(BigInteger)
    artifact_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("artifacts.id"))
    previous_content_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    content_sha256: Mapped[str] = mapped_column(String(64), ForeignKey("artifact_blobs.sha256"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


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
        Index("ix_audit_events_public_timeline", "visibility", "occurred_at", "id"),
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


class CommitAttemptRecord(Base):
    __tablename__ = "commit_attempts"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("status", ["prepared", "reconciling", "committed", "aborted"]),
            name="status_allowed",
        ),
        CheckConstraint("validated_knowledge_revision >= 0", name="knowledge_revision_nonnegative"),
        CheckConstraint(
            "validated_dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        CheckConstraint(
            _allowed_values("terminal_state", ["succeeded", "succeeded_partial"]),
            name="terminal_state_allowed",
        ),
        UniqueConstraint("session_id"),
        CheckConstraint(
            "(writer_lease_owner IS NULL AND session_fence IS NULL "
            "AND knowledge_writer_fence IS NULL AND dependency_writer_fence IS NULL "
            "AND writer_lease_expires_at IS NULL) OR "
            "(writer_lease_owner IS NOT NULL AND session_fence IS NOT NULL "
            "AND session_fence >= 1 AND knowledge_writer_fence IS NOT NULL "
            "AND knowledge_writer_fence >= 1 AND dependency_writer_fence IS NOT NULL "
            "AND dependency_writer_fence >= 1 AND writer_lease_expires_at IS NOT NULL)",
            name="writer_fence_tuple_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    status: Mapped[str] = mapped_column(String(16))
    validated_knowledge_revision: Mapped[int] = mapped_column(BigInteger)
    validated_dependency_graph_revision: Mapped[int] = mapped_column(BigInteger)
    staging_hash: Mapped[str] = mapped_column(String(64))
    terminal_state: Mapped[str] = mapped_column(String(32))
    prepared_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    writer_lease_owner: Mapped[str | None] = mapped_column(String(128), nullable=True)
    session_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    knowledge_writer_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    dependency_writer_fence: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    writer_lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class SessionStagingRecord(Base):
    __tablename__ = "session_staging"
    __table_args__ = (
        CheckConstraint("aggregate_type = 'knowledge_batch'", name="aggregate_type_allowed"),
        CheckConstraint("operation = 'insert'", name="operation_allowed"),
        CheckConstraint("schema_version = 1", name="schema_version_supported"),
        CheckConstraint("validation_status = 'verified'", name="validation_status_allowed"),
        CheckConstraint("validated_knowledge_revision >= 0", name="knowledge_revision_nonnegative"),
        CheckConstraint(
            "validated_dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        UniqueConstraint("attempt_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    attempt_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("commit_attempts.id"))
    aggregate_type: Mapped[str] = mapped_column(String(32))
    operation: Mapped[str] = mapped_column(String(16))
    payload: Mapped[dict[str, Any]] = mapped_column(JsonType)
    payload_hash: Mapped[str] = mapped_column(String(64))
    schema_version: Mapped[int] = mapped_column(Integer)
    validation_status: Mapped[str] = mapped_column(String(16))
    validated_knowledge_revision: Mapped[int] = mapped_column(BigInteger)
    validated_dependency_graph_revision: Mapped[int] = mapped_column(BigInteger)
    validation_rules_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ClaimRecord(Base):
    __tablename__ = "claims"
    __table_args__ = (
        CheckConstraint("length(statement) BETWEEN 1 AND 4096", name="statement_length"),
        CheckConstraint(
            _allowed_values("claim_type", [item.value for item in ClaimType]),
            name="claim_type_allowed",
        ),
        CheckConstraint(
            _allowed_values("freshness_status", ["fresh", "due", "stale", "unknown"]),
            name="freshness_status_allowed",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    statement: Mapped[str] = mapped_column(Text)
    claim_type: Mapped[str] = mapped_column(String(32))
    freshness_status: Mapped[str] = mapped_column(String(16))
    as_of: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverify_after: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    topic: Mapped[str] = mapped_column(String(256))
    created_in_session: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))


class EvidenceRecord(Base):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("relation", [item.value for item in EvidenceUse]),
            name="relation_allowed",
        ),
        CheckConstraint(
            _allowed_values("evidence_kind", [item.value for item in EvidenceKind]),
            name="evidence_kind_allowed",
        ),
        UniqueConstraint("claim_id", "evidence_kind", "identity_hash"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("claims.id"))
    relation: Mapped[str] = mapped_column(String(16))
    evidence_kind: Mapped[str] = mapped_column(String(32))
    identity_hash: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(Text)
    observation: Mapped[dict[str, Any]] = mapped_column(JsonType)
    covered_scope: Mapped[list[str]] = mapped_column(JsonType)
    integrity_checked: Mapped[bool] = mapped_column()
    independence_group: Mapped[str | None] = mapped_column(String(256), nullable=True)
    successful: Mapped[bool | None] = mapped_column(nullable=True)
    created_in_session: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))


class ClaimAssessmentRecord(Base):
    __tablename__ = "claim_assessments"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("effective_grade", [item.value for item in EvidenceGrade]),
            name="effective_grade_allowed",
        ),
        CheckConstraint(
            _allowed_values("epistemic_status", [item.value for item in EpistemicStatus]),
            name="epistemic_status_allowed",
        ),
        CheckConstraint("confidence_basis_points BETWEEN 0 AND 10000", name="confidence_range"),
        CheckConstraint(
            "(valid AND invalidation_reason IS NULL) OR "
            "(NOT valid AND invalidation_reason IS NOT NULL)",
            name="validity_tuple_complete",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    claim_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("claims.id"))
    effective_grade: Mapped[str] = mapped_column(String(2))
    epistemic_status: Mapped[str] = mapped_column(String(16))
    rules_version: Mapped[str] = mapped_column(String(256))
    rules_hash: Mapped[str] = mapped_column(String(64))
    evidence_set_hash: Mapped[str] = mapped_column(String(64))
    assessed_scope: Mapped[list[str]] = mapped_column(JsonType)
    confidence_basis_points: Mapped[int] = mapped_column(Integer)
    valid: Mapped[bool] = mapped_column()
    invalidation_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_in_session: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AssessmentEvidenceRecord(Base):
    __tablename__ = "assessment_evidence"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("role", ["support", "counter", "scope_witness", "context"]),
            name="role_allowed",
        ),
    )

    assessment_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claim_assessments.id"), primary_key=True
    )
    evidence_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("evidence.id"), primary_key=True
    )
    role: Mapped[str] = mapped_column(String(16))


class ClaimAssessmentHeadRecord(Base):
    __tablename__ = "claim_assessment_heads"
    __table_args__ = (
        CheckConstraint(
            _allowed_values("assessment_state", ["current", "pending", "invalid"]),
            name="assessment_state_allowed",
        ),
        CheckConstraint(
            _allowed_values(
                "epistemic_status",
                [item.value for item in EpistemicStatus],
            ),
            name="epistemic_status_allowed",
        ),
        CheckConstraint(
            _allowed_values(
                "prepared_by",
                ["session", "rules_activation", "reassessment_worker"],
            ),
            name="prepared_by_allowed",
        ),
        CheckConstraint(
            "(assessment_state = 'current' AND current_assessment_id IS NOT NULL "
            "AND epistemic_status IS NOT NULL) OR "
            "(assessment_state IN ('pending', 'invalid') "
            "AND current_assessment_id IS NULL AND epistemic_status IS NULL)",
            name="lifecycle_tuple_complete",
        ),
    )

    claim_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claims.id"), primary_key=True
    )
    config_snapshot_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("config_snapshots.id"), primary_key=True
    )
    assessment_state: Mapped[str] = mapped_column(String(16))
    current_assessment_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("claim_assessments.id"), nullable=True
    )
    epistemic_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    prepared_by: Mapped[str] = mapped_column(String(32))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CheckpointRecord(Base):
    __tablename__ = "checkpoints"
    __table_args__ = (
        CheckConstraint("knowledge_revision >= 0", name="knowledge_revision_nonnegative"),
        CheckConstraint(
            "dependency_graph_revision >= 0",
            name="dependency_graph_revision_nonnegative",
        ),
        UniqueConstraint("session_id"),
        UniqueConstraint("database_commit_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    session_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), ForeignKey("sessions.id"))
    database_commit_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("commit_attempts.id")
    )
    knowledge_revision: Mapped[int] = mapped_column(BigInteger)
    dependency_graph_revision: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
