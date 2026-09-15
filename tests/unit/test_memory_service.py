"""Unit (DB): memory service — lifecycle, dedup, freshness, claim view
(T3.6, T3.7, §8.2, §14.1)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import (
    AssessmentState,
    EffectiveGrade,
    EpistemicStatus,
    FreshnessStatus,
)
from packages.domain.models.memory import ORMClaim, ORMClaimAssessmentHead, ORMEvidence
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService, ReserveLimits
from packages.domain.services.staging import StagingService
from packages.memory.evidence import (
    manifest_content_hash,
    session_environment_fields,
)
from packages.memory.service import MemoryService

pytestmark = [pytest.mark.unit]


async def _seed_session(engine: AsyncEngine) -> tuple[async_sessionmaker, uuid.UUID]:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, 'committing', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
    async with factory() as db:
        orm = await db.get(ORMSession, sid)
        assert orm is not None
    return factory, sid


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


async def _record_staging(
    engine: AsyncEngine,
    sid: uuid.UUID,
    ops: list[tuple[str, JsonDict]],
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        session = await db.get(ORMSession, sid)
        assert session is not None
        staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))
        for op, payload in ops:
            if op == "claim":
                await staging.record(db, AuditService(db), session, "claim", payload, proposed_claims=1)
            else:
                await staging.record(db, AuditService(db), session, "evidence", payload, proposed_evidence=1)


def _comp_record(payload: JsonDict) -> Any:
    from packages.domain.models.enums import EvidenceKind
    from packages.domain.schemas.evidence import EvidenceRecord

    return EvidenceRecord(
        kind=EvidenceKind.COMPUTATION,
        identity_hash="h" * 32,
        payload=payload,
    )


@pytest.mark.asyncio
async def test_apply_claim_and_evidence_creates_current_head(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]

    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)

    assert result.claims_created == 1
    assert result.evidence_added == 1
    assert result.assessments == 1
    assert result.problems == ()

    async with factory() as db:
        view = await memory.claim_view(db, (await db.execute(select(ORMClaim))).scalars().first().id)
        assert view is not None
        assert view.assessment_state is AssessmentState.CURRENT
        assert view.epistemic_status is EpistemicStatus.SUPPORTED
        assert view.grade is EffectiveGrade.E2
        assert view.freshness is FreshnessStatus.FRESH
        assert len(view.evidence) == 1
        assert view.evidence[0].kind == "computation"


@pytest.mark.asyncio
async def test_same_claim_reused_and_evidence_deduped(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    ops = [
        ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
    ]
    records = [_comp_record({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]

    # first apply
    await _record_staging(engine, sid, ops)
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        r1 = await memory.apply_claim_staging(db, AuditService(db), session, records)
    assert r1.claims_created == 1 and r1.evidence_added == 1

    # second session, same claim + same evidence → reused + deduped
    _sid2_factory, sid2 = await _seed_session(engine)
    await _record_staging(engine, sid2, ops)
    async with factory() as db, transaction(db):
        session2 = await db.get(ORMSession, sid2)
        assert session2 is not None
        r2 = await memory.apply_claim_staging(db, AuditService(db), session2, records)
    assert r2.claims_created == 0 and r2.claims_reused == 1
    assert r2.evidence_added == 0 and r2.evidence_deduped == 1

    # exactly one claim and one evidence row in the whole DB
    async with factory() as db:
        claims = (await db.execute(select(ORMClaim))).scalars().all()
        evidence = (await db.execute(select(ORMEvidence))).scalars().all()
        assert len(claims) == 1
        assert len(evidence) == 1


@pytest.mark.asyncio
async def test_head_lifecycle_invariant_current_has_assessment(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    await _record_staging(
        engine, sid, [("claim", {"statement": "no evidence claim", "claim_type": "self_model", "scope": {}})]
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        await memory.apply_claim_staging(db, AuditService(db), session, [])

    # a claim with no evidence is still assessed (hypothesis E0) and the
    # head is current with a non-null assessment + epistemic status
    async with factory() as db:
        heads = (await db.execute(select(ORMClaimAssessmentHead))).scalars().all()
        assert len(heads) == 1
        assert heads[0].assessment_state == "current"
        assert heads[0].current_assessment_id is not None
        assert heads[0].epistemic_status == "hypothesis"


@pytest.mark.asyncio
async def test_env_manifest_hash_is_content_addressed(migrated_db: Any) -> None:
    # §8.7.3 (T4.6): the manifest is content-addressed over the FULL
    # field set — the session identity is NOT part of it (two sessions
    # in the same environment share one manifest)
    a = manifest_content_hash(
        session_environment_fields(protocol_hash="p1", tool_schema_hash="t1", seed=42)
    )
    b = manifest_content_hash(
        session_environment_fields(protocol_hash="p1", tool_schema_hash="t1", seed=42)
    )
    c = manifest_content_hash(
        session_environment_fields(protocol_hash="p2", tool_schema_hash="t1", seed=42)
    )
    assert a == b
    assert a != c
