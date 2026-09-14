"""Unit (DB): Context Builder — budgets, hard section, pending label,
ContextPacked audit (T3.8, §5.4, §5.4.2)."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.cognition.context import PENDING_SECTION, ContextBuilder
from packages.cognition.retrieval import PENDING_LABEL
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.services.audit import AuditService
from tests.unit.test_retrieval import _make_claim, _snapshot_id

pytestmark = [pytest.mark.unit]


async def _snapshot(engine: AsyncEngine) -> ORMConfigSnapshot:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
        assert snap is not None
        return snap


async def _sid(engine: AsyncEngine) -> uuid.UUID:
    factory = async_sessionmaker(engine, expire_on_commit=False)
    sid = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sessions (id, state, config_snapshot_id) "
                "VALUES (:id, 'exploring', (SELECT id FROM config_snapshots LIMIT 1))"
            ),
            {"id": sid},
        )
    return sid


@pytest.mark.asyncio
async def test_pack_reserves_protocol_and_reports_budget(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)
    sid = await _sid(engine)
    builder = ContextBuilder(snap)
    assert builder.budgets.validate() == []

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        pack = await builder.build(
            db, AuditService(db), sid,
            protocol="Протокол действий и правила среды.",
            identity="Идентичность NOEZEMA.",
            question_text="Сколько будет 6*7?",
            plan="План исследования.",
            last_session="",
            messages=[],
            recent_errors=[],
        )
    assert pack.input_budget == 26624
    assert pack.tokenizer_fingerprint
    # hard protocol section present and within its budget
    proto = pack.section("protocol")
    assert proto is not None
    assert proto.budget == 4096
    assert proto.tokens <= proto.budget
    # every section is within its budget
    for name, sec in pack.sections.items():
        assert sec.tokens <= sec.budget, name


@pytest.mark.asyncio
async def test_pack_includes_current_claim_and_labels_pending(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)
    snap_id = await _snapshot_id(engine)
    sid = await _sid(engine)

    await _make_claim(
        engine, snap_id,
        statement="6*7 равно 42, текущее знание",
        state="current", epistemic="supported", grade="E2", confidence=0.55,
    )
    await _make_claim(
        engine, snap_id,
        statement="6*7 pending, требует переоценки",
        state="pending", epistemic=None, grade=None, confidence=None,
    )

    builder = ContextBuilder(snap)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        pack = await builder.build(
            db, AuditService(db), sid,
            protocol="Протокол.",
            identity="Идентичность.",
            question_text="6*7 текущее знание переоценки",
            plan="План.",
            last_session="",
            messages=[],
            recent_errors=[],
        )

    claims_sec = pack.section("claims_evidence")
    assert claims_sec is not None
    assert "6*7 равно 42" in claims_sec.content
    # the pending claim is in its OWN section, labeled on the same line
    pending_sec = pack.section(PENDING_SECTION)
    assert pending_sec is not None
    assert PENDING_LABEL in pending_sec.content
    assert "6*7 pending" in pending_sec.content
    # pending claim ids are recorded separately for the audit
    assert len(pack.pending_claim_ids) == 1


@pytest.mark.asyncio
async def test_pack_pending_excluded_entirely_when_no_budget(migrated_db: Any) -> None:
    """If the pending budget cannot fit the labeled line, the claim is
    excluded ENTIRELY, never unlabeled (§5.4.2)."""
    _url, engine = migrated_db
    snap = await _snapshot(engine)
    snap_id = await _snapshot_id(engine)
    sid = await _sid(engine)

    # a very long statement that won't fit a tiny pending budget
    long_stmt = "6*7 " + "очень длинное утверждение для проверки бюджета ".join([str(i) for i in range(40)])
    await _make_claim(
        engine, snap_id,
        statement=long_stmt,
        state="pending", epistemic=None, grade=None, confidence=None,
    )

    builder = ContextBuilder(snap)
    # shrink the separate pending budget to force exclusion
    builder.pending_budget = 1
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        pack = await builder.build(
            db, AuditService(db), sid,
            protocol="Протокол.",
            identity="Идентичность.",
            question_text="6*7 проверка " + " ".join(str(i) for i in range(40)),
            plan="План.",
            last_session="",
            messages=[],
            recent_errors=[],
        )
    pending_sec = pack.section(PENDING_SECTION)
    assert pending_sec is not None
    assert pending_sec.content == ""  # excluded entirely
    assert PENDING_LABEL not in pending_sec.content
    # the exclusion is recorded with the labeled-line reason
    assert any(
        e.get("section") == PENDING_SECTION and e.get("reason") == "no_budget_for_labeled_line"
        for e in pack.exclusions
    )


@pytest.mark.asyncio
async def test_context_packed_audit_event_recorded(migrated_db: Any) -> None:
    _url, engine = migrated_db
    snap = await _snapshot(engine)
    sid = await _sid(engine)
    builder = ContextBuilder(snap)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await builder.build(
            db, AuditService(db), sid,
            protocol="Протокол.", identity="Идентичность.",
            question_text="Вопрос.", plan="План.",
            last_session="", messages=[], recent_errors=[],
        )
    async with factory() as db:
        rows = (
            await db.execute(
                text(
                    "SELECT payload FROM audit_events "
                    "WHERE type='context_packed' AND session_id=:s"
                ),
                {"s": sid},
            )
        ).fetchall()
    assert len(rows) == 1
    payload = rows[0][0]
    assert payload["tokenizer_fingerprint"]
    assert "token_counts" in payload
    assert "included_chunks" in payload
    assert "pending_claim_ids" in payload
    assert "exclusions" in payload
