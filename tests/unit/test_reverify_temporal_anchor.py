"""Unit (DB): опора перепроверки существующего утверждения (T7.73, ADR-0018).

Тот же путь, что исполняет fenced-транзакция фиксации (`MemoryService.
apply_claim_staging`), но на временном факте с внешними источниками — ровно
форма кейса подставки .92:

* сессия 1 заводит `temporal_fact` («годовая инфляция …») с двумя
  независимыми источниками → E3 supported;
* сессия 2 перепроверяет его по хостовому id: вопрос без даты, в предложении
  `as_of` отсутствует (как и велит правило «Перепроверка»), источник третий.

Дефект был здесь же (`packages/memory/service.py`, ветка reverify): опорная дата
утверждения безусловно перезаписывалась выводом из ВОПРОСА ПЕРЕПРОВЕРКИ —
`(None, none)` — и rules engine честно докладывал `as_of_missing`, срезая E3
supported до E1 hypothesis. Бейдж «Подтверждено» падал в сессии, которая
только добавила доказательство.

Тесты красные на прежнем коде (кейс 2 → `as_of_missing` вместо сохранённой
даты и E3); проверено временным откатом, STATUS T7.73.
"""

from __future__ import annotations

import hashlib
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import (
    AssessmentState,
    EffectiveGrade,
    EpistemicStatus,
    EvidenceKind,
)
from packages.domain.models.memory import ORMClaim
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.evidence import EvidenceRecord
from packages.domain.services.audit import AuditService
from packages.memory.service import MemoryService
from tests.unit.test_memory_service import (
    _apply_reverify,
    _record_staging,
    _seed_session,
    _snapshot,
)

pytestmark = pytest.mark.unit


def _sha(tag: str) -> str:
    return hashlib.sha256(f"noezema-t773/{tag}".encode()).hexdigest()


async def _seed_source_at(engine: AsyncEngine, url: str) -> uuid.UUID:
    """One durable host-verified source row on its own registrable domain."""
    factory = async_sessionmaker(engine, expire_on_commit=False)
    source_id = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO sources (id, source_type, canonical_uri, retrieved_at, "
                "content_hash) VALUES (:id, 'external_url', :uri, now(), :hash)"
            ),
            {"id": str(source_id), "uri": url, "hash": _sha(url)},
        )
    return source_id


def _source_record(source_id: uuid.UUID, url: str) -> EvidenceRecord:
    """A host adapter source_assertion record (the T7.8 payload convention)."""
    return EvidenceRecord(
        kind=EvidenceKind.SOURCE_ASSERTION,
        identity_hash="i" * 32,
        payload={
            "original_sha256": _sha(url),
            "chunk_id": "chunk-0",
            "url": url,
        },
        source_id=str(source_id),
        chunk_id="chunk-0",
    )


async def _seed_sources(engine: AsyncEngine, urls: tuple[str, ...]) -> list[EvidenceRecord]:
    records: list[EvidenceRecord] = []
    for url in urls:
        source_id = await _seed_source_at(engine, url)
        records.append(_source_record(source_id, url))
    return records


STATEMENT = "Годовая инфляция в 2025 году составила 5,59 процентного пункта"
#: Сессия 1: относительный вопрос («на текущую дату») → опорная дата сессии (ADR-0016).
ANCHOR_QUESTION = f"Какова {STATEMENT.lower()} на текущую дату?"
#: Сессия 2: перепроверка. Даты в вопросе нет — именно эта форма и стёрла дату.
REVERIFY_QUESTION = "Перепроверь это утверждение по независимым источникам."

ALPHA = "https://alpha.example/cpd"
BETA = "https://beta.example/cpd"
GAMMA = "https://gamma.example/news"


