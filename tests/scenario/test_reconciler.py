"""Reconciliation failpoints (T2.20, §5.2.2).

The outcome of a lost COMMIT answer is decided by the fenced row-lock
protocol — these tests seed each crash point and assert the protocol's
decision:

- kill BEFORE COMMIT (prepared row, no terminal record) -> aborted+failed
- kill AFTER server commit (attempt committed + terminal) -> accepted
- open final transaction (row-lock wait) -> finalizer_in_progress
  (NEVER a false aborted)
- stale finalizer (expired lease, prepared attempt) -> aborted
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.domain.services.audit import AuditService
from packages.domain.services.reconciler import LockTimeoutError, reconcile_commit

pytestmark = [pytest.mark.scenario]


async def _seed_committing(
    engine: Any, *, attempt_status: str, terminal: str | None = None, live_lease: bool = True
) -> uuid.UUID:
    """Seed a session in the commit boundary with a durable attempt row."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    qid = uuid.uuid4()
    attempt_id = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO questions (id, text, state, origin) "
                "VALUES (:q, 'Q', 'researching', 'seeded')"
            ),
            {"q": qid},
        )
        from datetime import UTC, datetime, timedelta

        state = terminal if terminal is not None else "committing"
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, question_id, config_snapshot_id, "
                "lease_owner, lease_expires_at) "
                "VALUES (:id, :st, :q, (SELECT id FROM config_snapshots LIMIT 1), :owner, :expires)"
            ),
            {
                "id": sid,
                "st": state,
                "q": qid,
                "owner": "node-a" if live_lease else None,
                "expires": datetime.now(UTC) + timedelta(seconds=30) if live_lease else None,
            },
        )
        await db.execute(
            text(
                "INSERT INTO commit_attempts (id, session_id, status, staging_hash, "
                "base_knowledge_revision, base_dependency_graph_revision) "
                "VALUES (:a, :s, :st, 'h', 0, 0)"
            ),
            {"a": attempt_id, "s": sid, "st": attempt_status},
        )
        if terminal is None:
            await db.execute(
                text(
                    "UPDATE sessions SET commit_attempt_id = :a WHERE id = :s"
                ),
                {"a": attempt_id, "s": sid},
            )
    return sid


