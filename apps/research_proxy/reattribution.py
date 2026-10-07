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

from apps.research_proxy.derivative_pointer import write_derivative_pointer
from apps.research_proxy.source_attribution import AttributionDecision, detect_source_attribution
from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.domain.db.uow import transaction
from packages.domain.services.audit import AuditService
from packages.memory.reassessment import WorkerOutcome, run_reassessment_batch
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
