"""Scenario: migration 0001 on a clean PostgreSQL database (T1.3).

Requires NOEZEMA_TEST_DATABASE_URL (CI postgres service). Creates a scratch
database, runs `alembic upgrade head`, and verifies the bootstrap invariants
of §14.1.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
from urllib.parse import urlparse

import pytest
from sqlalchemy import text

from packages.domain.config import (
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_SNAPSHOT_ID,
    QUESTION_UUID5_NAMESPACE,
    bootstrap_snapshot_sha256,
)

pytestmark = [pytest.mark.scenario]


def _scratch_db_url(base_url: str) -> tuple[str, str]:
    parts = urlparse(base_url)
    dbname = f"noezema_boot_{secrets.token_hex(4)}"
    scratch = parts._replace(path=f"/{dbname}").geturl()
    return scratch, dbname


@pytest.mark.asyncio
async def test_bootstrap_migration_on_clean_db(test_db_url: str) -> None:
    scratch_url, dbname = _scratch_db_url(test_db_url)

    from sqlalchemy.ext.asyncio import create_async_engine

    # Create the scratch database (test user is a superuser in CI/dev).
    admin = create_async_engine(test_db_url, isolation_level="AUTOCOMMIT")
    try:
        async with admin.connect() as conn:
            await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    finally:
        await admin.dispose()

    try:
        env = dict(os.environ)
        env["NOEZEMA_DATABASE_URL"] = scratch_url
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert proc.returncode == 0, f"alembic failed:\n{proc.stdout}\n{proc.stderr}"

        engine = create_async_engine(scratch_url)
        async with engine.connect() as conn:
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
            model = (await conn.execute(text("SELECT model->>'model_alias' FROM config_snapshots"))).scalar_one()
            assert model == "thinker-local"
        await engine.dispose()
    finally:
        admin = create_async_engine(test_db_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{dbname}"'))
        finally:
            await admin.dispose()
