"""Scenario (DB): T7.5 — evaluation run §22.2.

Covers the evaluation run lifecycle (create → finish), the frozen
config capture (config_snapshot_id, model_fingerprint, rules_version,
rules_hash, thresholds fixed before the series), the gate outcomes
(passed/failed/insufficient_sample per gate, §22.2), and the blind
sample (seed, size, stratification by type/status). The overall
outcome is computed from the gates: ``failed`` if any gate is
``failed``, ``insufficient_sample`` if any is ``insufficient_sample``
(and none ``failed``), else ``passed``.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from packages.evaluation.service import (
    create_evaluation_run,
    finish_evaluation_run,
    get_evaluation_run,
    list_evaluation_runs,
)

pytestmark = [pytest.mark.scenario]

BOOT = "(SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap')"


async def _bootstrap_snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    from sqlalchemy.ext.asyncio import async_sessionmaker

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
    return row[0]


@pytest.mark.asyncio
async def test_evaluation_run_lifecycle(migrated_db: tuple[str, AsyncEngine]) -> None:
    """Create → finish: the frozen config + gates are captured."""
    _scratch_url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    cs_id = await _bootstrap_snapshot_id(engine)

    async with factory() as db, db.begin():
        run = await create_evaluation_run(
            db,
            label="m7-eval-1",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "qwen36-35b-a3b-q6-mtp", "backend": "local"},
            rules_version="r1",
            rules_hash="a" * 64,
            thresholds={"new_supported_refuted_e2": 0.80},
            blind_sample_seed=42,
            blind_sample_size=50,
        )
        assert run is not None
        run_id = run.id
        assert run.outcome == "running"
        assert run.eligible_sessions == 0
        assert run.completed_sessions == 0
        assert run.blind_sample_seed == 42
        assert run.blind_sample_size == 50
        assert run.thresholds["new_supported_refuted_e2"] == 0.80

        # finish with all gates passed
        finished = await finish_evaluation_run(
            db,
            run_id,
            gates={
                "new_supported_refuted_e2": {
                    "outcome": "passed",
                    "numerator": 80,
                    "denominator": 100,
                },
                "external_temporal_e3": {
                    "outcome": "passed",
                    "numerator": 25,
                    "denominator": 25,
                },
            },
            eligible_sessions=80,
            completed_sessions=75,
        )
        assert finished is not None
        assert finished.outcome == "passed"
        assert finished.eligible_sessions == 80
        assert finished.completed_sessions == 75
        assert finished.finished_at is not None
        assert finished.gates["new_supported_refuted_e2"]["outcome"] == "passed"


@pytest.mark.asyncio
async def test_evaluation_run_failed_gate(migrated_db: tuple[str, AsyncEngine]) -> None:
    """A failed gate → overall outcome ``failed``."""
    _scratch_url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    cs_id = await _bootstrap_snapshot_id(engine)

    async with factory() as db, db.begin():
        run = await create_evaluation_run(
            db,
            label="m7-eval-failed",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "qwen36", "backend": "local"},
            rules_version="r1",
            rules_hash="b" * 64,
            blind_sample_seed=7,
            blind_sample_size=50,
        )
        assert run is not None
        run_id = run.id
        finished = await finish_evaluation_run(
            db,
            run_id,
            gates={
                "new_supported_refuted_e2": {
                    "outcome": "failed",
                    "numerator": 50,
                    "denominator": 100,
                },
            },
            eligible_sessions=100,
            completed_sessions=95,
        )
        assert finished is not None
        assert finished.outcome == "failed"


@pytest.mark.asyncio
async def test_evaluation_run_insufficient_sample(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """An insufficient_sample gate (denominator < 20) → overall
    ``insufficient_sample`` (neither pass nor fail)."""
    _scratch_url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    cs_id = await _bootstrap_snapshot_id(engine)

    async with factory() as db, db.begin():
        run = await create_evaluation_run(
            db,
            label="m7-eval-insufficient",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "qwen36", "backend": "local"},
            rules_version="r1",
            rules_hash="c" * 64,
            blind_sample_seed=99,
            blind_sample_size=50,
        )
        assert run is not None
        run_id = run.id
        finished = await finish_evaluation_run(
            db,
            run_id,
            gates={
                "new_supported_refuted_e2": {
                    "outcome": "passed",
                    "numerator": 10,
                    "denominator": 10,
                },
                "external_temporal_e3": {
                    "outcome": "insufficient_sample",
                    "numerator": 5,
                    "denominator": 5,
                },
            },
            eligible_sessions=50,
            completed_sessions=48,
        )
        assert finished is not None
        assert finished.outcome == "insufficient_sample"


@pytest.mark.asyncio
async def test_evaluation_run_list_and_detail(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """List + fetch: the runs are queryable (newest first)."""
    _scratch_url, engine = migrated_db
    from sqlalchemy.ext.asyncio import async_sessionmaker

    factory = async_sessionmaker(engine, expire_on_commit=False)
    cs_id = await _bootstrap_snapshot_id(engine)

    async with factory() as db, db.begin():
        run1 = await create_evaluation_run(
            db,
            label="first",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "m1", "backend": "local"},
            rules_version="r1",
            rules_hash="d" * 64,
        )
        run2 = await create_evaluation_run(
            db,
            label="second",
            config_snapshot_id=cs_id,
            model_fingerprint={"model": "m2", "backend": "local"},
            rules_version="r2",
            rules_hash="e" * 64,
        )
        assert run1 is not None and run2 is not None
        runs = await list_evaluation_runs(db)
        assert len(runs) == 2
        # newest first
        assert runs[0].label == "second"
        assert runs[1].label == "first"

        # detail fetch
        fetched = await get_evaluation_run(db, run2.id)
        assert fetched is not None
        assert fetched.label == "second"
        assert fetched.rules_version == "r2"
        assert fetched.outcome == "running"

        # 404 for unknown
        missing = await get_evaluation_run(db, uuid.uuid4())
        assert missing is None
