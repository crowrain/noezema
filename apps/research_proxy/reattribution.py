"""Переатрибуция решений битого детектора v1 (T7.77).

Команда `hostctl research-reattribute` перечитывает журнал fetch'ей окна деплоя T7.75:
для каждого события `research_fetch_completed`, решённого методом `host-source-attribution-v1`
(детектор не видел неразрывных пробелов, мягких переносов и переводов строк внутри фраз),
нормализованный текст поднимается из хранилища артефактов по `normalized_sha256` и решение
принимается заново детектором v2. Указатель проставляется только строкам, ещё не размеченным,
тех же функций, что и при fetch (`apps/research_proxy/derivative_pointer.py`) — идемпотентно.

Дальше работает существующий механизм пересчёта оценок: `apply_source_graph_change` (§11.3,
существующий тип журнала `SOURCE_GRAPH_CHANGED`) снимает с `current` оценки утверждений, чьи
улики задели изменённые источники, а рабочий переоценки (`run_reassessment_batch`, тот же, что
у `hostctl reassessment-tick`) пересобирает снимки независимости и переоценивает правилами.
Новых типов событий, миграций и правок движка правил здесь нет по построению.

Решения пишутся в той же транзакции, что и каскад (инвариант §3 «Audit + outbox»). Прямые
UPDATE идут только в provenance-поля источников (`parent_source_id`, `metadata.derivative_of`)
— это происхождение прочитанного, а не знание: оценки меняет исключительно rules engine.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.research_proxy.derivative_pointer import resolve_primary_source, write_derivative_pointer
from apps.research_proxy.source_attribution import AttributionDecision, detect_source_attribution, primary_source
from apps.research_proxy.value_attribution import (
    VALUE_ATTRIBUTION_METHOD_VERSION,
    ValueAttributionDecision,
    attribute_value_in_fragment,
)
from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.domain.canonical import canonical_json_bytes
from packages.domain.db.uow import transaction
from packages.domain.services.audit import AuditService
from packages.memory.reassessment import WorkerOutcome, run_reassessment_batch
from packages.memory.scope import EVIDENCE_VALUE_ATTRIBUTION_KEY, build_value_attribution
from packages.memory.source_graph import apply_source_graph_change

#: метод, которым помечены решения битого детектора; переатрибуция смотрит только такие события
BROKEN_METHOD_VERSION: Final = "host-source-attribution-v1"
#: автор записей журнала, порождённых каскадом переатрибуции (тип события существующий)
REATTRIBUTE_ACTOR: Final = "hostctl research-reattribute"
#: метка в metadata.derivative_of: решение принято отложенным прогоном, не чтением
WRITTEN_BY_REATTRIBUTE: Final = "research-reattribute"


class ReattributionError(RuntimeError):
    """Понятная отказ-ошибка команды (некорректный аргумент, повреждённая запись журнала)."""


@dataclass(frozen=True)
class ReattributeRow:
    """Строка отчёта команды: один кандидат из окна журнала."""

    canonical_uri: str
    was_status: str
    now_status: str
    primary_name: str | None
    action: str


@dataclass(frozen=True)
class ReattributionReport:
    since: str
    dry_run: bool
    rows: tuple[ReattributeRow, ...]
    changed_source_ids: frozenset[uuid.UUID]
    affected_claims: int
    invalidated_heads: int
    jobs_created: int
    worker: WorkerOutcome | None


def parse_since(raw: str) -> datetime:
    """ISO-дата начала окна (`--since`). Принимает и «Z» в конце — как печатает журнал."""
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReattributionError(f"--since: не ISO-дата ({raw!r}): {exc}") from exc
    if parsed.tzinfo is None:
        raise ReattributionError("--since: нужен таймзона (например 2026-10-07T14:30:00+00:00)")
    return parsed


async def _candidates(db: AsyncSession, since: datetime) -> list[dict[str, Any]]:
    """Последнее на сегодня решение v1 по каждой прочитанной странице окна."""
    return [
        dict(row)
        for row in (
            (
                await db.execute(
                    text(
                        "SELECT DISTINCT ON (payload->>'source_id') "
                        "  payload->>'source_id' AS source_id, "
                        "  payload->>'normalized_sha256' AS normalized_sha256, "
                        "  payload->>'attribution_status' AS was_status "
                        "FROM audit_events "
                        "WHERE type = 'research_fetch_completed' "
                        "  AND payload->>'attribution_method' = :method "
                        "  AND occurred_at >= :since "
                        "ORDER BY payload->>'source_id', occurred_at DESC, sequence DESC"
                    ),
                    {"method": BROKEN_METHOD_VERSION, "since": since},
                )
            )
            .mappings()
            .all()
        )
    ]


async def _current_state(db: AsyncSession, source_id: uuid.UUID) -> dict[str, Any] | None:
    row = (
        (
            await db.execute(
                text(
                    "SELECT canonical_uri, parent_source_id IS NOT NULL AS has_parent, "
                    "  metadata ? 'derivative_of' AS marked FROM sources WHERE id = :id"
                ),
                {"id": source_id},
            )
        )
        .mappings()
        .first()
    )
    return None if row is None else dict(row)


@dataclass(frozen=True)
class _Plan:
    source_id: uuid.UUID
    decision: AttributionDecision
    row_index: int


async def _plan_window(
    db: AsyncSession, store: ArtifactStore, since: datetime
) -> tuple[list[ReattributeRow], list[_Plan]]:
    rows: list[ReattributeRow] = []
    plan: list[_Plan] = []
    for candidate in await _candidates(db, since):
        raw_id = str(candidate["source_id"])
        try:
            source_id = uuid.UUID(raw_id)
        except ValueError:
            rows.append(
                ReattributeRow(
                    canonical_uri="(нет uri)",
                    was_status=str(candidate["was_status"]),
                    now_status="—",
                    primary_name=None,
                    action="битая запись журнала: source_id не uuid — пропущено",
                )
            )
            continue

        state = await _current_state(db, source_id)
        if state is None:
            rows.append(
                ReattributeRow(
                    canonical_uri="(источник утрачен)",
                    was_status=str(candidate["was_status"]),
                    now_status="—",
                    primary_name=None,
                    action="строка источника не найдена — пропущено",
                )
            )
            continue

        normalized_sha = candidate["normalized_sha256"]
        if not normalized_sha:
            rows.append(
                ReattributeRow(
                    canonical_uri=str(state["canonical_uri"]),
                    was_status=str(candidate["was_status"]),
                    now_status="—",
                    primary_name=None,
                    action="нет нормализованного текста — перечитать нечего",
                )
            )
            continue
        try:
            page_text = store.get(str(normalized_sha)).decode("utf-8")
        except (ArtifactStoreError, OSError, UnicodeDecodeError):
            rows.append(
                ReattributeRow(
                    canonical_uri=str(state["canonical_uri"]),
                    was_status=str(candidate["was_status"]),
                    now_status="—",
                    primary_name=None,
                    action="артефакт недоступен — перечитать нечего",
                )
            )
            continue

        decision = detect_source_attribution(
            canonical_uri=str(state["canonical_uri"]), text=page_text
        )
        if state["has_parent"] or state["marked"]:
            rows.append(
                ReattributeRow(
                    canonical_uri=str(state["canonical_uri"]),
                    was_status=str(candidate["was_status"]),
                    now_status=decision.status,
                    primary_name=decision.primary_name,
                    action="указатель уже проставлен ранее — изменений нет",
                )
            )
            continue
        if not decision.is_derivative:
            rows.append(
                ReattributeRow(
                    canonical_uri=str(state["canonical_uri"]),
                    was_status=str(candidate["was_status"]),
                    now_status=decision.status,
                    primary_name=decision.primary_name,
                    action="изменений нет",
                )
            )
            continue
        rows.append(
            ReattributeRow(
                canonical_uri=str(state["canonical_uri"]),
                was_status=str(candidate["was_status"]),
                now_status=decision.status,
                primary_name=decision.primary_name,
                action="будет проставлен указатель",
            )
        )
        plan.append(
            _Plan(source_id=source_id, decision=decision, row_index=len(rows) - 1)
        )
    return rows, plan


async def reattribute_window(
    factory: async_sessionmaker[AsyncSession],
    store: ArtifactStore,
    *,
    since: datetime,
    dry_run: bool = False,
) -> ReattributionReport:
    """Прогон переатрибуции окна. `dry_run` только читает и печатает план."""
    async with factory() as db:
        rows, plan = await _plan_window(db, store, since)

    if dry_run:
        return ReattributionReport(
            since=since.isoformat(),
            dry_run=True,
            rows=tuple(rows),
            changed_source_ids=frozenset(),
            affected_claims=0,
            invalidated_heads=0,
            jobs_created=0,
            worker=None,
        )

    if not plan:
        return ReattributionReport(
            since=since.isoformat(),
            dry_run=False,
            rows=tuple(rows),
            changed_source_ids=frozenset(),
            affected_claims=0,
            invalidated_heads=0,
            jobs_created=0,
            worker=None,
        )

    changed: set[uuid.UUID] = set()
    # указатели и каскад — одной транзакцией (инвариант audit+outbox §3)
    async with factory() as db, transaction(db):
        for item in plan:
            guarded = (
                (
                    await db.execute(
                        text(
                            "SELECT id FROM sources WHERE id = :id "
                            "  AND parent_source_id IS NULL "
                            "  AND NOT (metadata ? 'derivative_of') "
                            "FOR UPDATE"
                        ),
                        {"id": item.source_id},
                    )
                )
                .scalars()
                .first()
            )
            if guarded is None:
                continue  # разметка появилась параллельно — не трогаем
            parent_id = await write_derivative_pointer(
                db, str(item.source_id), item.decision, written_by=WRITTEN_BY_REATTRIBUTE
            )
            if parent_id is not None:
                changed.add(item.source_id)
        cascade = await apply_source_graph_change(
            db, AuditService(db), source_ids=frozenset(changed), actor=REATTRIBUTE_ACTOR
        )

    worker: WorkerOutcome | None = None
    if cascade.jobs_created:
        async with factory() as db:
            worker = await run_reassessment_batch(db)

    actions = {
        item.row_index: (
            "указатель проставлен"
            if item.source_id in changed
            else "разметка появилась параллельно — пропущено"
        )
        for item in plan
    }
    rows_out = tuple(
        ReattributeRow(
            canonical_uri=row.canonical_uri,
            was_status=row.was_status,
            now_status=row.now_status,
            primary_name=row.primary_name,
            action=actions.get(index, row.action),
        )
        for index, row in enumerate(rows)
    )
    return ReattributionReport(
        since=since.isoformat(),
        dry_run=False,
        rows=rows_out,
        changed_source_ids=frozenset(changed),
        affected_claims=cascade.affected_claims,
        invalidated_heads=cascade.invalidated,
        jobs_created=cascade.jobs_created,
        worker=worker,
    )


# ── T7.78 (ADR-0029): поуровневая атрибуция значения по уже существующим доказательствам ──
#
# Страничная переатрибуция выше решает «кто первоисточник этой СТРАНИЦЫ» и отказывается решать,
# когда на странице два первоисточника. Здесь решается другой вопрос — «чей пересказ читает это
# ДОКАЗАТЕЛЬСТВО»: фрагмент `По\xa0данным Росстата … 5,59%` остаётся пересказом Росстата и на
# неоднозначной странице. Строки источников при этом не размечаются: поуровневое решение живёт на
# улике (`evidence.scope.value_attribution`, `packages/memory/scope.py`), а пересчёт делает тот же
# существующий каскад §11.3 и тот же рабочий переоценки.
#
# Фрагмент доказательства долговременно не хранится (в строке улики есть только identity_hash по
# хешу исходного содержимого), поэтому основанием служит сохранённый нормализованный текст
# страницы: детектор значения выбирает из него атрибуцию того числа, на котором стоит утверждение.


@dataclass(frozen=True)
class EvidenceAttributionRow:
    """Строка отчёта команды: одна улика (утверждение + источник) окна."""

    claim_id: str
    claim_statement: str
    canonical_uri: str
    was_status: str
    now_status: str
    primary_name: str | None
    action: str


@dataclass(frozen=True)
class EvidenceReattributionReport:
    since: str
    dry_run: bool
    rows: tuple[EvidenceAttributionRow, ...]
    changed_evidence: int
    changed_source_ids: frozenset[uuid.UUID]
    affected_claims: int
    invalidated_heads: int
    jobs_created: int
    worker: WorkerOutcome | None


#: «было» для строки отчёта: у улики ещё нет решения о происхождении значения
NO_DECISION_YET: Final = "записи о происхождении значения нет"


@dataclass(frozen=True)
class _EvidencePlan:
    evidence_id: uuid.UUID
    source_id: uuid.UUID
    claim_id: uuid.UUID
    decision: ValueAttributionDecision
    row_index: int


async def _read_pages(db: AsyncSession, since: datetime) -> dict[uuid.UUID, str]:
    """Сохранённый нормализованный текст каждой страницы окна журнала (последнее решение по
    каждой прочитанной строке источника; метод страничного детектора не важен — он мог отказаться
    решать, а значение всё равно имеет первоисточник)."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT ON (payload->>'source_id') "
                    "  payload->>'source_id' AS source_id, "
                    "  payload->>'normalized_sha256' AS normalized_sha256 "
                    "FROM audit_events "
                    "WHERE type = 'research_fetch_completed' "
                    "  AND occurred_at >= :since "
                    "  AND jsonb_typeof(payload -> 'source_id') = 'string' "
                    "  AND jsonb_typeof(payload -> 'normalized_sha256') = 'string' "
                    "ORDER BY payload->>'source_id', occurred_at DESC, sequence DESC"
                ),
                {"since": since},
            )
        )
        .mappings()
        .all()
    )
    pages: dict[uuid.UUID, str] = {}
    for row in rows:
        try:
            source_id = uuid.UUID(str(row["source_id"]))
        except ValueError:
            continue
        sha = str(row["normalized_sha256"])
        if not sha:
            continue
        pages[source_id] = sha
    return pages


