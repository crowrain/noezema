"""Security invariants (T3.26, §8.2, §8.7.1, §14).

End-to-end invariants through the DB (not just the isolated rules engine):
- duplicate evidence does not raise the grade;
- counterevidence moves the head in the same commit;
- offline publish is either the old pointer or a complete new pointer +
  questions (atomic);
- pending/invalid is never served as current;
- the grade is produced only by the rules engine.
"""

from __future__ import annotations

import copy
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl import offline_rules as orl
from packages.domain.config import BOOTSTRAP_PAYLOAD, QUESTION_UUID5_NAMESPACE
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import AssessmentState, EvidenceKind
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.evidence import EvidenceRecord
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService, ReserveLimits
from packages.domain.services.staging import StagingService
from packages.memory.service import MemoryService

pytestmark = [pytest.mark.security]


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
        assert await db.get(ORMSession, sid) is not None
    return factory, sid


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


def _comp(payload: JsonDict) -> EvidenceRecord:
    return EvidenceRecord(kind=EvidenceKind.COMPUTATION, identity_hash="h" * 32, payload=payload)


async def _record_staging(engine: AsyncEngine, sid: uuid.UUID, ops: list[tuple[str, JsonDict]]) -> None:
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


async def _head(db, claim_id: uuid.UUID, snap_id: uuid.UUID) -> Any:
    row = (
        (
            await db.execute(
                text(
                    "SELECT assessment_state, epistemic_status, effective_grade "
                    "FROM claim_assessment_heads h LEFT JOIN claim_assessments a "
                    "ON a.id = h.current_assessment_id "
                    "WHERE h.claim_id=:c AND h.config_snapshot_id=:s"
                ),
                {"c": claim_id, "s": snap_id},
            )
        )
        .mappings()
        .first()
    )
    return row


@pytest.mark.asyncio
async def test_duplicate_evidence_does_not_raise_grade(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    # record the SAME computation evidence twice against one claim
    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
        ],
    )
    records = [_comp({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        await memory.apply_claim_staging(db, AuditService(db), session, records)

    async with factory() as db:
        # the duplicate evidence is deduped: exactly one evidence row
        ev_count = (
            await db.execute(text("SELECT count(*) FROM evidence"))
        ).scalar_one()
        assert int(ev_count) == 1
        head = (
            (
                await db.execute(
                    text(
                        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade "
                        "FROM claim_assessment_heads h LEFT JOIN claim_assessments a "
                        "ON a.id=h.current_assessment_id WHERE h.config_snapshot_id=:s"
                    ),
                    {"s": snap.id},
                )
            )
            .mappings()
            .first()
        )
        assert head is not None
        # computed_result with one supporting computation -> supported E2
        assert head["assessment_state"] == "current"
        assert head["epistemic_status"] == "supported"
        assert head["effective_grade"] == "E2"


@pytest.mark.asyncio
async def test_counterevidence_moves_head_same_commit(migrated_db: Any) -> None:
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine)
    snap = await _snapshot(engine)

    # one claim, one support + one counter, all in the same commit
    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": "6*7 равно 42", "claim_type": "computed_result", "scope": {"expr": "6*7"}}),
            ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
            ("evidence", {"evidence_index": 1, "claim_index": 0, "relation": "counters"}),
        ],
    )
    records = [
        _comp({"code": "print(6*7)", "stdout": "42", "exit_code": 0}),
        _comp({"code": "print(6*7)", "stdout": "41", "exit_code": 0}),
    ]
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        await memory.apply_claim_staging(db, AuditService(db), session, records)

    async with factory() as db:
        head = (
            (
                await db.execute(
                    text(
                        "SELECT h.assessment_state, h.epistemic_status, a.effective_grade "
                        "FROM claim_assessment_heads h LEFT JOIN claim_assessments a "
                        "ON a.id=h.current_assessment_id WHERE h.config_snapshot_id=:s"
                    ),
                    {"s": snap.id},
                )
            )
            .mappings()
            .first()
        )
        assert head is not None
        # counterevidence caps the claim at disputed E1 (never supported)
        assert head["epistemic_status"] == "disputed"
        assert head["effective_grade"] == "E1"
        assert head["assessment_state"] == "current"


