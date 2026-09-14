"""Unit (DB): claim retrieval — fulltext + current/pending separation
(T3.9, §5.4.2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.cognition.retrieval import (
    INVALID_LABEL,
    PENDING_LABEL,
    retrieve,
)
from packages.domain.models.enums import AssessmentState, EpistemicStatus

pytestmark = [pytest.mark.unit]


async def _snapshot_id(engine: AsyncEngine) -> uuid.UUID:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        return (
            (
                await db.execute(
                    text("SELECT id FROM config_snapshots LIMIT 1")
                )
            )
            .scalars()
            .first()
        )


async def _make_claim(
    engine: AsyncEngine,
    snap_id: uuid.UUID,
    *,
    statement: str,
    state: str,
    epistemic: str | None,
    grade: str | None,
    confidence: float | None,
) -> uuid.UUID:
    """Insert a claim + head (+assessment when current). Returns claim_id."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                "VALUES (:id, :st, 'computed_result', 'fresh')"
            ),
            {"id": cid, "st": statement},
        )
        assessment_id = None
        if state == "current":
            assessment_id = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO claim_assessments "
                    "(id, claim_id, effective_grade, epistemic_status, rules_version, "
                    " rules_hash, evidence_set_hash, assessed_scope, confidence, valid) "
                    "VALUES (:id, :c, :g, :e, 'rules-v1', 'h', 'h', '{}'::jsonb, :cf, true)"
                ),
                {"id": assessment_id, "c": cid, "g": grade, "e": epistemic, "cf": confidence},
            )
        await db.execute(
            text(
                "INSERT INTO claim_assessment_heads "
                "(claim_id, config_snapshot_id, assessment_state, current_assessment_id, "
                " epistemic_status, prepared_by) "
                "VALUES (:c, :s, :state, :a, :e, 'session')"
            ),
            {
                "c": cid,
                "s": snap_id,
                "state": state,
                "a": assessment_id,
                "e": epistemic,
            },
        )
    return cid


@pytest.mark.asyncio
async def test_retrieval_fulltext_matches_question(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="Сколько будет 6*7, результат 42",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "Сколько будет 6*7", snapshot_id=snap)
    assert len(res.current) == 1
    assert res.current[0].claim_type == "computed_result"
    assert res.current[0].grade is not None and res.current[0].grade.value == "E2"


@pytest.mark.asyncio
async def test_retrieval_no_match_returns_empty(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="Совершенно другой тезис про яблоки",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "6*7 произведение", snapshot_id=snap)
    assert res.current == []
    assert res.pending_invalid == []


@pytest.mark.asyncio
async def test_pending_claim_carries_label_same_line(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="6*7 это 42, требует переоценки",
        state="pending", epistemic=None, grade=None, confidence=None,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "6*7 переоценка", snapshot_id=snap)
    assert len(res.pending_invalid) == 1
    line = res.pending_invalid[0].line
    assert PENDING_LABEL in line
    assert "6*7 это 42" in line
    # label and statement share the same line
    assert line.index(PENDING_LABEL) < line.index("6*7 это 42")


@pytest.mark.asyncio
async def test_invalid_claim_carries_invalid_label(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="42 это 6*7, оценка сломана",
        state="invalid", epistemic=None, grade=None, confidence=None,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "42 6*7 сломана", snapshot_id=snap)
    assert len(res.pending_invalid) == 1
    assert INVALID_LABEL in res.pending_invalid[0].line


@pytest.mark.asyncio
async def test_current_and_pending_are_separated(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="6*7 текущее знание 42",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
    )
    await _make_claim(
        engine, snap,
        statement="6*7 pending знание 43",
        state="pending", epistemic=None, grade=None, confidence=None,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "6*7 знание", snapshot_id=snap)
    assert len(res.current) == 1
    assert res.current[0].assessment_state is AssessmentState.CURRENT
    assert len(res.pending_invalid) == 1
    assert res.pending_invalid[0].assessment_state is AssessmentState.PENDING


@pytest.mark.asyncio
async def test_epistemic_status_reflected(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="6*7 оспариваемое 42",
        state="current", epistemic="disputed", grade="E1", confidence=0.2,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "6*7 оспариваемое", snapshot_id=snap)
    assert res.current[0].epistemic_status is EpistemicStatus.DISPUTED