async def _anchor_temporal_claim(
    engine: AsyncEngine,
) -> tuple[async_sessionmaker, uuid.UUID, uuid.UUID]:
    """Session 1 (the anchor): temporal_fact + two independent sources → E3
    supported. Returns (factory, session_id, claim_id).

    The E3 baseline is asserted here on purpose: every assertion below says
    what the reverify did TO it (on the old code this session already fails
    nothing — it is the follow-up that broke)."""
    factory, sid = await _seed_session(engine, question_text=ANCHOR_QUESTION)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (ALPHA, BETA))
    await _record_staging(
        engine,
        sid,
        [
            (
                "claim",
                {
                    "statement": STATEMENT,
                    "claim_type": "temporal_fact",
                    # модельная as_of при относительном вопросе — только аудит:
                    # дату решения хост (ADR-0016) она не сдвигает
                    "as_of": "2026-01-21T00:00:00+00:00",
                    "scope": {"объект": "годовая инфляция"},
                },
            ),
            *[
                ("evidence", {"evidence_index": i, "claim_index": 0, "relation": "supports"})
                for i in range(len(records))
            ],
        ],
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)
    assert result.problems == ()
    assert result.claims_created == 1 and result.evidence_added == 2

    async with factory() as db:
        claim = (await db.execute(select(ORMClaim))).scalars().one()
        view = await memory.claim_view(db, claim.id)
    assert view is not None
    assert view.assessment_state is AssessmentState.CURRENT
    assert view.grade is EffectiveGrade.E3, f"the E3 baseline broke: {view.grade}"
    assert view.epistemic_status is EpistemicStatus.SUPPORTED
    return factory, sid, claim.id


def _reverify_ops(claim_id: uuid.UUID, *, as_of: str | None = None) -> list[tuple[str, JsonDict]]:
    """Session 2's proposal: reverify by the host-issued id, `as_of` absent —
    the documented shape (curator rule «Перепроверка», пример ADR-0018)."""
    payload: JsonDict = {
        "statement": STATEMENT,
        # форма предложения как на подставке: модель не указала temporal_fact —
        # оценку хост всё равно делает по типу ЯКОРЯ (ADR-0018)
        "claim_type": "external_fact",
        "scope": {},
        "existing_claim_id": str(claim_id),
    }
    if as_of is not None:
        payload["as_of"] = as_of
    return [
        ("claim", payload),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
    ]


async def _rows(db: Any, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    return list((await db.execute(text(sql), params or {})).all())


# ─── кейс 1 → кейс 2: перепроверка без даты не роняет оценку ────────────────


@pytest.mark.asyncio
async def test_reverify_without_a_date_keeps_as_of_and_grade(migrated_db: Any) -> None:
    """Требуемый репродюсер: сессия 2 приносит новое доказательство и НИКАКОЙ
    даты — опорная дата якоря остаётся, оценка не понижена, доказательства
    объединены (старые два + новый третий)."""
    _url, engine = migrated_db
    _factory, anchor_sid, claim_id = await _anchor_temporal_claim(engine)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (GAMMA,))

    factory2, sid2, result = await _apply_reverify(
        engine, snap, _reverify_ops(claim_id), REVERIFY_QUESTION, records
    )
    assert result.claims_created == 0
    assert result.claims_reverified == 1
    assert result.assessments == 1
    assert result.problems == ()
    # новое доказательство добавлено, старые не тронуты (дедуп по идентичности)
    assert result.evidence_added == 1 and result.evidence_deduped == 0

    async with factory2() as db:
        claim = await db.get(ORMClaim, claim_id)
        assert claim is not None
        # (а) опорная дата ЦЕЛА: именно её прежний код обнулил
        assert claim.as_of is not None, "reverify erased the anchor's reference date"
        stored_as_of = claim.as_of

        # (б) оценка не понижена
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.grade is EffectiveGrade.E3, f"grade lowered: {view.grade}"
        assert view.epistemic_status is EpistemicStatus.SUPPORTED

        # (в) причина оценки — «требования выполнены», а не «нет опорной даты»
        reasons = await _rows(
            db,
            "SELECT payload->>'reasons' FROM audit_events WHERE type = 'claim_assessed' "
            "AND session_id = :s",
            {"s": sid2},
        )
        assert reasons, "no assessment audit for the reverify session"
        assert '"as_of_missing"' not in str(reasons[0][0]), reasons[0][0]

        # (г) объединение доказательств: 3 ряда, три разных источника,
        #     два старых по-прежнему привязаны к сессии якоря
        evidence = await _rows(
            db,
            "SELECT source_id, created_in_session FROM evidence WHERE claim_id = :c",
            {"c": str(claim_id)},
        )
        assert len(evidence) == 3
        # the anchor's two rows are still there (the union, not a replacement)
        assert sum(1 for row in evidence if str(row[1]) == str(anchor_sid)) == 2

        # (д) лента называет, что случилось с датой якоря: оставлена, не подменена
        audit = await _rows(
            db,
            "SELECT payload FROM audit_events WHERE type = 'claim_reverified' AND session_id = :s",
            {"s": sid2},
        )
        note = dict(audit[0][0])
        assert note["anchor_kept"] is True
        assert note["stored_as_of"] == stored_as_of.isoformat()
        assert note["assessed_as_of"] == stored_as_of.isoformat()
        assert "as_of_conflict" not in note


