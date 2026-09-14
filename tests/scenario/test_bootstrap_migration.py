"""Scenario: migration 0001 on a clean PostgreSQL database (T1.3, §14.1).

Uses the shared ``migrated_db`` fixture (runs ``alembic upgrade head`` on a
scratch DB) and verifies the bootstrap invariants.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from packages.domain.config import (
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_SNAPSHOT_ID,
    QUESTION_UUID5_NAMESPACE,
    bootstrap_snapshot_sha256,
)

pytestmark = [pytest.mark.scenario]


@pytest.mark.asyncio
async def test_bootstrap_invariants(migrated_db: tuple[str, object]) -> None:
    _url, engine = migrated_db
    async with engine.connect() as conn:  # type: ignore[union-attr]
        # Exactly one config snapshot: the hash-pinned bootstrap.
        snap_query = (
            "SELECT id, activation_mode, activation_state, base_snapshot_id, "
            "payload_sha256, sha256 FROM config_snapshots"
        )
        rows = (await conn.execute(text(snap_query))).all()
        assert len(rows) == 1
        row = rows[0]
        assert row.id == BOOTSTRAP_SNAPSHOT_ID
        assert row.activation_mode == "bootstrap"
        assert row.activation_state == "active"
        assert row.base_snapshot_id is None
        assert row.payload_sha256 == BOOTSTRAP_PAYLOAD_SHA256
        assert row.sha256 == bootstrap_snapshot_sha256()

        # Exactly one global runtime head, pointing at bootstrap.
        head_query = (
            "SELECT scope, active_config_snapshot_id, activating_config_snapshot_id, "
            "activation_fence FROM runtime_config_heads"
        )
        heads = (await conn.execute(text(head_query))).all()
        assert len(heads) == 1
        assert heads[0].scope == "global"
        assert heads[0].active_config_snapshot_id == BOOTSTRAP_SNAPSHOT_ID
        assert heads[0].activating_config_snapshot_id is None
        assert heads[0].activation_fence == 0

        # Pinned question namespace.
        ns_query = "SELECT value FROM system_constants WHERE key = 'question_uuid5_namespace'"
        ns = (await conn.execute(text(ns_query))).scalar_one()
        assert ns == QUESTION_UUID5_NAMESPACE

        # Payload sections materialized from the same canonical JSON.
        model_alias = (
            await conn.execute(text("SELECT model->>'model_alias' FROM config_snapshots"))
        ).scalar_one()
        assert model_alias == "thinker-local"
        rules = (
            await conn.execute(text("SELECT claim_type_rules FROM config_snapshots"))
        ).scalar_one()
        assert len(rules) == 8  # one entry per v1 claim type
