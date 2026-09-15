"""Scenario (DB): T7.5 — evaluation run web routes (§22.2).

Covers the read-only web routes for the evaluation runs:
``GET /api/v1/evaluation`` (list, newest first) and
``GET /api/v1/evaluation/{run_id}`` (detail: frozen config + gates +
blind sample), plus the ``/evaluation`` HTML page (read-only view).
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.web.api import create_app
from packages.evaluation.service import create_evaluation_run

pytestmark = [pytest.mark.scenario]

BOOT = "(SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap')"


def _client(app) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _make_app(scratch_url: str):
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(engine=engine, factory=factory)
    return app, engine


async def _bootstrap_snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    factory = async_sessionmaker(engine)
    async with factory() as db:
        row = (
            await db.execute(
                text(
                    "SELECT id FROM config_snapshots "
                    "WHERE activation_mode = 'bootstrap'"
                )
            )
        ).first()
    return row[0]  # type: ignore[index]


@pytest.mark.asyncio
async def test_evaluation_list_and_detail(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """The list + detail routes return the frozen config + gates."""
    scratch_url, engine = migrated_db
    app, app_engine = await _make_app(scratch_url)
    cs_id = await _bootstrap_snapshot_id(engine)

    # create two runs (different labels)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await create_evaluation_run(
            db,
            label="eval-1",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "m1", "backend": "local"},
            rules_version="r1",
            rules_hash="a" * 64,
            blind_sample_seed=42,
            blind_sample_size=50,
        )
        run2 = await create_evaluation_run(
            db,
            label="eval-2",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "m2", "backend": "local"},
            rules_version="r2",
            rules_hash="b" * 64,
            blind_sample_seed=99,
            blind_sample_size=50,
        )
        assert run2 is not None
        run2_id = run2.id

    async with _client(app) as client:
        # list
        r = await client.get("/api/v1/evaluation")
        assert r.status_code == 200
        runs = r.json()["runs"]
        assert len(runs) == 2
        # newest first
        assert runs[0]["label"] == "eval-2"
        assert runs[1]["label"] == "eval-1"
        assert runs[0]["outcome"] == "running"
        assert runs[0]["blind_sample_size"] == 50

        # detail
        r2 = await client.get(f"/api/v1/evaluation/{run2_id}")
        assert r2.status_code == 200
        detail = r2.json()
        assert detail["label"] == "eval-2"
        assert detail["rules_version"] == "r2"
        assert detail["model_fingerprint"]["model"] == "m2"
        assert detail["blind_sample_seed"] == 99
        assert detail["outcome"] == "running"

        # 404 for unknown
        r3 = await client.get(f"/api/v1/evaluation/{uuid.uuid4()}")
        assert r3.status_code == 404

    await app_engine.dispose()


@pytest.mark.asyncio
async def test_evaluation_page_is_readonly_html(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """The /evaluation page is a read-only HTML view."""
    scratch_url, _engine = migrated_db
    app, app_engine = await _make_app(scratch_url)
    async with _client(app) as client:
        r = await client.get("/evaluation")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    assert "evaluation" in r.text.lower()
    await app_engine.dispose()