@pytest.mark.asyncio
async def test_kill_before_commit_is_aborted(migrated_db: Any) -> None:
    """prepared row + no terminal record + fenced owner -> aborted+failed."""
    _url, engine = migrated_db
    sid = await _seed_committing(engine, attempt_status="prepared", live_lease=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        result = await reconcile_commit(db, AuditService(db), sid, original_owner="node-a")
    assert result.outcome == "aborted"

    async with factory() as db:
        row = (
            await db.execute(
                text("SELECT state, termination_reason FROM sessions WHERE id=:id"), {"id": sid}
            )
        ).mappings().first()
        assert row["state"] == "failed"
        assert row["termination_reason"] == "reconciled_abort"
        status = (
            await db.execute(
                text("SELECT status FROM commit_attempts WHERE session_id=:s"), {"s": sid}
            )
        ).scalar_one()
        assert status == "aborted"
        # the alert/reconciliation audit trail exists
        kinds = (
            await db.execute(
                text("SELECT type FROM audit_events WHERE session_id=:s"), {"s": sid}
            )
        ).scalars().all()
    assert "commit_reconciled" in kinds
    assert "session_failed" in kinds


@pytest.mark.asyncio
async def test_kill_after_commit_is_accepted(migrated_db: Any) -> None:
    """attempt committed + terminal record present -> accept, no churn."""
    _url, engine = migrated_db
    sid = await _seed_committing(
        engine, attempt_status="committed", terminal="succeeded", live_lease=False
    )

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        result = await reconcile_commit(db, AuditService(db), sid)
    assert result.outcome == "committed_accepted"

    async with factory() as db:
        state = (
            await db.execute(text("SELECT state FROM sessions WHERE id=:id"), {"id": sid})
        ).scalar_one()
    assert state == "succeeded"  # untouched


@pytest.mark.asyncio
async def test_open_final_tx_yields_finalizer_in_progress(migrated_db: Any) -> None:
    """A still-running final transaction holds the row lock: the
    reconciler must see ``finalizer_in_progress`` (a transient retry),
    never a false ``aborted``."""
    _url, engine = migrated_db
    sid = await _seed_committing(engine, attempt_status="prepared", live_lease=True)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    # connection 1: open a transaction that locks the session row
    async with factory() as lock_db, lock_db.begin():
        await lock_db.execute(
            text("SELECT id FROM sessions WHERE id = :id FOR UPDATE"), {"id": sid}
        )
        # connection 2: reconcile — must time out on the row lock
        async with factory() as rec_db:
            with pytest.raises(LockTimeoutError):
                async with rec_db.begin():
                    await reconcile_commit(
                        rec_db, AuditService(rec_db), sid, lock_timeout_ms=300
                    )
            await rec_db.rollback()

    async with factory() as db:
        state = (
            await db.execute(text("SELECT state FROM sessions WHERE id=:id"), {"id": sid})
        ).scalar_one()
        assert state == "committing"  # NOT aborted by the timed-out probe


@pytest.mark.asyncio
async def test_stale_finalizer_fenced_then_reconciled(migrated_db: Any) -> None:
    """A finalizer whose lease has expired is fenced out (lease_lost) and
    leaves a prepared attempt + no terminal record; the reconciler then
    aborts it. The fence is what makes a stale writer harmless."""
    from packages.domain.models.commit import ORMCommitAttempt
    from packages.domain.models.enums import SessionState
    from packages.domain.models.sessions import ORMSession
    from packages.domain.services.commit import finalize
    from packages.domain.services.reserve import HostReserveService, ReserveLimits
    from packages.domain.services.staging import StagingService

    _url, engine = migrated_db
    sid = await _seed_committing(engine, attempt_status="prepared", live_lease=False)

    factory = async_sessionmaker(engine, expire_on_commit=False)
    staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))

    # the stale finalizer tries to commit — the fencing predicate refuses
    async with factory() as db, db.begin():
        session = await db.get(ORMSession, sid)
        attempt = (
            (
                await db.execute(
                    text("SELECT id FROM commit_attempts WHERE session_id=:s"),
                    {"s": sid},
                )
            )
            .scalars()
            .first()
        )
        attempt_row = await db.get(ORMCommitAttempt, attempt)
        result = await finalize(
            db, AuditService(db), session, attempt_row, "node-stale", staging,
            terminal=SessionState.SUCCEEDED, steps=1, evidence_count=0,
            claims=0, questions_created=0, termination_reason=None,
        )
    assert result.outcome == "lease_lost"

    # the reconciler now aborts the dangling prepared attempt
    async with factory() as db, db.begin():
        rec = await reconcile_commit(db, AuditService(db), sid, original_owner="node-stale")
    assert rec.outcome == "aborted"
    async with factory() as db:
        state = (
            await db.execute(text("SELECT state FROM sessions WHERE id=:id"), {"id": sid})
        ).scalar_one()
    assert state == "failed"


@pytest.mark.asyncio
async def test_no_prepared_attempt_is_pre_boundary_failure(migrated_db: Any) -> None:
    """No durable attempt row: the failure was BEFORE the commit boundary
    -> plain failed, no reconciliation mystery."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id) "
                    "VALUES (:id, 'committing', (SELECT id FROM config_snapshots LIMIT 1))"
                ),
                {"id": sid},
            )
        async with db.begin():
            result = await reconcile_commit(db, AuditService(db), sid)
    assert result.outcome == "aborted"
    assert "pre-boundary" in result.detail
    async with factory() as db:
        state = (
            await db.execute(text("SELECT state FROM sessions WHERE id=:id"), {"id": sid})
        ).scalar_one()
    assert state == "failed"