@pytest.mark.asyncio
async def test_pending_invalid_never_served_as_current(migrated_db: Any) -> None:
    """A claim whose head is pending/invalid under the effective snapshot is
    not returned as a current claim by the claim view / retrieval."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # seed a claim + a PENDING head under the effective snapshot (no
    # current assessment)
    cid = uuid.uuid4()
    async with factory() as db, db.begin():
        snap_id = (await db.execute(text("SELECT id FROM config_snapshots LIMIT 1"))).scalar_one()
        await db.execute(
            text("INSERT INTO claims (id, statement, claim_type, freshness_status) VALUES (:id, :s, :ct, 'unknown')"),
            {"id": cid, "s": "temporal claim без as_of", "ct": "temporal_fact"},
        )
        await db.execute(
            text(
                "INSERT INTO claim_assessment_heads (claim_id, config_snapshot_id, assessment_state, "
                "current_assessment_id, epistemic_status, prepared_by) "
                "VALUES (:c, :s, 'pending', NULL, NULL, 'rules_activation')"
            ),
            {"c": cid, "s": snap_id},
        )
    # retrieval must not surface it as a current claim
    from packages.cognition.retrieval import retrieve

    async with factory() as db:
        snap_id = (await db.execute(text("SELECT id FROM config_snapshots LIMIT 1"))).scalar_one()
        res = await retrieve(db, "temporal claim", snapshot_id=snap_id)
        assert all(c.claim_id != cid for c in res.current)
        # it may appear in the separately-limited pending/invalid list, never in current
        if any(c.claim_id == cid for c in res.pending_invalid):
            matching = next(c for c in res.pending_invalid if c.claim_id == cid)
            assert matching.assessment_state is AssessmentState.PENDING
    await engine.dispose()


@pytest.mark.asyncio
async def test_grade_produced_only_by_rules_engine(migrated_db: Any) -> None:
    """Every current head carries the rules-engine fingerprint: a rules_hash
    and a grade that is exactly the rule's supported grade (no other
    producer)."""
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
    records = [_comp({"code": "print(6*7)", "stdout": "42", "exit_code": 0})]
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        await memory.apply_claim_staging(db, AuditService(db), session, records)
    async with factory() as db:
        row = (
            (
                await db.execute(
                    text(
                        "SELECT a.effective_grade, a.rules_hash, a.rules_version, "
                        "c.claim_type FROM claim_assessments a JOIN claims c ON c.id=a.claim_id "
                        "WHERE a.valid"
                    )
                )
            )
            .mappings()
            .first()
        )
        assert row is not None
        assert row["rules_hash"]  # the grade came from the rules engine
        # T7.17: the assessment is produced by the host-derived scope
        # engine (rules-v2)
        assert row["rules_version"] == "rules-v2"
        # computed_result's min_grade_for_supported is E2 -> the grade is E2
        assert row["effective_grade"] == "E2"
    await engine.dispose()


@pytest.mark.asyncio
async def test_offline_publish_is_atomic(migrated_db: Any) -> None:
    """After a successful offline publish, the pointer is on the candidate
    AND every invalid head has its deterministic question present (the
    all-or-nothing flip)."""
    _url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    ns = uuid.UUID(QUESTION_UUID5_NAMESPACE)
    cid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text("INSERT INTO claims (id, statement, claim_type, freshness_status) VALUES (:id, :s, :ct, 'unknown')"),
            {"id": cid, "s": "тезис формальной теоремы", "ct": "formal_theorem"},
        )
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    payload["claim_type_rules"].pop("formal_theorem")
    async with factory() as db, db.begin():
        audit = AuditService(db)
        res = await orl.run_offline_change(db, audit, requested_payload=payload, question_namespace=ns)
    assert res.published
    async with factory() as db:
        # the pointer moved to the candidate
        active = (
            await db.execute(text("SELECT active_config_snapshot_id FROM runtime_config_heads"))
        ).scalar_one()
        assert active == res.candidate_id
        # the invalid head's question exists (atomic: pointer + questions)
        qid = uuid.uuid5(ns, f"invalid-assessment:{res.candidate_id}:{cid}")
        q = (
            await db.execute(text("SELECT 1 FROM questions WHERE id=:q"), {"q": qid})
        ).scalar_one_or_none()
        assert q is not None
    await engine.dispose()
