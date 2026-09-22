"""Unit (DB): claim retrieval — fulltext + current/pending separation
(T3.9, §5.4.2)."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.cognition.retrieval import (
    INVALID_LABEL,
    PENDING_LABEL,
    retrieve,
)
from packages.domain.models.enums import AssessmentState, EpistemicStatus, FreshnessStatus

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
    search_statements: list[str] | None = None,
    stored_freshness: str = "fresh",
    reverify_after: datetime | None = None,
) -> uuid.UUID:
    """Insert a claim + head (+assessment when current). Returns claim_id."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO claims (id, statement, search_statements, claim_type, "
                "freshness_status, reverify_after) "
                "VALUES (:id, :st, :ss, 'computed_result', :f, :rv)"
            ),
            {
                "id": cid,
                "st": statement,
                "ss": json.dumps(search_statements or []),
                "f": stored_freshness,
                "rv": reverify_after,
            },
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


# ── cross-lingual search (ADR-0006 rev, T7.7) ────────────────────────────


@pytest.mark.asyncio
async def test_retrieval_english_query_finds_russian_claim(
    migrated_db: Any,
) -> None:
    """A Russian claim with an English search_statements rendering is
    found by an ENGLISH query — the exact EVAL-2 failure
    («caching» vs «кэширование» matched nothing)."""
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="Кэширование — сохранение результатов вычислений для повторного использования.",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
        search_statements=[
            "Caching is storing computation results for reuse."
        ],
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        en = await retrieve(db, "what is caching", snapshot_id=snap)
        ru = await retrieve(db, "кэширование", snapshot_id=snap)
    # the cross-lingual hit (the requirement under test)
    assert len(en.current) == 1
    assert en.current[0].statement.startswith("Кэширование")
    # and the native language still works (single-lexeme full match;
    # partial-AND matches rank ~1e-20 = no match, pre-existing FTS
    # semantics, unchanged by the cross-lingual index)
    assert len(ru.current) == 1
    assert ru.current[0].statement.startswith("Кэширование")


@pytest.mark.asyncio
async def test_retrieval_foreign_query_without_rendering_still_empty(
    migrated_db: Any,
) -> None:
    """A claim WITHOUT search_statements is not matched by a foreign-
    language query (no phantom cross-lingual hits)."""
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="Кэширование — сохранение результатов для повторного использования.",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "what is caching", snapshot_id=snap)
    assert res.current == []


# ── freshness rule at the retrieval seam (T7.27, ADR-0014) ──────────────


@pytest.mark.asyncio
async def test_retrieval_does_not_surface_overdue_claim_as_fresh(
    migrated_db: Any,
) -> None:
    """T7.27 (EVAL-4d, §8.6/T3.7): a claim whose ``reverify_after`` is in
    the past is surfaced as DUE — even when the stored
    ``freshness_status`` column still says 'fresh' (no reassessment ran,
    no flip happened). The control claim (deadline in the future) stays
    FRESH; at equal relevance/grade the overdue claim ranks below it."""
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    now = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)
    overdue = await _make_claim(
        engine, snap,
        statement="Ставка ЦБ РФ составляет 14 процентов",
        state="current", epistemic="supported", grade="E3", confidence=0.75,
        stored_freshness="fresh",  # the EVAL-4d state: the cache was never updated
        reverify_after=now - timedelta(days=1),
    )
    within = await _make_claim(
        engine, snap,
        statement="Ставка ЦБ РФ составляет 16 процентов",
        state="current", epistemic="supported", grade="E3", confidence=0.75,
        stored_freshness="fresh",
        reverify_after=now + timedelta(days=29),
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "Ставка ЦБ РФ", snapshot_id=snap, now=now)
    by_id = {c.claim_id: c for c in res.current}
    assert set(by_id) == {overdue, within}
    # the overdue claim is NOT surfaced as fresh
    assert by_id[overdue].freshness is FreshnessStatus.DUE
    assert by_id[within].freshness is FreshnessStatus.FRESH
    # the rule drives the score: equal relevance/grade, the due claim
    # (0.4) ranks below the fresh one (1.0)
    assert by_id[within].score > by_id[overdue].score
    assert res.current[0].claim_id == within
    assert res.current[1].claim_id == overdue


@pytest.mark.asyncio
async def test_retrieval_no_deadline_claim_is_unknown(migrated_db: Any) -> None:
    """A claim without a reverify deadline (valid_to=NULL, open end —
    §8.6) is UNKNOWN, never 'fresh' (T7.27: the stored column is not
    trusted at the retrieval seam)."""
    _url, engine = migrated_db
    snap = await _snapshot_id(engine)
    await _make_claim(
        engine, snap,
        statement="Теорема Пифагора верна для евклидовых плоскостей",
        state="current", epistemic="supported", grade="E4", confidence=0.95,
        stored_freshness="fresh",
        reverify_after=None,
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        res = await retrieve(db, "Теорема Пифагора", snapshot_id=snap)
    assert len(res.current) == 1
    assert res.current[0].freshness is FreshnessStatus.UNKNOWN
