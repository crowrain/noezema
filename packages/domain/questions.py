"""Trusted-host contracts for durable research-question ingestion."""

from __future__ import annotations

from datetime import datetime
from typing import Self

from pydantic import AwareDatetime, Field

from packages.domain._base import ContractModel, NonEmptyText
from packages.domain.enums import QuestionOrigin
from packages.domain.ids import ConfigSnapshotId, QuestionId


class QuestionDraft(ContractModel):
    """Immutable input accepted by the trusted FIFO question queue."""

    id: QuestionId
    text: NonEmptyText
    origin: QuestionOrigin
    origin_config_snapshot_id: ConfigSnapshotId
    parent_id: QuestionId | None = None
    priority: int = Field(default=0, ge=-1000, le=1000)
    created_at: AwareDatetime

    @classmethod
    def seeded(
        cls,
        *,
        text: str,
        origin_config_snapshot_id: ConfigSnapshotId,
        created_at: datetime,
        parent_id: QuestionId | None = None,
        priority: int = 0,
    ) -> Self:
        """Create a host-identified question from installation or operator seeds."""

        return cls(
            id=QuestionId.new(),
            text=text,
            origin=QuestionOrigin.SEEDED,
            origin_config_snapshot_id=origin_config_snapshot_id,
            parent_id=parent_id,
            priority=priority,
            created_at=created_at,
        )

    @classmethod
    def from_message(
        cls,
        *,
        text: str,
        origin_config_snapshot_id: ConfigSnapshotId,
        created_at: datetime,
        parent_id: QuestionId | None = None,
        priority: int = 0,
    ) -> Self:
        """Create a host-identified question derived from a human message."""

        return cls(
            id=QuestionId.new(),
            text=text,
            origin=QuestionOrigin.MESSAGE,
            origin_config_snapshot_id=origin_config_snapshot_id,
            parent_id=parent_id,
            priority=priority,
            created_at=created_at,
        )