@pytest.mark.asyncio
async def test_reverify_with_a_different_proposed_date_is_not_applied(migrated_db: Any) -> None:
    """Контр-случай по ADR-0018: ДРУГОЕ непустое значение в предложении — не
    молчаливая подмена. Дата якоря остаётся, отказанное значение видно в ленте
    (и именно там оно разбирается как ревизия/опровержение, а не как дата)."""
    _url, engine = migrated_db
    _factory, _anchor_sid, claim_id = await _anchor_temporal_claim(engine)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (GAMMA,))

    factory2, sid2, result = await _apply_reverify(
        engine,
        snap,
        _reverify_ops(claim_id, as_of="1995-01-01T00:00:00+00:00"),
        REVERIFY_QUESTION,
        records,
    )
    assert result.claims_reverified == 1

    async with factory2() as db:
        claim = await db.get(ORMClaim, claim_id)
        assert claim is not None
        assert claim.as_of is not None and claim.as_of.year != 1995
        audit = dict(
            (
                await _rows(
                    db,
                    "SELECT payload FROM audit_events WHERE type = 'claim_reverified' "
                    "AND session_id = :s",
                    {"s": sid2},
                )
            )[0][0]
        )
        assert audit["anchor_kept"] is True
        assert audit["as_of_conflict"] == "1995-01-01T00:00:00+00:00"


@pytest.mark.asyncio
async def test_reverify_with_counterevidence_still_downgrades_honestly(
    migrated_db: Any,
) -> None:
    """Понижение не запрещено — запрещено понижение БЕЗ причины. Перепроверка с
    опровергающим свидетельством остаётся честной: disputed ≤ E1 с названием
    причины (ADR-0018: противоречие — не ревизия значения)."""
    _url, engine = migrated_db
    _factory, _anchor_sid, claim_id = await _anchor_temporal_claim(engine)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (GAMMA,))

    ops = [
        (
            "claim",
            {
                "statement": STATEMENT,
                "claim_type": "temporal_fact",
                "scope": {},
                "existing_claim_id": str(claim_id),
            },
        ),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "counters"}),
    ]
    factory2, sid2, result = await _apply_reverify(engine, snap, ops, REVERIFY_QUESTION, records)
    assert result.claims_reverified == 1

    async with factory2() as db:
        claim = await db.get(ORMClaim, claim_id)
        assert claim is not None
        # дата не пострадала — пострадало знание, и это видно
        assert claim.as_of is not None
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.epistemic_status is EpistemicStatus.DISPUTED
        assert view.grade is EffectiveGrade.E1
        reasons = (
            await _rows(
                db,
                "SELECT payload->>'reasons' FROM audit_events WHERE type = 'claim_assessed' "
                "AND session_id = :s",
                {"s": sid2},
            )
        )[0][0]
        assert '"counterevidence_unresolved"' in reasons


