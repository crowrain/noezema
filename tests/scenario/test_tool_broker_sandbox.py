"""Scenario: the Tool Broker against the real one-shot sandbox (T2.10).

Skipped when no docker engine is available (same fixtures/pattern as
tests/scenario/test_sandbox_runtime.py).
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from packages.broker import SandboxToolBroker, reconcile_stuck_actions
from packages.domain.db.uow import transaction
from tests.conftest import _runtime, _sealed

pytestmark = [pytest.mark.scenario]


@pytest.mark.asyncio
async def test_broker_sandbox_tools(sandbox_image: str, tmp_path: Path) -> None:
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()

    base = tmp_path / "base"
    base.mkdir()
    (base / "corpus.txt").write_text("base data\n", encoding="utf-8")

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)

        obs = await broker.execute("python.execute", {"code": "print(6*7)"})
        assert obs.ok, obs.error
        assert obs.data["stdout"].strip() == "42"
        assert obs.idempotency_class.value == "non_idempotent"

        obs = await broker.execute("shell.execute", {"command": "ls /base"})
        assert obs.ok
        assert "corpus.txt" in obs.data["stdout"]

        # overlay roundtrip on the host side
        obs = await broker.execute("workspace.write", {"path": "res.md", "content": "6*7=42"})
        assert obs.ok, obs.error
        assert (handle.workspace_dir / "res.md").read_text() == "6*7=42"
        obs = await broker.execute("workspace.read", {"path": "res.md"})
        assert obs.ok and obs.data["content"] == "6*7=42"

        # escape attempt: rejected, nothing written outside
        obs = await broker.execute("workspace.write", {"path": "/etc/escape.txt", "content": "x"})
        assert not obs.ok
        assert not Path("/etc/escape.txt").exists()

        # a nonzero exit code is a result, not an infra error
        obs = await broker.execute("shell.execute", {"command": "exit 7"})
        assert not obs.ok
        assert obs.data["exit_code"] == 7
        assert obs.error == "exit code 7"
        assert not obs.transient
    finally:
        await rt.destroy(handle)


@pytest.mark.asyncio
async def test_reconcile_stuck_actions(migrated_db) -> None:
    """T2.9: a crash between action start and result → outcome_unknown,
    audited; never a guessed success."""
    scratch_url, _ = migrated_db
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = str(uuid.uuid4())
    rid = str(uuid.uuid4())

    try:
        async with factory() as db, transaction(db):
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id) "
                    "VALUES (:id, 'exploring', (SELECT id FROM config_snapshots LIMIT 1))"
                ),
                {"id": sid},
            )
            await db.execute(
                text(
                    "INSERT INTO model_runs (id, session_id, turn_id, phase, model_fingerprint) "
                    "VALUES (:id, :s, :t, 'exploring', :fp)"
                ),
                {"id": rid, "s": sid, "t": rid, "fp": '{"model": "fake"}'},
            )
            await db.execute(
                text(
                    "INSERT INTO actions (id, session_id, model_run_id, idempotency_key, "
                    "idempotency_class, tool, arguments_hash, state, started_at) "
                    "VALUES (:id, :s, :r, 'k1', 'non_idempotent', 'python.execute', 'h', 'started', now())"
                ),
                {"id": str(uuid.uuid4()), "s": sid, "r": rid},
            )

        async with factory() as db2, transaction(db2):
            n = await reconcile_stuck_actions(db2, sid)
        assert n == 1

        async with engine.connect() as conn:
            state = (
                await conn.execute(text("SELECT state FROM actions WHERE session_id=:s"), {"s": sid})
            ).scalar_one()
            events = (
                await conn.execute(
                    text(
                        "SELECT type FROM audit_events "
                        "WHERE session_id=:s AND type='action_outcome_unknown'"
                    ),
                    {"s": sid},
                )
            ).fetchall()
            outbox = (
                await conn.execute(text("SELECT COUNT(*) FROM outbox_events"))
            ).scalar_one()
            audit = (
                await conn.execute(
                    text("SELECT COUNT(*) FROM audit_events WHERE session_id=:s"), {"s": sid}
                )
            ).scalar_one()
        assert state == "outcome_unknown"
        assert len(events) == 1
        assert outbox == audit  # the reconciliation audit has its outbox twin
    finally:
        await engine.dispose()