async def _open_pages(store: ArtifactStore, shas: dict[uuid.UUID, str]) -> tuple[dict[uuid.UUID, str], set[uuid.UUID]]:
    """Тексты доступных артефактов + источники, текста которых нет (отказ честный, не молчаливый)."""
    texts: dict[uuid.UUID, str] = {}
    missing: set[uuid.UUID] = set()
    for source_id, sha in shas.items():
        try:
            texts[source_id] = store.get(sha).decode("utf-8")
        except (ArtifactStoreError, OSError, UnicodeDecodeError):
            missing.add(source_id)
    return texts, missing


async def _undecided_evidence(db: AsyncSession, source_id: uuid.UUID) -> list[dict[str, Any]]:
    """Улики этого источника без решения о происхождении значения (детерминированный порядок)."""
    return [
        dict(row)
        for row in (
            (
                await db.execute(
                    text(
                        "SELECT e.id AS evidence_id, e.claim_id, c.statement, s.canonical_uri "
                        "FROM evidence e "
                        "JOIN claims c ON c.id = e.claim_id "
                        "JOIN sources s ON s.id = e.source_id "
                        "WHERE e.source_id = :sid AND e.evidence_kind = 'source_assertion' "
                        "  AND jsonb_typeof(e.scope -> 'value_attribution') IS NULL "
                        "ORDER BY e.claim_id, e.id"
                    ),
                    {"sid": source_id},
                )
            )
            .mappings()
            .all()
        )
    ]


