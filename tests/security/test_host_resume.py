"""Host resume scenarios (T3.27, §8.7.1.1).

- a DB outage of any duration stays in retry_wait (exit 0, no start-limit
  spend) and resolves on its own;
- an unclassified probe failure maps to resume_degraded;
- a permanent/inconsistent journal maps to resume_blocked (exit 78);
- retained (resolved) history is NOT active: the head is dropped after a
  full replay and the node is healthy again.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl.journal import STATE_CHECKING, STATE_RESOLVED, JournalStore, TransitionRecord
from hostctl.resume import EXIT_RESUME_BLOCKED, classify, run_resume_probe

pytestmark = [pytest.mark.security]

VALID_POLICY = (
    b"schema_version = 1\nresume_retry_initial = \"30s\"\nresume_retry_multiplier = 2.0\n"
    b"resume_retry_max = \"30min\"\nresume_retry_jitter = 0.0\n"
    b"resume_retry_escalate_after = \"15min\"\nretry_timer_period = \"30s\"\n"
    b"retry_timer_accuracy = \"1s\"\nmaintenance_flock_deadline = \"2s\"\n"
)


def _write_policy(tmp_path: Path) -> Path:
    p = tmp_path / "host-recovery.defaults.toml"
    p.write_bytes(VALID_POLICY)
    return p


@pytest.mark.asyncio
async def test_db_outage_is_retry_wait(migrated_db: tuple[str, AsyncEngine], tmp_path: Path) -> None:
    _scratch_url, _engine = migrated_db
    host_lib = tmp_path / "host-lib"
    host_lib.mkdir()
    baseline = _write_policy(tmp_path)
    # an engine pointed at a closed port: every DB access fails -> the
    # probe must treat the outage as transient (retry_wait, exit 0), not a
    # permanent block, and must not spend the start-limit budget
    from sqlalchemy.ext.asyncio import create_async_engine

    dead = create_async_engine("postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:1/noezema")
    factory = async_sessionmaker(dead, expire_on_commit=False)
    try:
        async with factory() as db:
            outcome = await run_resume_probe(db, host_lib_base=host_lib, policy_baseline=baseline)
    finally:
        await dead.dispose()
    assert outcome.outcome == "retry_wait"
    assert outcome.exit_code == 0


def test_unclassified_failure_is_degraded() -> None:
    assert classify(None) == "resume_degraded"


@pytest.mark.asyncio
async def test_permanent_inconsistency_is_blocked(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch_url, engine = migrated_db
    host_lib = tmp_path / "host-lib"
    host_lib.mkdir()
    baseline = _write_policy(tmp_path)
    store = JournalStore(host_lib)
    for aid in ("a", "b"):
        store.write_record(
            TransitionRecord(
                attempt_id=aid,
                operation="offline_rules",
                candidate_snapshot_id=None,
                base_snapshot_id=None,
                observed_pointer_tuple={},
                state=STATE_CHECKING,
            )
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        outcome = await run_resume_probe(db, host_lib_base=host_lib, policy_baseline=baseline)
    assert outcome.outcome == "resume_blocked"
    assert outcome.exit_code == EXIT_RESUME_BLOCKED
    await engine.dispose()


@pytest.mark.asyncio
async def test_retained_history_is_not_active(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """A completed (resolved) transition is history, not an active
    transition: reconcile drops the head after a full replay and the probe
    resolves (the node is healthy)."""
    _scratch_url, engine = migrated_db
    host_lib = tmp_path / "host-lib"
    host_lib.mkdir()
    baseline = _write_policy(tmp_path)
    store = JournalStore(host_lib)
    rec = TransitionRecord(
        attempt_id="done",
        operation="offline_rules",
        candidate_snapshot_id=None,
        base_snapshot_id=None,
        observed_pointer_tuple={},
        state=STATE_RESOLVED,
    )
    store.write_record(rec)
    store.write_event("done", 1, {"to_state": "checking"})
    store.write_event("done", 2, {"to_state": "resolved"})
    store.write_head(rec, initial_event_sha256="e", creation_boot_id="b")
    rec.last_event_seq = 2
    rec.replayed_through_seq = 2
    rec.replayed_at = "now"
    store.write_record(rec)
    # reconcile should drop the head (resolved + complete replay)
    res = store.reconcile()
    assert res.ok
    assert not store.head_exists()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        outcome = await run_resume_probe(db, host_lib_base=host_lib, policy_baseline=baseline)
    assert outcome.outcome == "resolved"
    assert outcome.exit_code == 0
    await engine.dispose()
