"""config_snapshots + runtime_config_heads ORM models (T1.4, §14)."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, Text, Uuid
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from packages.domain.models.base import Base, JsonDict, created_at_column


class ORMConfigSnapshot(Base):
    __tablename__ = "config_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    base_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    payload_sha256: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    activation_mode: Mapped[str] = mapped_column(Text, nullable=False)
    activation_state: Mapped[str] = mapped_column(Text, nullable=False)
    activation_cursor: Mapped[int | None] = mapped_column(BigInteger)
    activation_manifest_hash: Mapped[str | None] = mapped_column(Text)
    activation_cohort_revision: Mapped[int | None] = mapped_column(BigInteger)
    activation_expected_head_count: Mapped[int | None] = mapped_column(BigInteger)
    activation_verified_head_count: Mapped[int | None] = mapped_column(BigInteger)
    activation_heads_sha256: Mapped[str | None] = mapped_column(Text)
    activation_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_publish_manifest_hash: Mapped[str | None] = mapped_column(Text)
    post_publish_cursor: Mapped[int | None] = mapped_column(BigInteger)
    post_publish_attempts: Mapped[int | None] = mapped_column(Integer)
    post_publish_next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_publish_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_publish_blocked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    post_publish_last_error: Mapped[str | None] = mapped_column(Text)
    model: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    embeddings: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    prompts: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    policy: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    curiosity: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    # T5.2 (stage 4): multi-step planning (mode "template" | "llm",
    # max_steps) — pinned in the snapshot
    planning: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    # T5.3 (stage 4): verification section (mode "off" | "llm",
    # max_checks) — pinned in the snapshot
    verification: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    # T5.4 (stage 4): repetition section (§9) — pinned in the snapshot
    repetition: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    # T5.5 (stage 4): extraction section (§11.2) — pinned in the
    # snapshot
    extraction: Mapped[JsonDict | None] = mapped_column(JSONB, nullable=True)
    token_budgets: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    session_limits: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    activation_limits: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    claim_type_rules: Mapped[JsonDict] = mapped_column(JSONB, nullable=False)
    # T3.29 (§5.2.1): wake schedule + admission limits + backoff (trusted
    # boundary; the sandbox never sees it)
    wake_schedule: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    # T4.4 (§5.9.1): reassessment admission thresholds (T_escalate,
    # T_worker_admission, queue SLO) — pinned in the snapshot
    reassessment_admission: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    # T4.5 (§8.7.2, §5.2.1): repair backlog admission thresholds
    # (T_repair_admission, repair SLO) — pinned in the snapshot
    repair_admission: Mapped[JsonDict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at_column()


class ORMRuntimeConfigHead(Base):
    __tablename__ = "runtime_config_heads"

    scope: Mapped[str] = mapped_column(Text, primary_key=True)
    active_config_snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    activating_config_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    activation_fence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    activation_lease_owner: Mapped[str | None] = mapped_column(Text)
    activation_lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = created_at_column()


class ORMSystemConstant(Base):
    __tablename__ = "system_constants"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = created_at_column()