async def _decided_evidence(db: AsyncSession, source_id: uuid.UUID) -> list[dict[str, Any]]:
    """Улики этого источника, где решение уже есть: переатрибуция идемпотентна, и оператор обязан
    видеть, что пропущено и почему (молчание читалось бы как «нечего проверять»)."""
    return [
        dict(row)
        for row in (
            (
                await db.execute(
                    text(
                        "SELECT e.id AS evidence_id, e.claim_id, c.statement, s.canonical_uri, "
                        "  e.scope -> 'value_attribution' ->> 'primary_name' AS primary_name "
                        "FROM evidence e "
                        "JOIN claims c ON c.id = e.claim_id "
                        "JOIN sources s ON s.id = e.source_id "
                        "WHERE e.source_id = :sid AND e.evidence_kind = 'source_assertion' "
                        "  AND jsonb_typeof(e.scope -> 'value_attribution') = 'object' "
                        "ORDER BY e.claim_id, e.id"
                    ),
                    {"sid": source_id},
                )
            )
            .mappings()
            .all()
        )
    ]


async def _claim_count_for(db: AsyncSession, source_ids: frozenset[uuid.UUID]) -> int:
    """Сколько утверждений зацепит каскад (тот же селектор, что у `apply_source_graph_change`)."""
    if not source_ids:
        return 0
    return int(
        (
            await db.execute(
                text(
                    "SELECT COUNT(DISTINCT claim_id) FROM evidence WHERE source_id = ANY(:ids)"
                ),
                {"ids": list(source_ids)},
            )
        ).scalar_one()
    )


