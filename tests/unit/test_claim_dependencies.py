"""Unit (DB): claim dependencies (T4.1, §8.6) — the DAG cycle check at
the commit boundary, edge validation, research vs evidential kinds."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.memory import ORMClaim, ORMClaimDependency
from packages.domain.models.sessions import ORMSession
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService, ReserveLimits
from packages.domain.services.staging import StagingService
from packages.memory.service import MemoryService, find_evidential_cycles

pytestmark = [pytest.mark.unit]


# ─── pure cycle check ───────────────────────────────────────────────────────


def _u(s: str) -> uuid.UUID:
    return uuid.UUID(int=int(s, 16))


def test_cycle_check_empty() -> None:
    assert find_evidential_cycles([], []) == set()


def test_cycle_check_closes_existing_chain() -> None:
    # existing a -> b -> c; a new c -> a closes the cycle
    existing = [(_u("a"), _u("b")), (_u("b"), _u("c"))]
    new = [(_u("c"), _u("a"))]
    assert find_evidential_cycles(existing, new) == {0}


def test_cycle_check_non_cycle_ok() -> None:
    existing = [(_u("a"), _u("b"))]
    new = [(_u("b"), _u("a"))]  # existing a -> b plus new b -> a: a cycle
    assert find_evidential_cycles(existing, new) == {0}
    # the non-cycle case: a new claim c depends on a (no path from a back to c)
    existing2 = [(_u("a"), _u("b"))]
    new2 = [(_u("c"), _u("a"))]
    assert find_evidential_cycles(existing2, new2) == set()
    # but b -> a with a -> b existing IS a cycle; the non-cycle case:
    existing2 = [(_u("a"), _u("b"))]
    new2 = [(_u("c"), _u("a"))]  # new claim c depends on a: a reachable from a? path from a: a->b; no
    assert find_evidential_cycles(existing2, new2) == set()


def test_cycle_check_two_new_edges() -> None:
    # no existing edges; new a -> b and b -> a close a cycle between themselves
    existing: list[tuple[uuid.UUID, uuid.UUID]] = []
    new = [(_u("a"), _u("b")), (_u("b"), _u("a"))]
    assert find_evidential_cycles(existing, new) == {0, 1}


def test_cycle_check_duplicate_existing_edge_is_not_a_cycle() -> None:
    existing = [(_u("a"), _u("b"))]
    new = [(_u("a"), _u("b"))]  # re-proposing the same edge: path from b never reaches a
    assert find_evidential_cycles(existing, new) == set()


# ─── DB: apply_claim_staging with dependencies ──────────────────────────────


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


async def _record_claim(
    engine: AsyncEngine,
    sid: uuid.UUID,
    payload: JsonDict,
) -> None:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        session = await db.get(ORMSession, sid)
        assert session is not None
        staging = StagingService(HostReserveService(ReserveLimits(32, 16, 64, 4)))
        await staging.record(db, AuditService(db), session, "claim", payload, proposed_claims=1)


async def _apply(
    factory: async_sessionmaker,
    sid: uuid.UUID,
    snap: ORMConfigSnapshot,
    records: list[Any] = (),
) -> Any:
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        return await memory.apply_claim_staging(db, AuditService(db), session, list(records))


@pytest.mark.asyncio
async def test_dependency_edge_committed_and_cycle_rejected(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)

    # session 1: claim B (no deps)
    f1, s1 = await _seed_session(engine)
    await _record_claim(engine, s1, {"statement": "B: сумма", "claim_type": "computed_result", "scope": {}})
    r1 = await _apply(f1, s1, snap)
    assert r1.claims_created == 1

    factory0 = async_sessionmaker(engine, expire_on_commit=False)

    async def _claim_by_statement(stmt: str) -> uuid.UUID:
        async with factory0() as db:
            row = (
                await db.execute(
                    select(ORMClaim.id).where(ORMClaim.statement == stmt)
                )
            ).scalar_one()
            return row if isinstance(row, uuid.UUID) else uuid.UUID(str(row))

    b_id = await _claim_by_statement("B: сумма")

    # session 2: claim A depends on B (evidential)
    f2, s2 = await _seed_session(engine)
    await _record_claim(
        engine,
        s2,
        {
            "statement": "A: вывод из B",
            "claim_type": "computed_result",
            "scope": {},
            "dependencies": [{"claim_id": str(b_id), "kind": "evidential"}],
        },
    )
    r2 = await _apply(f2, s2, snap)
    assert r2.dependencies_added == 1
    assert r2.dependencies_evidential_added == 1
    assert r2.dependencies_rejected == ()

    async with factory0() as db:
        edges = (await db.execute(select(ORMClaimDependency))).scalars().all()
    assert len(edges) == 1
    assert edges[0].to_claim_id == b_id
    assert edges[0].kind == "evidential"

    # session 3: REUSED claim B with a dependency B -> A: the new edge
    # plus the existing A -> B close the cycle; the edge must be
    # rejected and nothing new written (the claim still commits)
    f3, s3 = await _seed_session(engine)
    a_id = await _claim_by_statement("A: вывод из B")
    await _record_claim(
        engine,
        s3,
        {
            "statement": "B: сумма",
            "claim_type": "computed_result",
            "scope": {},
            "dependencies": [{"claim_id": str(a_id), "kind": "evidential"}],
        },
    )
    r3 = await _apply(f3, s3, snap)
    assert r3.claims_reused == 1
    assert r3.dependencies_added == 0
    assert len(r3.dependencies_rejected) == 1
    assert "cycle" in r3.dependencies_rejected[0]

    async with factory0() as db:
        edges = (await db.execute(select(ORMClaimDependency))).scalars().all()
    assert len(edges) == 1  # the cyclic edge was not written

    # session 4: the SAME edge as kind=research: research deps are not
    # cycle-checked and do not touch the evidential graph
    f4, s4 = await _seed_session(engine)
    await _record_claim(
        engine,
        s4,
        {
            "statement": "B: сумма",
            "claim_type": "computed_result",
            "scope": {},
            "dependencies": [{"claim_id": str(a_id), "kind": "research"}],
        },
    )
    r4 = await _apply(f4, s4, snap)
    assert r4.dependencies_added == 1
    assert r4.dependencies_evidential_added == 0

    async with factory0() as db:
        edges = (await db.execute(select(ORMClaimDependency))).scalars().all()
    assert len(edges) == 2


@pytest.mark.asyncio
async def test_invalid_dependency_edges_rejected(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)

    f1, s1 = await _seed_session(engine)
    await _record_claim(engine, s1, {"statement": "X base", "claim_type": "external_fact", "scope": {}})
    await _apply(f1, s1, snap)

    factory0 = async_sessionmaker(engine, expire_on_commit=False)
    async with factory0() as db:
        x_id = (await db.execute(select(ORMClaim.id))).scalar_one()
        x_id = x_id if isinstance(x_id, uuid.UUID) else uuid.UUID(str(x_id))

    # session 2: a reused X with every flavor of a bad edge
    f2, s2 = await _seed_session(engine)
    await _record_claim(
        engine,
        s2,
        {
            "statement": "X base",
            "claim_type": "external_fact",
            "scope": {},
            "dependencies": [
                {"claim_id": str(x_id), "kind": "evidential"},  # self-dependency
                {"claim_id": str(uuid.uuid4()), "kind": "evidential"},  # missing target
                {"claim_id": str(x_id), "kind": "friend"},  # bad kind
                42,  # not a dict
            ],
        },
    )
    r2 = await _apply(f2, s2, snap)
    assert r2.dependencies_added == 0
    assert len(r2.dependencies_rejected) == 4
    joined = " | ".join(r2.dependencies_rejected)
    assert "self-dependency" in joined
    assert "missing" in joined
    assert "bad dependency kind" in joined
    assert "unparseable" in joined

    # the audit twin recorded the rejections
    async with factory0() as db:
        n = (
            await db.execute(
                text(
                    "SELECT count(*) FROM audit_events "
                    "WHERE type = 'dependency_edge_rejected'"
                )
            )
        ).scalar_one()
    assert n == 1


@pytest.mark.asyncio
async def test_evidential_edge_to_noncurrent_target_rejected(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)

    f1, s1 = await _seed_session(engine)
    await _record_claim(engine, s1, {"statement": "X current", "claim_type": "external_fact", "scope": {}})
    await _apply(f1, s1, snap)

    factory0 = async_sessionmaker(engine, expire_on_commit=False)
    async with factory0() as db:
        x_id = (await db.execute(select(ORMClaim.id))).scalar_one()
        x_id = x_id if isinstance(x_id, uuid.UUID) else uuid.UUID(str(x_id))
    # §8.6: a pending/invalid head is not an acting dependency —
    # force the head to pending (M4 cascade will produce these for real)
    async with factory0() as db, db.begin():
        await db.execute(
            text(
                "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
                "current_assessment_id = NULL, epistemic_status = NULL"
            )
        )

    f2, s2 = await _seed_session(engine)
    await _record_claim(
        engine,
        s2,
        {
            "statement": "Y depends",
            "claim_type": "external_fact",
            "scope": {},
            "dependencies": [
                {"claim_id": str(x_id), "kind": "evidential"},  # refused: target not current
                {"claim_id": str(x_id), "kind": "research"},  # allowed: explicit marker
            ],
        },
    )
    r2 = await _apply(f2, s2, snap)
    assert r2.dependencies_added == 1
    assert r2.dependencies_evidential_added == 0
    assert len(r2.dependencies_rejected) == 1
    assert "non-current" in r2.dependencies_rejected[0]
