"""Async tests for CuratorService — SQLite in-memory backend."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

from packages.domain.models.base import Base
from packages.domain.models.orm_claim import ORMClaim, ORMEvidence, ORMClaimAssessment, ORMClaimAssessmentHead
from packages.domain.models.orm_session import ORMSession, ORMQuestion
from packages.domain.services.curator_service import CuratorService
from packages.domain.services.memory_service import MemoryService
from packages.memory.rules_engine import RulesEngine


# SQLite in-memory (synchronous schema setup, async queries)
TEST_URL = "sqlite+aiosqlite:///:memory:"
engine = create_async_engine(TEST_URL, echo=False)
SessionFactory = async_sessionmaker(engine, expire_on_commit=False)


async def create_tables():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def teardown():
    await engine.dispose()


async def test_generate_claims_from_evidence():
    """Curator groups evidence by tool and creates separate claims."""
    await create_tables()
    rules = RulesEngine()

    async with SessionFactory() as session:
        curator = CuratorService(session, rules)

        evidence = [
            {"tool": "workspace.read", "result": {"content": "abc"}},
            {"tool": "workspace.read", "result": {"content": "def"}},
            {"tool": "memory.search", "result": {"hits": 1}},
        ]

        claims = await curator.generate_claims_from_evidence(
            session_id=uuid.uuid4(),
            evidence_list=evidence,
        )

        assert len(claims) == 2  # workspace.read + memory.search

        # Check evidence counts per claim
        for claim in claims:
            from sqlalchemy import select
            ev_result = await session.execute(
                select(ORMEvidence).where(ORMEvidence.claim_id == claim.id)
            )
            evs = ev_result.scalars().all()
            assert len(evs) >= 1
            for ev in evs:
                assert ev.identity_hash  # hash is set

        await session.commit()

    await teardown()


async def test_reconcile_claims_duplicates():
    """Reconciliation merges claims with identical statements."""
    await create_tables()

    sid = uuid.uuid4()
    async with SessionFactory() as session:
        # Two identical claims
        for _ in range(2):
            claim = ORMClaim(
                statement="Duplicate statement",
                claim_type="external_fact",
                freshness_status="fresh",
                created_in_session=sid,
            )
            session.add(claim)
        await session.flush()

        # Add evidence to second claim so it gets migrated
        second = (await session.execute(
            select(ORMClaim).where(ORMClaim.created_in_session == sid)
        )).scalars().all()[1]
        ev = ORMEvidence(
            claim_id=second.id,
            relation="supports",
            evidence_kind="test",
            identity_hash="hash123",
        )
        session.add(ev)
        await session.flush()

        # Reconcile
        rules = RulesEngine()
        curator = CuratorService(session, rules)
        report = await curator.reconcile_claims(sid)

        assert report["duplicates_merged"] == 1
        assert report["claims_remaining"] == 1

        # Verify evidence was migrated
        remaining = (await session.execute(
            select(ORMClaim).where(ORMClaim.created_in_session == sid)
        )).scalars().all()
        assert len(remaining) == 1

        ev_count = (await session.execute(
            select(ORMEvidence).where(ORMEvidence.claim_id == remaining[0].id)
        )).scalars().all()
        assert len(ev_count) == 1

        await session.commit()

    await teardown()


async def test_curtail_stale_claims():
    """Claims older than max_age_days are marked stale."""
    from datetime import datetime, timedelta
    await create_tables()

    sid = uuid.uuid4()
    async with SessionFactory() as session:
        old_claim = ORMClaim(
            statement="Old claim",
            claim_type="external_fact",
            freshness_status="fresh",
            valid_from=datetime.utcnow() - timedelta(days=60),
            created_in_session=sid,
        )
        fresh_claim = ORMClaim(
            statement="Fresh claim",
            claim_type="external_fact",
            freshness_status="fresh",
            valid_from=datetime.utcnow(),
            created_in_session=sid,
        )
        session.add_all([old_claim, fresh_claim])
        await session.flush()

        rules = RulesEngine()
        curator = CuratorService(session, rules)

        curtailed = await curator.curtail_stale_claims(max_age_days=30)

        assert curtailed == 1

        # Refresh and check
        await session.refresh(old_claim)
        await session.refresh(fresh_claim)
        assert old_claim.freshness_status == "stale"
        assert fresh_claim.freshness_status == "fresh"

        await session.commit()

    await teardown()


async def test_memory_service_create_and_assess():
    """MemoryService: create claim, add evidence, assess → E2 with 1 source."""
    await create_tables()
    rules = RulesEngine()

    async with SessionFactory() as session:
        mem = MemoryService(session, rules)

        claim = await mem.create_claim(
            statement="Test claim",
            claim_type="external_fact",
            session_id=uuid.uuid4(),
        )
        assert claim.id is not None

        await mem.add_evidence(
            claim_id=claim.id,
            relation="supports",
            evidence_kind="source_assertion",
            identity_hash="unique123",
        )

        assessment = await mem.assess_claim(claim.id, session_id=claim.created_in_session)

        assert assessment.effective_grade == "E2"
        assert assessment.epistemic_status == "supported"
        assert assessment.confidence == 0.6

        await session.commit()

    await teardown()