async def _plan_evidence_window(
    db: AsyncSession, store: ArtifactStore, since: datetime
) -> tuple[list[EvidenceAttributionRow], list[_EvidencePlan]]:
    rows: list[EvidenceAttributionRow] = []
    plan: list[_EvidencePlan] = []
    shas = await _read_pages(db, since)
    texts, missing = await _open_pages(store, shas)

    for source_id in sorted(texts):
        page_text = texts[source_id]
        for item in await _decided_evidence(db, source_id):
            name = str(item["primary_name"] or "") or None
            rows.append(
                EvidenceAttributionRow(
                    claim_id=str(item["claim_id"]),
                    claim_statement=str(item["statement"] or ""),
                    canonical_uri=str(item["canonical_uri"] or ""),
                    was_status="решение уже записано",
                    now_status=f"derivative{f' → {name}' if name else ''}",
                    primary_name=name,
                    action="изменений нет: решение уже записано ранее",
                )
            )
        undecided = await _undecided_evidence(db, source_id)
        if not undecided:
            continue
        canonical_uri = str(undecided[0]["canonical_uri"] or "")
        for item in undecided:
            decision = attribute_value_in_fragment(
                canonical_uri=canonical_uri or None,
                text=page_text,
                claim_statement=str(item["statement"] or ""),
            )
            if not decision.is_derivative:
                rows.append(
                    EvidenceAttributionRow(
                        claim_id=str(item["claim_id"]),
                        claim_statement=str(item["statement"] or ""),
                        canonical_uri=canonical_uri,
                        was_status=NO_DECISION_YET,
                        now_status=decision.status,
                        primary_name=None,
                        action="изменений нет: атрибуция значения не собрана",
                    )
                )
                continue
            rows.append(
                EvidenceAttributionRow(
                    claim_id=str(item["claim_id"]),
                    claim_statement=str(item["statement"] or ""),
                    canonical_uri=canonical_uri,
                    was_status=NO_DECISION_YET,
                    now_status=decision.status,
                    primary_name=decision.primary_name,
                    action="будет записано происхождение значения улики",
                )
            )
            plan.append(
                _EvidencePlan(
                    evidence_id=uuid.UUID(str(item["evidence_id"])),
                    source_id=source_id,
                    claim_id=uuid.UUID(str(item["claim_id"])),
                    decision=decision,
                    row_index=len(rows) - 1,
                )
            )
    for source_id in sorted(missing):
        rows.append(
            EvidenceAttributionRow(
                claim_id="—",
                claim_statement="(артефакт недоступен)",
                canonical_uri=str(source_id),
                was_status=NO_DECISION_YET,
                now_status="—",
                primary_name=None,
                action="нормализованный текст недоступен — перечитать нечего",
            )
        )
    return rows, plan


