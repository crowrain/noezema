"""Config access: effective snapshot via the global runtime head (§14.1)."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.config import config_snapshot_sha256
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead


class ConfigError(RuntimeError):
    pass


class ConfigService:
    @staticmethod
    async def get_effective(db: AsyncSession) -> ORMConfigSnapshot:
        """The effective config is determined ONLY by pointer equality
        (active head). Fail-closed on hash mismatch or missing head."""
        stmt = select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == "global")
        head = (await db.execute(stmt)).scalar_one_or_none()
        if head is None:
            raise ConfigError("global runtime config head missing — bootstrap migration required")

        snapshot = await db.get(ORMConfigSnapshot, head.active_config_snapshot_id)
        if snapshot is None:
            raise ConfigError(f"active config snapshot {head.active_config_snapshot_id} missing")

        expected = config_snapshot_sha256(snapshot.base_snapshot_id, snapshot.payload_sha256)
        if snapshot.sha256 != expected:
            raise ConfigError(
                f"config snapshot {snapshot.id} hash mismatch: {snapshot.sha256} != {expected}"
            )
        return snapshot

    @staticmethod
    def question_namespace(db_value: str) -> uuid.UUID:
        return uuid.UUID(db_value)
