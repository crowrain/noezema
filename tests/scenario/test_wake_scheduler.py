"""Scenario: wake scheduler DB-backed rules (T3.29, §5.2.1).

Admission gates (skip with exact reason, never queue), the backoff/pause
state machine in ``wake_scheduler_state`` and the ``wake_skipped`` audit
trail. Pure schedule timing and backoff math are in
tests/unit/test_wake_schedule.py.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from apps.orchestrator.scheduler import WakeScheduleError, WakeScheduler
from packages.domain.db.uow import transaction

pytestmark = [pytest.mark.scenario]

NODE = "local-node"
NOW = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _sched(db, data_root: Path) -> WakeScheduler:
    return WakeScheduler(db, node_owner=NODE, data_root=data_root)


async def _set_node_state(db, state: str) -> None:
    await db.execute(
        text(
            "INSERT INTO system_constants (key, value) VALUES ('node_state', :v) "
            "ON CONFLICT (key) DO UPDATE SET value = :v"
        ),
        {"v": state},
    )


async def _seed_state_row(db, **fields) -> None:
    cols = ["node_id"]
    vals: dict[str, object] = {"n": NODE}
    for i, (k, v) in enumerate(fields.items()):
        cols.append(k)
        vals[f"f{i}"] = v
    placeholders = ", ".join(f":f{i}" for i in range(len(fields)))
    await db.execute(
        text(f"INSERT INTO wake_scheduler_state (node_id, {', '.join(cols[1:])}) VALUES (:n, {placeholders})"),
        vals,
    )


async def _read_state_row(db) -> dict:
    row = (
        await db.execute(
            text(
                "SELECT consecutive_failures, backoff_until, last_session_state, paused_reason "
                "FROM wake_scheduler_state WHERE node_id = :n"
            ),
            {"n": NODE},
        )
    ).mappings().one()
    return dict(row)


async def _bootstrap_id(db):
    return (await db.execute(text("SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'"))).scalar_one()


async def _mutate_schedule(db, patch: dict) -> None:
    # in-place JSON mutation; the snapshot hash covers the hash columns only,
    # so the effective-config check stays intact (test-only shortcut)
    await db.execute(
        text("UPDATE config_snapshots SET wake_schedule = wake_schedule || CAST(:p AS jsonb)"),
        {"p": json.dumps(patch, sort_keys=True)},
    )


async def _seed_session(db, state: str = "exploring") -> uuid.UUID:
    sid = uuid.uuid4()
    await db.execute(
        text("INSERT INTO sessions (id, state, config_snapshot_id) VALUES (:id, :s, :c)"),
        {"id": sid, "s": state, "c": str(await _bootstrap_id(db))},
    )
    return sid


async def _seed_commit_attempt(db, session_id: uuid.UUID, status: str = "prepared") -> None:
    await db.execute(
        text(
            "INSERT INTO commit_attempts (id, session_id, status, staging_hash, "
            "base_knowledge_revision, base_dependency_graph_revision) "
            "VALUES (:id, :s, :st, :h, 0, 0)"
        ),
        {"id": uuid.uuid4(), "s": session_id, "st": status, "h": "f" * 64},
    )


async def _wake_skipped_audit(db) -> list[dict]:
    rows = (
        await db.execute(text("SELECT payload, actor FROM audit_events WHERE type = 'wake_skipped'"))
    ).mappings().all()
    return [dict(r) for r in rows]


# ── schedule timing (DB-backed) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_first_scheduled_tick_wakes(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "wake"
        async with factory() as db:
            assert await _wake_skipped_audit(db) == []
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduled_wait_interval_not_elapsed(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _seed_state_row(db, last_session_finished_at=NOW - timedelta(seconds=60))
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "wait"
        assert decision.reason == "interval_not_elapsed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduled_wait_min_interval(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _mutate_schedule(db, {"min_session_interval_seconds": 14400})
            await _seed_state_row(db, last_session_finished_at=NOW - timedelta(seconds=7200))
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "wait"
        assert decision.reason == "min_interval_not_elapsed"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_scheduled_wait_backoff_window(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _seed_state_row(
                db,
                last_session_finished_at=NOW - timedelta(seconds=7200),
                backoff_until=NOW + timedelta(seconds=3600),
            )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "wait"
        assert decision.reason == "backoff_active"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wake_now_bypasses_timing(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            # interval not elapsed AND a backoff window is active
            await _seed_state_row(
                db,
                last_session_finished_at=NOW - timedelta(seconds=60),
                backoff_until=NOW + timedelta(seconds=3600),
            )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=NOW)
        assert decision.action == "wake"
        # the same tick as scheduled would have waited
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "wait"
    finally:
        await engine.dispose()


# ── admission gates (skip, never queue) ──────────────────────────────────


@pytest.mark.asyncio
async def test_skip_when_node_paused(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _set_node_state(db, "paused")
            await _seed_state_row(db, paused_reason="operator")
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "paused"
        assert decision.detail == "operator"
        async with factory() as db:
            audit = await _wake_skipped_audit(db)
        assert len(audit) == 1
        assert audit[0]["payload"]["reason"] == "paused"
        assert audit[0]["payload"]["source"] == "scheduled"
        assert audit[0]["payload"]["node_owner"] == NODE
        assert audit[0]["actor"] == "wake-scheduler"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skip_on_nonterminal_session(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _seed_session(db, state="exploring")
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "nonterminal_session"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skip_on_unresolved_commit_attempt(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            # a terminal session with an unresolved attempt: the reconciler's
            # business, but the admission gate must still see it
            sid = await _seed_session(db, state="succeeded")
            await _seed_commit_attempt(db, sid, status="prepared")
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "unresolved_commit_attempt"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skip_when_activation_slot_busy(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            # slot invariant: the activating slot and the lease fields move together
            bid = await _bootstrap_id(db)
            await db.execute(
                text(
                    "UPDATE runtime_config_heads SET "
                    "activating_config_snapshot_id = :id, "
                    "activation_lease_owner = 'test-lease', "
                    "activation_lease_expires_at = now() + interval '1 hour'"
                ),
                {"id": str(bid)},
            )
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "activation_slot_busy"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skip_when_disk_quota_exceeded(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    data_root = tmp_path / "data"
    data_root.mkdir()
    (data_root / "big.bin").write_bytes(b"x" * (2 * 1024 * 1024))  # 2 MiB
    try:
        async with factory() as db, transaction(db):
            await _mutate_schedule(db, {"disk_quota_mb": 1})
        async with factory() as db:
            decision = await _sched(db, data_root).decide(source="scheduled", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "disk_quota_exceeded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_skip_when_gpu_required_but_unavailable(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _mutate_schedule(db, {"gpu_required": True})
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
        # fail-closed: GPU introspection is not implemented in MVP
        assert decision.action == "skip"
        assert decision.reason == "gpu_unavailable"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_wake_now_bypasses_timing_but_not_admission(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await _set_node_state(db, "paused")
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="wake_now", now=NOW)
        assert decision.action == "skip"
        assert decision.reason == "paused"
    finally:
        await engine.dispose()


# ── failure bookkeeping: backoff + auto-pause ────────────────────────────


@pytest.mark.asyncio
async def test_failed_session_sets_backoff(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            # small interval so the backoff window (not the interval) is the
            # binding constraint after the failure
            await _mutate_schedule(
                db, {"interval_seconds": 10, "min_session_interval_seconds": 5, "backoff_base_seconds": 3600}
            )
        async with factory() as db:
            node_state = await _sched(db, tmp_path).record_session_result(final_state="failed", now=NOW)
        assert node_state == "idle"
        async with factory() as db:
            row = await _read_state_row(db)
        assert row["consecutive_failures"] == 1
        assert row["last_session_state"] == "failed"
        assert row["backoff_until"] == NOW + timedelta(seconds=3600)
        # the next scheduled tick waits inside the backoff window
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW + timedelta(seconds=100))
        assert decision.action == "wait"
        assert decision.reason == "backoff_active"
        # ... and is due once the window has passed
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=NOW + timedelta(hours=3))
        assert decision.action == "wake"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_backoff_grows_with_consecutive_failures(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await _sched(db, tmp_path).record_session_result(final_state="failed", now=NOW)
            await _sched(db, tmp_path).record_session_result(
                final_state="failed", now=NOW + timedelta(hours=2)
            )
        async with factory() as db:
            row = await _read_state_row(db)
        assert row["consecutive_failures"] == 2
        assert row["backoff_until"] == NOW + timedelta(hours=2) + timedelta(seconds=120)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_three_consecutive_failures_pause_the_node(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        t = NOW
        async with factory() as db:
            for _ in range(3):
                t = t + timedelta(hours=2)
                await _sched(db, tmp_path).record_session_result(final_state="failed", now=t)
        async with factory() as db:
            node = (
                await db.execute(
                    text("SELECT value FROM system_constants WHERE key = 'node_state'")
                )
            ).scalar_one()
            row = await _read_state_row(db)
        assert node == "paused"
        assert row["paused_reason"] == "consecutive_failures"
        # and the next tick is a skip, not a wake
        async with factory() as db:
            decision = await _sched(db, tmp_path).decide(source="scheduled", now=t + timedelta(hours=2))
        assert decision.action == "skip"
        assert decision.reason == "paused"
        assert decision.detail == "consecutive_failures"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_success_resets_failure_state(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await _sched(db, tmp_path).record_session_result(final_state="failed", now=NOW)
            await _sched(db, tmp_path).record_session_result(
                final_state="succeeded", now=NOW + timedelta(hours=2)
            )
        async with factory() as db:
            row = await _read_state_row(db)
        assert row["consecutive_failures"] == 0
        assert row["backoff_until"] is None
        assert row["last_session_state"] == "succeeded"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_operator_pause_survives_session_results(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            # the operator PAUSE command sets both (web path, T3.29)
            await _set_node_state(db, "paused")
            await _seed_state_row(db, paused_reason="operator")
        async with factory() as db:
            node = await _sched(db, tmp_path).record_session_result(final_state="failed", now=NOW)
            assert node == "paused"
        async with factory() as db:
            node = await _sched(db, tmp_path).record_session_result(
                final_state="succeeded", now=NOW + timedelta(hours=2)
            )
            assert node == "paused"
        async with factory() as db:
            row = await _read_state_row(db)
        # a success clears the bookkeeping but never the operator pause
        assert row["consecutive_failures"] == 0
        assert row["paused_reason"] == "operator"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_partial_success_and_cancel_reset_failures(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await _sched(db, tmp_path).record_session_result(final_state="failed", now=NOW)
            await _sched(db, tmp_path).record_session_result(
                final_state="succeeded_partial", now=NOW + timedelta(hours=2)
            )
            await _sched(db, tmp_path).record_session_result(
                final_state="cancelled", now=NOW + timedelta(hours=4)
            )
        async with factory() as db:
            row = await _read_state_row(db)
        assert row["consecutive_failures"] == 0
        assert row["backoff_until"] is None
    finally:
        await engine.dispose()


# ── fail-closed config ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_corrupt_wake_schedule_is_fail_closed(migrated_db, tmp_path: Path) -> None:
    _, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            await db.execute(text("UPDATE config_snapshots SET wake_schedule = '{}'::jsonb"))
        async with factory() as db:
            with pytest.raises(WakeScheduleError):
                await _sched(db, tmp_path).decide(source="scheduled", now=NOW)
    finally:
        await engine.dispose()