async def reattribute_evidence_window(
    factory: async_sessionmaker[AsyncSession],
    store: ArtifactStore,
    *,
    since: datetime,
    dry_run: bool = False,
) -> EvidenceReattributionReport:
    """Прогон поуровневой атрибуции окна. `dry_run` только читает и печатает план."""
    async with factory() as db:
        rows, plan = await _plan_evidence_window(db, store, since)
        preview_claims = await _claim_count_for(
            db, frozenset({item.source_id for item in plan})
        )

    if dry_run or not plan:
        return EvidenceReattributionReport(
            since=since.isoformat(),
            dry_run=dry_run,
            rows=tuple(rows),
            changed_evidence=0,
            changed_source_ids=frozenset(),
            affected_claims=preview_claims,
            invalidated_heads=0,
            jobs_created=0,
            worker=None,
        )

    changed_evidence = 0
    changed_sources: set[uuid.UUID] = set()
    # записи и каскад — одной транзакцией (инвариант audit+outbox §3)
    async with factory() as db, transaction(db):
        for item in plan:
            spec = primary_source(item.decision.primary_key or "")
            if spec is None:
                continue
            guarded = (
                (
                    await db.execute(
                        text(
                            "SELECT id FROM evidence WHERE id = :id AND source_id = :sid "
                            "  AND jsonb_typeof(scope -> 'value_attribution') IS NULL "
                            "FOR UPDATE"
                        ),
                        {"id": item.evidence_id, "sid": item.source_id},
                    )
                )
                .scalars()
                .first()
            )
            if guarded is None:
                continue  # решение появилось параллельно — не трогаем (идемпотентность)
            parent_id = await resolve_primary_source(
                db, spec, declared_by=VALUE_ATTRIBUTION_METHOD_VERSION
            )
            record = build_value_attribution(
                primary_key=spec.key,
                primary_name=spec.name,
                primary_uri=spec.home_uri,
                parent_source_id=parent_id,
                method=VALUE_ATTRIBUTION_METHOD_VERSION,
                basis_fragment=item.decision.basis_fragment,
            )
            if record is None or parent_id == str(item.source_id):
                continue
            await db.execute(
                text(
                    "UPDATE evidence SET scope = COALESCE(scope, '{}'::jsonb) || CAST(:m AS jsonb) "
                    "WHERE id = :id AND jsonb_typeof(scope -> 'value_attribution') IS NULL"
                ),
                {
                    "m": canonical_json_bytes(
                        {EVIDENCE_VALUE_ATTRIBUTION_KEY: record.as_scope()}
                    ).decode("utf-8"),
                    "id": item.evidence_id,
                },
            )
            changed_evidence += 1
            changed_sources.add(item.source_id)
        cascade = await apply_source_graph_change(
            db, AuditService(db), source_ids=frozenset(changed_sources), actor=REATTRIBUTE_ACTOR
        )

    worker: WorkerOutcome | None = None
    if cascade.jobs_created:
        async with factory() as db:
            worker = await run_reassessment_batch(db)

    written = {item.row_index for item in plan}
    rows_out = tuple(
        EvidenceAttributionRow(
            claim_id=row.claim_id,
            claim_statement=row.claim_statement,
            canonical_uri=row.canonical_uri,
            was_status=row.was_status,
            now_status=row.now_status,
            primary_name=row.primary_name,
            action=(
                "происхождение значения улики записано"
                if index in written
                else row.action
            ),
        )
        for index, row in enumerate(rows)
    )
    return EvidenceReattributionReport(
        since=since.isoformat(),
        dry_run=False,
        rows=rows_out,
        changed_evidence=changed_evidence,
        changed_source_ids=frozenset(changed_sources),
        affected_claims=cascade.affected_claims,
        invalidated_heads=cascade.invalidated,
        jobs_created=cascade.jobs_created,
        worker=worker,
    )
