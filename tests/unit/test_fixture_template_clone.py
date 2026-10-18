"""T7.55: scratch-DB template fixture — clone identity and isolation.

The `migrated_db` fixture clones its scratch DB from a per-session
pre-migrated template (`CREATE DATABASE ... TEMPLATE`). These tests verify:

1. identity: the clone's catalog (alembic version, tables, columns, indexes,
   constraints) is exactly what a fresh direct `alembic upgrade head` on an
   empty DB produces;
2. isolation: two clones of the same template do not see each other's writes.

The tests intentionally create extra scratch DBs themselves (a clone and a
direct-migration DB, dropped in teardown); the alembic subprocess runs only in
the identity test — it verifies exactly the pre-T7.55 fixture path.
"""

from __future__ import annotations

import asyncio
import os
import secrets
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from tests.conftest import REPO_ROOT, _admin_exec, _drop_scratch_database

pytestmark = [pytest.mark.unit]


async def _catalog_fingerprint(engine: AsyncEngine) -> dict[str, Any]:
    async with engine.connect() as conn:
        versions = [
            row[0]
            for row in (await conn.execute(text("SELECT version_num FROM alembic_version ORDER BY 1"))).all()
        ]
        tables = [
            row[0]
            for row in (
                await conn.execute(
                    text("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY 1")
                )
            ).all()
        ]
        columns = [
            tuple(row)
            for row in (
                await conn.execute(
                    text(
                        "SELECT table_name, column_name, ordinal_position, data_type, is_nullable "
                        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2, 3"
                    )
                )
            ).all()
        ]
        indexes = [
            tuple(row)
            for row in (
                await conn.execute(
                    text("SELECT indexname, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2")
                )
            ).all()
        ]
        constraints = [
            tuple(row)
            for row in (
                await conn.execute(
                    text(
                        "SELECT conname, contype, conrelid::regclass::text FROM pg_constraint "
                        "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2, 3"
                    )
                )
            ).all()
        ]
    return {
        "versions": versions,
        "tables": tables,
        "columns": columns,
        "indexes": indexes,
        "constraints": constraints,
    }


async def _create_direct_scratch(test_db_url: str, dbname: str) -> None:
    """The pre-T7.55 fixture path: empty DB + `alembic upgrade head` subprocess."""
    parts = urlparse(test_db_url)
    scratch_url = parts._replace(path=f"/{dbname}").geturl()
    await _admin_exec(test_db_url, f'CREATE DATABASE "{dbname}"')
    env = dict(os.environ)
    env["NOEZEMA_DATABASE_URL"] = scratch_url

    def run() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )

    proc = await asyncio.to_thread(run)
    assert proc.returncode == 0, f"alembic failed:\n{proc.stdout}\n{proc.stderr}"


async def test_clone_is_identical_to_direct_migration(
    migrated_db: tuple[str, AsyncEngine], test_db_url: str
) -> None:
    """A clone of the template equals a fresh direct `alembic upgrade head` on an empty DB."""
    _clone_url, clone_engine = migrated_db

    direct_name = f"noezema_mig_{secrets.token_hex(4)}"  # scratch naming convention; dropped in teardown below
    parts = urlparse(test_db_url)
    direct_url = parts._replace(path=f"/{direct_name}").geturl()
    await _create_direct_scratch(test_db_url, direct_name)
    direct_engine = create_async_engine(direct_url)
    try:
        clone_fp = await _catalog_fingerprint(clone_engine)
        direct_fp = await _catalog_fingerprint(direct_engine)

        assert clone_fp["versions"], "alembic version table is empty in the clone"
        assert clone_fp["tables"], "no tables in the clone"
        assert len(clone_fp["indexes"]) > 10, "sanity: a migrated schema has indexes"
        assert clone_fp["versions"] == direct_fp["versions"]
        assert clone_fp["tables"] == direct_fp["tables"]
        assert clone_fp["columns"] == direct_fp["columns"]
        assert clone_fp["indexes"] == direct_fp["indexes"]
        assert clone_fp["constraints"] == direct_fp["constraints"]
    finally:
        await direct_engine.dispose()
        # T7.63: same teardown rule as the fixture itself — an attached backend must not leak this DB.
        await _drop_scratch_database(test_db_url, direct_name)


async def test_two_clones_are_isolated(
    migrated_db: tuple[str, AsyncEngine], test_db_url: str, migrated_db_template: str
) -> None:
    """A table/row written in one clone is invisible in a second clone of the same template."""
    _scratch_url, engine_a = migrated_db

    parts = urlparse(test_db_url)
    name_b = f"noezema_mig_{secrets.token_hex(4)}"  # scratch naming convention; dropped in teardown below
    url_b = parts._replace(path=f"/{name_b}").geturl()
    await _admin_exec(
        test_db_url, "SET lock_timeout TO '20s'", f'CREATE DATABASE "{name_b}" TEMPLATE "{migrated_db_template}"'
    )
    engine_b = create_async_engine(url_b)
    try:
        async with engine_a.connect() as conn:
            await conn.execute(text("CREATE TABLE t755_probe (id integer PRIMARY KEY, marker text NOT NULL)"))
            await conn.execute(text("INSERT INTO t755_probe (id, marker) VALUES (1, 'clone-a')"))
            await conn.commit()

        async with engine_a.connect() as conn:
            rows_a = (await conn.execute(text("SELECT count(*) FROM t755_probe"))).scalar_one()
            probe_a = (await conn.execute(text("SELECT to_regclass('public.t755_probe')"))).scalar_one()
        async with engine_b.connect() as conn:
            probe_b = (await conn.execute(text("SELECT to_regclass('public.t755_probe')"))).scalar_one()

        assert rows_a == 1
        assert probe_a is not None
        assert probe_b is None, "clone B must not see a table created in clone A"
    finally:
        await engine_b.dispose()
        await _drop_scratch_database(test_db_url, name_b)
