"""Tests for the session lease + progress watchdog (T2.16, §5.2.3)."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from packages.domain.services.lease import LeaseLost, LeaseService

pytestmark = [pytest.mark.unit]


async def _seed(engine: Any, state: str = "exploring") -> tuple[async_sessionmaker, uuid.UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, :st, (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid, "st": state},
        )
    return factory, sid


@pytest.mark.asyncio
async def test_acquire_and_heartbeat(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed(engine)
    lease = LeaseService(ttl=timedelta(seconds=30))

    async with factory() as db, db.begin():
        await lease.acquire(db, sid, "node-a")

    async with factory() as db:
        st = await lease.state(db, sid)
    assert st is not None
    assert st.owner == "node-a"
    assert st.expires_at is not None
    assert st.phase_deadline is not None

    async with factory() as db, db.begin():
        await lease.heartbeat(db, sid, "node-a", progress=True)
    async with factory() as db:
        st2 = await lease.state(db, sid)
    assert st2 is not None
    assert st2.last_progress_at is not None

    # heartbeat by a WRONG owner is refused
    with pytest.raises(LeaseLost):
        async with factory() as db:
            async with db.begin():
                await lease.heartbeat(db, sid, "node-b")

    # a second acquire by another owner is refused while the lease is live
    with pytest.raises(LeaseLost):
        async with factory() as db:
            async with db.begin():
                await lease.acquire(db, sid, "node-b")

    async with factory() as db:
        assert await lease.is_live(db, sid, "node-a")


@pytest.mark.asyncio
async def test_expired_lease_can_be_taken(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed(engine)
    lease = LeaseService(ttl=timedelta(seconds=30))

    async with factory() as db:
        async with db.begin():
            await lease.acquire(db, sid, "node-a")
        # force expiry
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET lease_expires_at = now() - interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": sid},
            )
        async with db.begin():
            await lease.acquire(db, sid, "node-b")  # stale lease -> allowed

    async with factory() as db:
        assert await lease.is_live(db, sid, "node-b")


@pytest.mark.asyncio
async def test_terminal_session_refuses_lease(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed(engine, state="succeeded")
    lease = LeaseService()

    with pytest.raises(LeaseLost):
        async with factory() as db:
            async with db.begin():
                await lease.acquire(db, sid, "node-a")


@pytest.mark.asyncio
async def test_heartbeat_refused_after_phase_deadline(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed(engine)
    lease = LeaseService(ttl=timedelta(seconds=300))

    async with factory() as db:
        async with db.begin():
            await lease.acquire(db, sid, "node-a", phase_deadline=timedelta(seconds=1))
        # push the deadline into the past
        async with db.begin():
            await db.execute(
                text(
                    "UPDATE sessions SET phase_deadline = now() - interval '1 second' "
                    "WHERE id=:id"
                ),
                {"id": sid},
            )

    # the watchdog refuses to keep a stuck session alive
    with pytest.raises(LeaseLost):
        async with factory() as db:
            async with db.begin():
                await lease.heartbeat(db, sid, "node-a", progress=True)
