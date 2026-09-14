"""Tests for the COW workspace freeze (T2.12, §5.2.2 point 3)."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.artifacts.workspace import compute_root_sha256, freeze_workspace
from packages.domain.models.artifacts import ORMWorkspaceEntry, ORMWorkspaceManifest

pytestmark = [pytest.mark.unit]


@pytest.mark.asyncio
async def test_freeze_records_manifest(migrated_db: Any, tmp_path: Path) -> None:
    _scratch_url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)

    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, 'committing', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )

    # build a small overlay
    ws = tmp_path / "overlay"
    ws.mkdir()
    (ws / "a.txt").write_text("alpha", encoding="utf-8")
    (ws / "sub").mkdir()
    (ws / "sub" / "b.txt").write_text("beta", encoding="utf-8")

    manifest: ORMWorkspaceManifest | None = None
    async with factory() as db:
        from packages.domain.db.uow import transaction

        async with transaction(db):
            manifest = await freeze_workspace(db, sid, ws)
    assert manifest is not None
    assert manifest.entry_count == 2
    assert manifest.total_size == 9
    assert manifest.root_sha256

    async with factory() as db:
        entries = (
            (
                await db.execute(
                    select(ORMWorkspaceEntry).where(ORMWorkspaceEntry.manifest_id == manifest.id)
                )
            )
            .scalars()
            .all()
        )
        by_path = {e.path: e for e in entries}
        assert set(by_path) == {"a.txt", "sub/b.txt"}
        assert by_path["a.txt"].size == 5
        assert by_path["a.txt"].sha256 == hashlib.sha256(b"alpha").hexdigest()

        m = await db.get(ORMWorkspaceManifest, manifest.id)
        assert m is not None and m.session_id == sid


def test_root_hash_is_deterministic_and_order_independent() -> None:
    e1 = [("a.txt", 5, "aa"), ("sub/b.txt", 4, "bb")]
    e2 = [("sub/b.txt", 4, "bb"), ("a.txt", 5, "aa")]
    assert compute_root_sha256(e1) == compute_root_sha256(e2)
    e3 = [("a.txt", 5, "aa"), ("sub/b.txt", 4, "cc")]
    assert compute_root_sha256(e1) != compute_root_sha256(e3)
