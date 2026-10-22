"""Scenario (DB): блокировка переатрибуции по коррекции оператора — ПО УТВЕРЖДЕНИЮ (T7.130 часть 2).

Стенд `363be67-v29` (ДЕФЕКТ 1): утверждение 9c43a83b «Рост реальных заработных плат … за
январь-июль 2026 года … 6,0% (по данным Росстата)» получило честное «Проверено» (E3), хотя
страница finmarket.ru/news/6717673 прямо говорит «…сообщил в среду Росстат». Записи о
происхождении значения на этой улике нет, потому что переатрибуция напечатала «под коррекцией
оператора (ADR-0031) — улики не переписываем». Коррекция оператора (`ec30f06f`: sberbank →
finmarket) относится к ДРУГОМУ утверждению — 738548ca («…в июле 2026 года … 3,3%»), — но замок
T7.114 был наложен на ВСЕГ sources и на все утверждения сразу.

Что закреплено прогоном переатрибуции (`reattribute_evidence_window`, сеть — FakeFetchClient):

- (а) источник под коррекцией оператора ПЕРЕАТРИБУТУЕТСЯ для чужого утверждения (9c43a83b →
  Росстат pairing `statement_names_producer`) и НЕ переписывается для оспоренного (738548ca —
  та же строка плана «под коррекцией оператора (ADR-0031)», записи нет);
- (б) отмена спора (`cancel_dispute`, `valid = false`) возвращает прежнее поведение: оспоренное
  утверждение тоже переатрибутируется;
- коррекция без читаемой ссылки на утверждение (в журнале нет `payload.claim_id`) остаётся
  закрытой для ВСЕХ утверждений — fail-closed, поведение T7.114 сохранено;
- указатель страницы (`sources.parent_source_id`) по-прежнему заморожен для источника целиком:
  правка касается только улик, планировщик страниц не тронут.

Связь «коррекция → утверждение» берётся без миграции тем же путём, что её записывает
`packages/memory/dispute.py`: `source_graph_corrections.reason_audit_event_id` →
`audit_events.payload ->> 'claim_id'` (событие `OPERATOR_COMMAND_RECEIVED`). Спор заводится
настоящим сервисом, отмена — настоящим сервисом. Сеть подменена (§6); staging, `MemoryService`,
группировка и правила — настоящие; прямых UPDATE доменных таблиц нет.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.db.uow import transaction
from packages.domain.services.audit import AuditService
from packages.memory.dispute import ACTOR_HOSTCTL, cancel_dispute, dispute_claim
from tests.scenario.test_research_evidence import (
    _all,
    _apply,
    _research_obs,
    _scalar,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

SINCE_ISO = "2000-01-01T00:00:00+00:00"
CURRENT_VALUE_METHOD = "host-value-attribution-v18"
VALUE_SCHEMA = "host-value-attribution-v1"
PAIRING_STATEMENT = "statement_names_producer"

LOCKED = "под коррекцией оператора (ADR-0031) — улики не переписываем"
WILL_WRITE = "будет записано происхождение значения улики"

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t7130"

#: та же страница стенда, что в фикстуре T7.130 (нормализованный снимок менеджера)
URL_FIN = "https://www.finmarket.ru/news/6717673"
_FIN_PAGE = (FIXTURE_DIR / "page-finmarket-ru-news-6717673.normalized.txt").read_text()
FIN_LINES = [line for line in _FIN_PAGE.split("\n") if line.strip()]
#: страница-пересказ (вместо sberbank термометра — тот же смысл: июльские 3,3% без первоисточника
#: в окне улики); домен нарочно другой, ловушка T7.75
URL_SBER = "https://termometr.example/news/2026/realnye-zarplaty-iyul"
SBER_LINES = [
    "Реальные зарплаты в июле 2026 года выросли на 3,3% год к году.",
    "Номинальные начисления прибавили 14,5% год к году.",
    "Накопленным итогом за январь-июль реальные зарплаты прибавили 6,0%.",
]

#: формулировки стендовых утверждений 738548ca и 9c43a83b (дословно)
STATEMENT_JUL = (
    "Рост реальных заработных плат в России в июле 2026 года составил 3,3% "
    "в годовом выражении (по данным Росстата)."
)
STATEMENT_JAN_JUL = (
    "Рост реальных заработных плат в России за январь-июль 2026 года составил 6,0% "
    "в годовом выражении (по данным Росстата)."
)

REASON = "Страница термометра пересказывает сюжет делового издания, а не самостоятельная публикация"


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.107)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        lines = {URL_FIN: FIN_LINES, URL_SBER: SBER_LINES}[url]
        paragraphs = "".join(f"<p>{line}</p>" for line in lines if line.strip())
        data = f"<html><body>{paragraphs}</body></html>".encode()
        return FetchResult(
            url=url,
            final_url=url,
            content_type="text/html",
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


@pytest.fixture()
def live_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Настоящий fetch-путь с НАСТОЯЩИМ детектором: подменена только сеть."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _seed(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...]
) -> list[dict[str, Any]]:
    from apps.research_proxy.service import ResearchProxyService

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        return [dict(await service.fetch(url)) for url in urls]
    finally:
        await engine.dispose()


def _records(envelopes: list[dict[str, Any]], pages: dict[str, list[str]]) -> list[Any]:
    from apps.orchestrator.evidence import observation_to_evidence

    records = [
        observation_to_evidence(
            _research_obs(
                env["final_url"],
                str(env["source_id"]),
                str(env["original_sha256"]),
                str(env["normalized_sha256"]),
                normalized_text="\n".join(pages[env["final_url"]]),
            ),
            {"url": env["final_url"]},
        )
        for env in envelopes
    ]
    assert all(rec is not None for rec in records)
    return records


async def _seed_claim(
    scratch_url: str,
    store: FilesystemArtifactStore,
    urls: tuple[str, ...],
    statement: str,
    pages: dict[str, list[str]],
) -> None:
    envelopes = await _seed(scratch_url, store, urls)
    await _apply(scratch_url, _records(envelopes, pages), statement)


def _since() -> Any:
    return datetime.fromisoformat(SINCE_ISO.replace("Z", "+0:00")).replace(tzinfo=UTC)


async def _run_backfill(
    scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool = False
) -> Any:
    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_evidence_window(factory, store, since=_since(), dry_run=dry_run)
    finally:
        await engine.dispose()


async def _claim_id(scratch_url: str, statement: str) -> uuid.UUID:
    row = await _scalar(
        scratch_url, "SELECT id::text AS id FROM claims WHERE statement = " + _quoted(statement)
    )
    assert row is not None, "утверждение не записано"
    return uuid.UUID(str(row[0]))


async def _record_of(scratch_url: str, statement: str, canonical_uri: str) -> dict[str, Any] | None:
    row = await _scalar(
        scratch_url,
        "SELECT e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN claims c ON c.id = e.claim_id JOIN sources s ON s.id = e.source_id "
        "WHERE c.statement = " + _quoted(statement) + " AND s.canonical_uri = " + _quoted(canonical_uri),
    )
    assert row is not None, f"улики {canonical_uri} нет у утверждения"
    return None if row[0] is None else dict(row[0])


async def _dispute(scratch_url: str, claim_id: uuid.UUID) -> Any:
    """Настоящий операторский спор (ADR-0031): коррекция merge + ссылка на событие журнала."""
    engine = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, transaction(db):
            return await dispute_claim(
                db,
                AuditService(db),
                claim_id=claim_id,
                primary_uri=URL_FIN,
                retelling_uri=URL_SBER,
                reason=REASON,
                actor=ACTOR_HOSTCTL,
            )
    finally:
        await engine.dispose()


async def _cancel(scratch_url: str, claim_id: uuid.UUID) -> Any:
    engine = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, transaction(db):
            return await cancel_dispute(
                db, AuditService(db), claim_id=claim_id, actor=ACTOR_HOSTCTL
            )
    finally:
        await engine.dispose()


async def _corrections(scratch_url: str) -> list[dict[str, Any]]:
    return await _all(
        scratch_url,
        "SELECT id::text AS id, actor, valid, from_source_id::text AS src, to_source_id::text AS dst, "
        "reason_audit_event_id::text AS reason_id FROM source_graph_corrections "
        "WHERE actor LIKE 'operator:%'",
    )


async def _linked_claim_of_correction(scratch_url: str, correction_id: str) -> str:
    """Как именно связь «коррекция → утверждение» видна планировщику (без миграции)."""
    row = await _scalar(
        scratch_url,
        "SELECT ae.payload ->> 'claim_id' FROM source_graph_corrections c "
        "JOIN audit_events ae ON ae.id = c.reason_audit_event_id WHERE c.id = " + _quoted(correction_id),
    )
    return str(row[0]) if row and row[0] is not None else ""


async def _plant_bare_correction(scratch_url: str, *, uri: str) -> None:
    """Коррекция оператора БЕЗ ссылки на утверждение: актор оператора, `reason_audit_event_id` нет.

    Так выглядят коррекции, заведённые до T7.81/T7.114 или рукой мигратора: планировщик обязан
    держать их как прежний замок на весь источник (fail-closed).
    """
    engine = create_async_engine(scratch_url)
    from_uri = f"https://mirror.example/disputed-{uuid.uuid4().hex[:8]}"
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata) "
                    "VALUES (:from_id, 'external_url', :from_uri, :ch, '{}')"
                ),
                {
                    "from_id": str(uuid.uuid4()),
                    "from_uri": from_uri,
                    "ch": f"t7130-{uuid.uuid4().hex[:12]}",
                },
            )
            await conn.execute(
                text(
                    "INSERT INTO source_graph_corrections (id, actor, kind, from_source_id, "
                    "to_source_id, rules_version, valid) VALUES (:id, 'operator:hostctl', 'merge', "
                    "(SELECT id FROM sources WHERE canonical_uri = :from_uri), "
                    "(SELECT id FROM sources WHERE canonical_uri = :uri), 'rules-v2', true)"
                ),
                {"id": str(uuid.uuid4()), "from_uri": from_uri, "uri": uri},
            )
    finally:
        await engine.dispose()


# ─── (а) замок по утверждению: чужое утверждение переатрибутируется ───────────────────────


@pytest.mark.asyncio
async def test_correction_locks_only_the_disputed_claim(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    pages = {URL_FIN: FIN_LINES, URL_SBER: SBER_LINES}

    # оспоренное утверждение стенда: две улики (пересказ + деловое издание)
    await _seed_claim(scratch_url, store, (URL_SBER, URL_FIN), STATEMENT_JUL, pages)
    # утверждение, которого спор не касается: та же страница finmarket
    await _seed_claim(scratch_url, store, (URL_FIN,), STATEMENT_JAN_JUL, pages)

    assert await _record_of(scratch_url, STATEMENT_JUL, URL_FIN) is None
    assert await _record_of(scratch_url, STATEMENT_JAN_JUL, URL_FIN) is None

    outcome = await _dispute(scratch_url, await _claim_id(scratch_url, STATEMENT_JUL))
    assert outcome.invalidated >= 1, outcome
    corrections = await _corrections(scratch_url)
    assert corrections, "коррекция оператора не заведена"
    # связь, на которую опирается планировщик: событие журнала с `claim_id` оспоренного утверждения
    disputed = str(await _claim_id(scratch_url, STATEMENT_JUL))
    for row in corrections:
        assert await _linked_claim_of_correction(scratch_url, row["id"]) == disputed, row

    # ── план: чужое утверждение будет записано, оспоренное — под замком ──
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    jan_jul = next(r for r in plan.rows if r.claim_statement == STATEMENT_JAN_JUL and r.canonical_uri == URL_FIN)
    jul = next(r for r in plan.rows if r.claim_statement == STATEMENT_JUL and r.canonical_uri == URL_FIN)
    assert jan_jul.action == WILL_WRITE, jan_jul
    assert jan_jul.now_status.startswith("derivative"), jan_jul
    assert jan_jul.primary_name == "Росстат", jan_jul
    assert jul.action == LOCKED, jul

    # ── прогон: запись появляется только у чужого утверждения ──
    run = await _run_backfill(scratch_url, store)
    assert run.changed_evidence >= 1, run.rows
    record = await _record_of(scratch_url, STATEMENT_JAN_JUL, URL_FIN)
    assert record is not None, "чужое утверждение не переатрибутировано из-за чужого спора"
    assert record["schema"] == VALUE_SCHEMA, record
    assert record["primary_key"] == "rosstat", record
    assert record["pairing"] == PAIRING_STATEMENT, record
    assert record["method"] == CURRENT_VALUE_METHOD, record
    # оспоренное утверждение не тронуто (ни записи, ни снятия)
    assert await _record_of(scratch_url, STATEMENT_JUL, URL_FIN) is None
    # повтор идемпотентен: замок чужого спора не превращается в вечный пересмотр
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0, again.rows


# ─── (б) отмена спора возвращает прежнее поведение ────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancelled_dispute_unlocks_the_disputed_claim(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    pages = {URL_FIN: FIN_LINES, URL_SBER: SBER_LINES}

    await _seed_claim(scratch_url, store, (URL_SBER, URL_FIN), STATEMENT_JUL, pages)
    await _seed_claim(scratch_url, store, (URL_FIN,), STATEMENT_JAN_JUL, pages)
    claim_jul = await _claim_id(scratch_url, STATEMENT_JUL)
    await _dispute(scratch_url, claim_jul)

    first = await _run_backfill(scratch_url, store)
    assert first.changed_evidence >= 1, first.rows
    assert await _record_of(scratch_url, STATEMENT_JUL, URL_FIN) is None

    cancelled = await _cancel(scratch_url, claim_jul)
    assert len(cancelled.correction_ids) >= 1, cancelled
    assert all(row["valid"] is False for row in await _corrections(scratch_url)), await _corrections(
        scratch_url
    )

    # прежнее поведение до спора: переатрибутируется и оспоренное утверждение
    after = await _run_backfill(scratch_url, store)
    assert after.changed_evidence >= 1, after.rows
    record = await _record_of(scratch_url, STATEMENT_JUL, URL_FIN)
    assert record is not None, "после отмены спора замок не снят"
    assert record["primary_key"] == "rosstat", record
    assert record["method"] == CURRENT_VALUE_METHOD, record
    # запись чужого утверждения не перезаписана повторно
    assert (await _run_backfill(scratch_url, store)).changed_evidence == 0


# ─── fail-closed: коррекция без ссылки на утверждение закрывает источник целиком ───────────


@pytest.mark.asyncio
async def test_correction_without_claim_link_stays_locked_for_every_claim(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    pages = {URL_FIN: FIN_LINES, URL_SBER: SBER_LINES}

    await _seed_claim(scratch_url, store, (URL_SBER, URL_FIN), STATEMENT_JUL, pages)
    await _seed_claim(scratch_url, store, (URL_FIN,), STATEMENT_JAN_JUL, pages)
    await _plant_bare_correction(scratch_url, uri=URL_FIN)

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    locked = [r for r in plan.rows if r.canonical_uri == URL_FIN and r.action == LOCKED]
    assert {r.claim_statement for r in locked} == {STATEMENT_JUL, STATEMENT_JAN_JUL}, plan.rows
    run = await _run_backfill(scratch_url, store)
    assert await _record_of(scratch_url, STATEMENT_JUL, URL_FIN) is None
    assert await _record_of(scratch_url, STATEMENT_JAN_JUL, URL_FIN) is None
    assert all(r.action != WILL_WRITE for r in run.rows if r.canonical_uri == URL_FIN), run.rows
    # указатель страницы под коррекцией не появляется (страничный путь не тронут)
    pointer = await _scalar(
        scratch_url,
        "SELECT parent_source_id::text FROM sources WHERE canonical_uri = " + _quoted(URL_FIN),
    )
    assert pointer[0] is None, pointer
