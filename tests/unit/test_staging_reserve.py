"""Tests for session staging + host reserve (T2.13, T2.14, §5.2.2, §6.6)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.canonical import canonical_sha256
from packages.domain.models.artifacts import ORMStagingOp
from packages.domain.models.questions import ORMQuestion
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import (
    HostReserveService,
    ReserveLimits,
    StagingBudgetExceeded,
)
from packages.domain.services.staging import StagingService

pytestmark = [pytest.mark.unit]


async def _seed_session(
    engine: AsyncEngine,
) -> tuple[async_sessionmaker, uuid.UUID]:
    """Insert a session row and return (factory, session_id).

    Reuses the fixture engine so the scratch DB is never held by a second
    connection pool (which would block the fixture's DROP DATABASE)."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, 'consolidating', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
    return factory, sid





@pytest.mark.asyncio
async def test_record_and_hash(migrated_db: Any) -> None:
    _scratch_url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    from packages.domain.models.sessions import ORMSession

    async with factory() as db:
        session = await db.get(ORMSession, sid)
    assert session is not None
    staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))

    payload = {"text": "Почему 42?", "origin": "model_proposal"}
    row: ORMStagingOp | None = None
    async with factory() as db:
        from packages.domain.db.uow import transaction

        async with transaction(db):
            row = await staging.record(
                db, AuditService(db), session, "question", payload, proposed_questions=1
            )
    assert row is not None
    assert row.payload_hash == canonical_sha256(payload)
    assert row.schema_version == 1
    assert row.state == "recorded"

    async with factory() as db:
        got = await db.get(ORMStagingOp, row.id)
        assert got is not None and got.op == "question"


@pytest.mark.asyncio
async def test_reserve_blocks_overflow_before_write(migrated_db: Any) -> None:
    _scratch_url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    from packages.domain.models.sessions import ORMSession

    async with factory() as db:
        session = await db.get(ORMSession, sid)
    assert session is not None
    # tiny limits: only 1 new claim allowed
    staging = StagingService(HostReserveService(ReserveLimits(32, 1, 64, 4)))

    async with factory() as db:
        from packages.domain.db.uow import transaction

        async with transaction(db):
            await staging.record(
                db, AuditService(db), session, "claim", {"statement": "c1"}, proposed_claims=1
            )

    # the second claim overflows max_new_claims_per_session=1 BEFORE the write
    with pytest.raises(StagingBudgetExceeded) as exc_info:
        async with factory() as db:
            from packages.domain.db.uow import transaction

            async with transaction(db):
                await staging.record(
                    db, AuditService(db), session, "claim", {"statement": "c2"},
                    proposed_claims=1,
                )
    assert exc_info.value.counter == "max_new_claims_per_session"
    assert exc_info.value.to_payload()["code"] == "staging_budget_exceeded"

    # only the first claim was recorded
    async with factory() as db:
        rows = (
            (
                await db.execute(
                    select(ORMStagingOp).where(
                        ORMStagingOp.session_id == sid, ORMStagingOp.op == "claim"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_apply_recorded_creates_questions(migrated_db: Any) -> None:
    _scratch_url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    from packages.domain.models.sessions import ORMSession

    async with factory() as db:
        session = await db.get(ORMSession, sid)
    assert session is not None
    staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))

    async with factory() as db:
        from packages.domain.db.uow import transaction

        async with transaction(db):
            await staging.record(
                db, AuditService(db), session, "question",
                {"text": "Вопрос А", "origin": "model_proposal"}, proposed_questions=1,
            )
            await staging.record(
                db, AuditService(db), session, "claim",
                {"statement": "6*7=42", "claim_type": "computed_result"}, proposed_claims=1,
            )

    applied_claims, applied_questions = (0, 0)
    async with factory() as db:
        from packages.domain.db.uow import transaction

        async with transaction(db):
            applied_claims, applied_questions = await staging.apply_recorded(
                db, AuditService(db), session
            )
    assert applied_claims == 1
    assert applied_questions == 1

    # the question row now exists
    async with factory() as db:
        qs = (
            (await db.execute(select(ORMQuestion).where(ORMQuestion.parent_id == session.question_id)))
            .scalars()
            .all()
        )
        assert any(q.text == "Вопрос А" for q in qs)

        # staging rows are applied and not re-appliable
        rows = (
            (await db.execute(select(ORMStagingOp).where(ORMStagingOp.session_id == sid)))
            .scalars()
            .all()
        )
        assert all(r.state == "applied" for r in rows)


def test_reserve_limits_fields() -> None:
    lim = ReserveLimits(32, 16, 64, 4)
    assert lim.max_new_claims_per_session == 16
    assert lim.max_evidence_items_per_session == 64