@pytest.mark.asyncio
async def test_reuse_by_statement_without_a_date_keeps_as_of_and_grade(
    migrated_db: Any,
) -> None:
    """Дедуп-повтор (T7.9) того же утверждения вопросом без даты — та же дыра,
    что и reverify: прежний код обнулял `claims.as_of`. Теперь дата остаётся,
    оценка не падает; перепривязка относительного вопроса закреплена отдельно
    (`test_reused_claim_as_of_rederived_from_current_question`)."""
    _url, engine = migrated_db
    _factory, _anchor_sid, claim_id = await _anchor_temporal_claim(engine)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (GAMMA,))
    ops = [
        ("claim", {"statement": STATEMENT, "claim_type": "temporal_fact", "scope": {}}),
        ("evidence", {"evidence_index": 0, "claim_index": 0, "relation": "supports"}),
    ]
    factory2, _sid2, result = await _apply_reverify(engine, snap, ops, REVERIFY_QUESTION, records)
    assert result.claims_created == 0 and result.claims_reused == 1

    async with factory2() as db:
        claim = await db.get(ORMClaim, claim_id)
        assert claim is not None
        assert claim.as_of is not None, "reuse erased the anchor's reference date"
        view = await MemoryService(snap).claim_view(db, claim_id)
        assert view is not None
        assert view.grade is EffectiveGrade.E3, f"grade lowered: {view.grade}"
        reasons = (
            await _rows(
                db,
                "SELECT payload->>'reasons' FROM audit_events WHERE type = 'claim_assessed' "
                "AND session_id = :s",
                {"s": str(_sid2)},
            )
        )[0][0]
        assert '"as_of_missing"' not in reasons, reasons


@pytest.mark.asyncio
async def test_new_temporal_claim_without_a_date_is_still_as_of_missing(
    migrated_db: Any,
) -> None:
    """Ворота не ослаблены: НОВОМУ временному утверждению без опорной даты хосту
    по-прежнему нечем его оценить → `as_of_missing`, E1 hypothesis (ADR-0016).
    Ослабление касалось только перепроверки существующего (тесты схемы)."""
    _url, engine = migrated_db
    factory, sid = await _seed_session(engine, question_text=REVERIFY_QUESTION)
    snap = await _snapshot(engine)
    records = await _seed_sources(engine, (ALPHA, BETA))
    new_statement = "Индекс потребительских цен вырос на 5,59 процентного пункта"
    await _record_staging(
        engine,
        sid,
        [
            ("claim", {"statement": new_statement, "claim_type": "temporal_fact", "scope": {}}),
            *[
                ("evidence", {"evidence_index": i, "claim_index": 0, "relation": "supports"})
                for i in range(len(records))
            ],
        ],
    )
    memory = MemoryService(snap)
    async with factory() as db, transaction(db):
        session = await db.get(ORMSession, sid)
        assert session is not None
        result = await memory.apply_claim_staging(db, AuditService(db), session, records)
        assert result.claims_created == 1
        claim_id = (
            await _rows(db, "SELECT id FROM claims WHERE statement = :s", {"s": new_statement})
        )[0][0]
        view = await memory.claim_view(db, claim_id)
        reasons = (
            await _rows(
                db,
                "SELECT payload->>'reasons' FROM audit_events WHERE type = 'claim_assessed' "
                "AND session_id = :s",
                {"s": str(sid)},
            )
        )[0][0]
    assert view is not None
    assert view.grade is EffectiveGrade.E1
    assert view.epistemic_status is EpistemicStatus.HYPOTHESIS
    assert '"as_of_missing"' in reasons
