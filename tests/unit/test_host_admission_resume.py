"""Unit: runtime admission (fail-closed) + resume classification (T3.13,
T3.14, §8.7.1.1)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl.admission import admission_check
from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord
from hostctl.policy import load_policy
from hostctl.resume import (
    EXIT_RESUME_BLOCKED,
    classify,
    replay_audit_events,
    run_resume_probe,
)
from packages.domain.services.audit import AuditService

VALID_POLICY = (
    b"schema_version = 1\nresume_retry_initial = \"30s\"\nresume_retry_multiplier = 2.0\n"
    b"resume_retry_max = \"30min\"\nresume_retry_jitter = 0.0\n"
    b"resume_retry_escalate_after = \"15min\"\nretry_timer_period = \"30s\"\n"
    b"retry_timer_accuracy = \"1s\"\nmaintenance_flock_deadline = \"2s\"\n"
)


@pytest.fixture()
async def env(migrated_db: tuple[str, AsyncEngine], tmp_path: Path) -> AsyncIterator[tuple[async_sessionmaker, Path]]:
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    host_lib = tmp_path / "host-lib"
    host_lib.mkdir(parents=True)
    policy_file = tmp_path / "host-recovery.defaults.toml"
    policy_file.write_bytes(VALID_POLICY)
    yield factory, host_lib
    await engine.dispose()


def test_classify_mapping():
    assert classify(None) == "resume_degraded"
    assert classify("multiple_unresolved_transitions") == "resume_blocked"
    assert classify("host_policy_invalid") == "resume_blocked"
    assert classify("config_head") == "resume_blocked"
    assert classify("db_unavailable") == "retry_wait"
    assert classify("stale_offline_marker") == "retry_wait"


async def test_admission_clean_is_ok(env):
    factory, host_lib = env
    async with factory() as db:
        report = await admission_check(
            db, host_lib_base=host_lib, policy_baseline=host_lib.parent / "host-recovery.defaults.toml"
        )
    assert report.ok, report.problems
    assert report.problems == []


async def test_admission_unresolved_transition_blocks(env):
    factory, host_lib = env
    store = JournalStore(host_lib)
    rec = TransitionRecord(
        attempt_id="x", operation="offline_rules", candidate_snapshot_id=None,
        base_snapshot_id=None, observed_pointer_tuple={}, state=STATE_CHECKING,
    )
    store.write_record(rec)
    async with factory() as db:
        report = await admission_check(
            db, host_lib_base=host_lib, policy_baseline=host_lib.parent / "host-recovery.defaults.toml"
        )
    assert not report.ok
    assert any("unresolved_host_transition" in p for p in report.problems)


async def test_admission_policy_change_in_progress_blocks(env):
    factory, host_lib = env
    (host_lib / "host-policy-change-head.json").write_text("{}")
    async with factory() as db:
        report = await admission_check(
            db, host_lib_base=host_lib, policy_baseline=host_lib.parent / "host-recovery.defaults.toml"
        )
    assert not report.ok
    assert "host_policy_change_in_progress" in report.problems


async def test_admission_invalid_policy_blocks(env):
    factory, host_lib = env
    bad = host_lib.parent / "bad.toml"
    bad.write_bytes(b"schema_version = 9\n")
    async with factory() as db:
        report = await admission_check(db, host_lib_base=host_lib, policy_override=bad)
    assert not report.ok
    assert any(p.startswith("host_policy_invalid") for p in report.problems)


async def test_resume_clean_resolves(env):
    factory, host_lib = env
    baseline = host_lib.parent / "host-recovery.defaults.toml"
    async with factory() as db:
        outcome = await run_resume_probe(db, host_lib_base=host_lib, policy_baseline=baseline)
    assert outcome.outcome == "resolved"
    assert outcome.exit_code == 0
    assert outcome.attempt_id


async def test_resume_unresolved_transition_is_blocked(env):
    factory, host_lib = env
    store = JournalStore(host_lib)
    for aid in ("a", "b"):
        store.write_record(
            TransitionRecord(
                attempt_id=aid, operation="offline_rules", candidate_snapshot_id=None,
                base_snapshot_id=None, observed_pointer_tuple={}, state=STATE_CHECKING,
            )
        )
    baseline = host_lib.parent / "host-recovery.defaults.toml"
    async with factory() as db:
        outcome = await run_resume_probe(db, host_lib_base=host_lib, policy_baseline=baseline)
    assert outcome.outcome == "resume_blocked"
    assert outcome.exit_code == EXIT_RESUME_BLOCKED


async def test_resume_audit_replay_is_idempotent(env):
    factory, host_lib = env
    store = JournalStore(host_lib)
    store.write_event("a", 1, {"to_state": "resume_blocked", "outcome": "resume_blocked"})
    store.write_event("a", 2, {"to_state": "retry_wait", "outcome": "retry_wait"})

    async with factory() as db, db.begin():
        audit = AuditService(db)
        first = await replay_audit_events(db, audit, attempt_id="a", store=store)
    assert first == 1  # only the resume_blocked event is surfaced
    # replay again -> idempotent, nothing new
    async with factory() as db, db.begin():
        audit = AuditService(db)
        second = await replay_audit_events(db, audit, attempt_id="a", store=store)
    assert second == 0

    async with factory() as db:
        n = (
            await db.execute(
                text("SELECT count(*) FROM audit_events WHERE type='alert_raised' AND payload->>'attempt_id'='a'")
            )
        ).scalar_one()
        assert int(n) == 1


def test_backoff_is_deterministic_no_jitter():
    from hostctl.resume import backoff_seconds

    policy = load_policy(VALID_POLICY, source="baseline")
    s0 = backoff_seconds(policy, 0)
    s1 = backoff_seconds(policy, 1)
    s2 = backoff_seconds(policy, 2)
    assert s0 == 30
    assert s1 == 60
    assert s2 == 120
    # deterministic: same inputs -> same outputs
    assert backoff_seconds(policy, 2) == s2
