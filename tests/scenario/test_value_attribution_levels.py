"""Scenario (DB): производность не страницы, а конкретного значения (T7.78, ADR-0029).

Стендовый репродюсер (`9266248e`, «инфляция 2025 = 5,59%», E4; `c970bc08`, E3): на странице sbercib.ru
рядом стоят «По\xa0данным Росстата … 5,59%» и «По оценкам Банка России … 10,7%». Страничный детектор
честно отказывается решать про СТРАНИЦУ (`ambiguous_primaries`) — указателя нет — и две новости о
числе Росстата остаются двумя группами независимости, то есть E3 «подтверждено независимо».

Здесь проверяется второй уровень: для утверждения, которое стоит на числе Росстата, эта страница —
пересказ Росстата. Что важно по инвариантам:

- логика группировки `group_source_graph` и правила не менялись: изменились только ВХОДЫ
  загрузчика снимка (`packages/memory/source_graph.py`) — эффективный родитель читается с улики;
- grade производит только rules engine: пересчёт сделан существующим каскадом §11.3
  (`apply_source_graph_change`, тип журнала `source_graph_changed`) и рабочим переоценки;
- ничего не ослаблено: значение, которому на странице первоисточник не присвоен, остаётся
  двухгруппным E3; собственная оценка редакции не приклеивается к первоисточнику вовсе.

Сеть подменена детерминированным FakeFetchClient (§6), всё остальное — настоящий код: proxy,
staging, `MemoryService`, независимость, правила, переатрибуция и CLI.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.reattribution import reattribute_evidence_window
from apps.web.reliability import describe_verification
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.sessions import ORMSession
from packages.domain.schemas.staging import CuratorProposal
from packages.domain.services.audit import AuditService
from packages.domain.services.reserve import HostReserveService
from packages.domain.services.staging import StagingService
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.memory.service import MemoryService
from tests.scenario.test_research_evidence import (
    _all,
    _apply,
    _research_obs,
    _scalar,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

SINCE = "2000-01-01T00:00:00+00:00"

# Живая страница: навигация + атрибуция Росстата (число года) + атрибуция Банка России (январское
# число). Неразрывные пробелы — прямо в литералах (ловушка T7.77).
SBERCIB_LINES = [
    "SberCIB Аналитика",
    "Главная · Аналитика · Макроэкономика · Отчёты · Контакты",
    "",
    "Инфляция в России: итоги года и январские оценки",
    "",
    "По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59% (после 9,52% в\xa02024 году).",
    "В продовольственном сегменте цены выросли на 6,1% за месяц.",
    "По оценкам Банка России, инфляция в\xa0январе замедлилась до 10,7% после 11,2% месяцем ранее.",
    "",
    "Материал подготовлен аналитическим центром SberCIB.",
]
PAGE_SBERCIB = "\n".join(SBERCIB_LINES)
PAGE_VEDOMOSTI = (
    "По\xa0данным Росстата, инфляция в России за 2025 год составила 5,59% — "
    "об этом сообщил опубликованный ведомственный обзор."
)
PAGE_STUDY = (
    "Независимая оценка инфляции 2025 года: по нашей методике показатель составил 6,4% — "
    "в отличие от официальных данных Росстата (5,59%), которые мы пересчитали по своей корзине."
)

URL_SBERCIB = "https://sbercib.example/inflyaciya-2025"
URL_VEDOMOSTI = "https://vedomosti.example/inflyaciya-2025-goda"
URL_STUDY = "https://research.example.org/inflation-estimate"

PAGES: dict[str, str] = {
    URL_SBERCIB: PAGE_SBERCIB,
    URL_VEDOMOSTI: PAGE_VEDOMOSTI,
    URL_STUDY: PAGE_STUDY,
}

STATEMENT_ROSSTAT = "Инфляция в России по итогам 2025 года составила 5,59%."
STATEMENT_OTHER = "Инфляция в России по итогам 2025 года составила 6,4%."


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.75/T7.77)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url].split("\n") if line.strip())
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
    """Настоящий fetch-путь с НАСТОЯЩИМ детектором v2: подменена только сеть."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


async def _seed(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Реальный `research.fetch`: строки источников, артефакты и журнал окна."""

    from apps.research_proxy.service import ResearchProxyService

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        return [dict(await service.fetch(url)) for url in urls]
    finally:
        await engine.dispose()


def _records(envelopes: list[dict[str, Any]]) -> list[Any]:
    records = [
        observation_to_evidence(
            _research_obs(
                env["final_url"],
                str(env["source_id"]),
                str(env["original_sha256"]),
                str(env["normalized_sha256"]),
                normalized_text=PAGES[env["final_url"]],
            ),
            {"url": env["final_url"]},
        )
        for env in envelopes
    ]
    assert all(rec is not None for rec in records)
    return records


async def _origins(scratch_url: str, statement: str) -> list[Any]:
    """Записи о происхождении значения на уликах утверждения (то, что лежит в `evidence.scope`)."""

    return await _all(
        scratch_url,
        "SELECT e.id, e.source_id, s.canonical_uri, "
        "e.scope -> 'value_attribution' AS origin "
        "FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "LEFT JOIN sources s ON s.id = e.source_id WHERE c.statement = " + _quoted(statement)
        + " ORDER BY s.canonical_uri",
    )


async def _group_snapshot(scratch_url: str, statement: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT m.source_id, m.group_id, m.basis FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(statement),
    )


async def _current_assessment(scratch_url: str, statement: str) -> tuple[str, str]:
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, h.epistemic_status FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE c.statement = :s AND h.assessment_state = 'current'",
        {"s": statement},
    )
    assert row is not None, "у утверждения нет текущей оценки"
    return str(row[0]), str(row[1])


async def _worker_reasons(scratch_url: str, statement: str) -> tuple[tuple[str, str], list[str]]:
    row = await _scalar(
        scratch_url,
        "SELECT payload->>'grade', payload->>'epistemic_status', payload->'reasons' "
        "FROM audit_events WHERE type = 'reassessment_job_completed' "
        "AND payload->>'claim_id' = (SELECT id::text FROM claims WHERE statement = :s) "
        "AND jsonb_typeof(payload -> 'reasons') = 'array' "
        "ORDER BY occurred_at DESC LIMIT 1",
        {"s": statement},
    )
    assert row is not None, "рабочий переоценки не оставил записи о переоценке"
    return (str(row[0]), str(row[1])), list(row[2] or [])


async def _run_backfill(
    scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool = False
) -> Any:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_evidence_window(
            factory, store, since=_since(), dry_run=dry_run
        )
    finally:
        await engine.dispose()


def _since() -> Any:
    from datetime import UTC, datetime

    return datetime.fromisoformat(SINCE.replace("Z", "+00:00")).replace(tzinfo=UTC)


# ─── главный случай: неоднозначная страница, но значение утверждения — пересказ ──


@pytest.mark.asyncio
async def test_evidence_level_decision_merges_the_page_with_the_primary(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI))
    by_url = {env["final_url"]: env for env in envelopes}

    # 1. страничный уровень остаётся прежним: неоднозначную страницу он не размечает
    assert by_url[URL_SBERCIB]["attribution_status"] == "ambiguous_primaries", by_url[URL_SBERCIB]
    assert by_url[URL_SBERCIB]["derivative_of"] is None
    assert by_url[URL_VEDOMOSTI]["attribution_status"] == "derivative"
    parents = {
        str(r["canonical_uri"]): r["parent_source_id"]
        for r in await _all(
            scratch_url,
            "SELECT canonical_uri, parent_source_id FROM sources WHERE canonical_uri IN "
            f"({_quoted(URL_SBERCIB)}, {_quoted(URL_VEDOMOSTI)})",
        )
    }
    assert parents[URL_SBERCIB] is None, parents
    assert parents[URL_VEDOMOSTI] is not None, parents

    # 2. состояние стенда до правки: две группы и E3 «подтверждено независимо»
    await _apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT)
    before = await _group_snapshot(scratch_url, STATEMENT_ROSSTAT)
    assert len({str(m["group_id"]) for m in before}) == 2, before
    assert await _current_assessment(scratch_url, STATEMENT_ROSSTAT) == ("E3", "supported")

    # 3. поуровневая переатрибуция окна: решение записано на улике, не на странице
    report = await _run_backfill(scratch_url, store)
    # обе улики этого утверждения стоят на числе Росстата: решение записано на каждой
    assert report.changed_evidence == 2, report.rows
    assert len(report.changed_source_ids) == 2
    origins = await _origins(scratch_url, STATEMENT_ROSSTAT)
    decided = [o for o in origins if o["origin"] is not None]
    assert len(decided) == 2, origins
    record = dict(decided[0]["origin"])
    assert record["schema"] == "host-value-attribution-v1"
    assert record["primary_key"] == "rosstat"
    assert record["primary_name"] == "Росстат"
    # T7.85a/T7.85b: метод значений v4 (строгое pairing осталось; после честного отказа возможно
    # оконное решение с вето «другого источника числа»; здесь решает строгий pairing);
    # схема записи при этом остаётся host-value-attribution-v1
    assert record["method"] == "host-value-attribution-v4"
    assert "По данным Росстата" in record["basis_fragment"]
    # эффективный родитель — тот же якорь первоисточника, что уже создан страничным указателем
    anchor = await _scalar(scratch_url, "SELECT id::text AS id FROM sources WHERE canonical_uri = :u", {"u": "https://rosstat.gov.ru/"})
    assert anchor is not None and str(record["parent_source_id"]) == anchor[0]

    # страница помечена НЕ была: поуровневое решение живёт только на улике
    rows_after = await _all(
        scratch_url,
        "SELECT parent_source_id, metadata ? 'derivative_of' AS marked FROM sources "
        "WHERE canonical_uri = " + _quoted(URL_SBERCIB),
    )
    assert rows_after[0]["parent_source_id"] is None and rows_after[0]["marked"] is False

    # 4. пересчёт — только существующим каскадом и рабочим переоценки
    assert len(await _all(scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'")) == 1
    assert report.affected_claims == 1 and report.invalidated_heads >= 1 and report.jobs_created == 1
    assert report.worker is not None and report.worker.completed == 1

    # 5. rules engine: две группы склеились через общего первоисточника → честная оценка
    members = await _group_snapshot(scratch_url, STATEMENT_ROSSTAT)
    assert len({str(m["group_id"]) for m in members}) == 1, members
    assert all(str(m["basis"]).startswith("parent:") for m in members), members
    assert await _current_assessment(scratch_url, STATEMENT_ROSSTAT) == ("E1", "hypothesis")
    (grade, status), reasons = await _worker_reasons(scratch_url, STATEMENT_ROSSTAT)
    assert (grade, status) == ("E1", "hypothesis"), (grade, status)
    assert "insufficient_independence" in reasons, reasons


# ─── противоположные случаи: ничего не ослаблено и не приклеено лишнего ────


@pytest.mark.asyncio
async def test_value_without_attribution_keeps_two_groups_and_e3(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Другое число: первоисточник назван, но не про это значение → отказа мало, склейки нет."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI))
    await _apply(scratch_url, _records(envelopes), STATEMENT_OTHER)

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0, report.rows
    assert all("изменений нет" in row.action for row in report.rows), report.rows
    assert all(row.now_status == "value_not_attributed" for row in report.rows), report.rows
    assert not await _origins(scratch_url, STATEMENT_OTHER) or all(
        o["origin"] is None for o in await _origins(scratch_url, STATEMENT_OTHER)
    )
    assert (report.affected_claims, report.invalidated_heads, report.jobs_created) == (0, 0, 0)

    members = await _group_snapshot(scratch_url, STATEMENT_OTHER)
    assert len({str(m["group_id"]) for m in members}) == 2, members
    assert await _current_assessment(scratch_url, STATEMENT_OTHER) == ("E3", "supported")


@pytest.mark.asyncio
async def test_own_assessment_page_is_not_glued_to_the_primary(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Своя оценка редакции на своей странице: отдельная группа, даже когда первоисточник назван."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_STUDY))
    by_url = {env["final_url"]: env for env in envelopes}
    assert by_url[URL_STUDY]["attribution_status"] == "own_assessment", by_url[URL_STUDY]

    await _apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT)
    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 1, report.rows  # только улика sbercib
    origins = {str(o["canonical_uri"]): o["origin"] for o in await _origins(scratch_url, STATEMENT_ROSSTAT)}
    assert origins[URL_STUDY] is None, origins
    assert origins[URL_SBERCIB] is not None

    members = await _group_snapshot(scratch_url, STATEMENT_ROSSTAT)
    groups = {str(m["group_id"]) for m in members}
    assert len(groups) == 2, members
    assert await _current_assessment(scratch_url, STATEMENT_ROSSTAT) == ("E3", "supported")


# ─── новый путь (staging улики): решение производит хост, а не переатрибуция ──


def _make_orchestrator(scratch_url: str) -> tuple[Orchestrator, async_sessionmaker[AsyncSession], Any]:
    """Оркестратор без LLM: решение о происхождении значения — хостовое, сеть не нужна."""

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url="http://127.0.0.1:9", model="fake-thinker", max_retries=0)
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=_NoToolsExecutor(),
    )
    return orchestrator, factory, engine


class _NoToolsExecutor:
    """Исполнитель не вызывается: проверяется только хостовое решение об улике."""

    async def execute(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("исполнитель инструментов в этом тесте не вызывается")


async def _apply_with_origins(
    scratch_url: str, records: list[Any], statement: str, origins: dict[int, dict[str, Any] | None]
) -> None:
    """Commit boundary с тем же конвертом `evidence`, который пишет `_curator` (T7.78)."""

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            assert snap is not None
            sid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, started_at) "
                    "VALUES (:id, 'exploring', :c, now())"
                ),
                {"id": str(sid), "c": str(snap.id)},
            )
            o_session = (await db.execute(select(ORMSession).where(ORMSession.id == sid))).scalars().first()
            assert o_session is not None
            audit = AuditService(db)
            staging = StagingService(HostReserveService.for_snapshot(snap))
            service = MemoryService(snap)
            proposal = CuratorProposal(
                summary="Research claim",
                claims=[
                    {
                        "statement": statement,
                        "claim_type": "external_fact",
                        "scope": {"event": "inflation-2025"},
                    }
                ],
                evidence_links=[
                    {"evidence_index": i, "claim_index": 0, "relation": "supports"}
                    for i in range(len(records))
                ],
                new_questions=[],
            )
            assert proposal.validate_against(len(records), questions_max=4) == []
            await staging.record(
                db, audit, o_session, "claim", proposal.claims[0].model_dump(mode="json"), proposed_claims=1
            )
            for index, link in enumerate(proposal.evidence_links):
                payload = link.model_dump(mode="json")
                origin = origins.get(index)
                if origin is not None:
                    payload.update(origin)  # ровно то, что добавляет `_curator`
                await staging.record(db, audit, o_session, "evidence", payload, proposed_evidence=1)
            result = await service.apply_claim_staging(db, audit, o_session, records)
            assert result.problems == (), f"commit boundary problems: {result.problems}"
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_new_evidence_carries_the_decision_into_its_scope(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI))
    records = _records(envelopes)

    orch, factory, engine = _make_orchestrator(scratch_url)
    try:
        origins: dict[int, dict[str, Any] | None] = {}
        async with factory() as db, db.begin():
            for index, record in enumerate(records):
                payload = await orch._value_attribution(db, record, STATEMENT_ROSSTAT)
                origins[index] = payload
    finally:
        await engine.dispose()

    # решение принято на обеих уликах: у vedomosti оно есть и на уровне страницы тоже
    assert all(origin is not None for origin in origins.values()), origins
    assert origins[0]["value_attribution"]["primary_name"] == "Росстат"  # type: ignore[index]

    await _apply_with_origins(scratch_url, records, STATEMENT_ROSSTAT, origins)

    origins_rows = await _origins(scratch_url, STATEMENT_ROSSTAT)
    assert all(o["origin"] is not None for o in origins_rows), origins_rows
    members = await _group_snapshot(scratch_url, STATEMENT_ROSSTAT)
    assert len({str(m["group_id"]) for m in members}) == 1, members
    assert await _current_assessment(scratch_url, STATEMENT_ROSSTAT) == ("E1", "hypothesis")


@pytest.mark.asyncio
async def test_decision_survives_later_rescope_of_the_claim(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """T7.17 перескопировал бы scope поверх решения: носитель решения обязан пережить его."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB,))
    records = _records(envelopes)

    orch, factory, engine = _make_orchestrator(scratch_url)
    try:
        async with factory() as db, db.begin():
            origin = await orch._value_attribution(db, records[0], STATEMENT_ROSSTAT)
    finally:
        await engine.dispose()
    assert origin is not None

    await _apply_with_origins(scratch_url, records, STATEMENT_ROSSTAT, {0: origin})
    first = {str(o["canonical_uri"]): dict(o["origin"]) for o in await _origins(scratch_url, STATEMENT_ROSSTAT)}
    assert first, "решение не записано"

    # второй коммит того же утверждения: хост заново выводит scope каждой улики (T7.17)
    await _apply_with_origins(scratch_url, records, STATEMENT_ROSSTAT, {})
    second = {str(o["canonical_uri"]): o["origin"] for o in await _origins(scratch_url, STATEMENT_ROSSTAT)}
    assert {k: dict(v) for k, v in second.items() if v} == first, second


# ─── карточка «как проверено» видит пересказ и на уровне улики ─────────────


@pytest.mark.asyncio
async def test_verification_phrase_counts_evidence_level_decisions(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_STUDY))
    await _apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT)
    await _run_backfill(scratch_url, store)

    rows = await _all(
        scratch_url,
        "SELECT e.relation, e.evidence_kind, e.scope, s.source_type, s.canonical_uri, "
        "s.parent_source_id, ps.canonical_uri AS parent_uri "
        "FROM evidence e LEFT JOIN sources s ON s.id = e.source_id "
        "LEFT JOIN sources ps ON ps.id = s.parent_source_id "
        "JOIN claims c ON c.id = e.claim_id WHERE c.statement = " + _quoted(STATEMENT_ROSSTAT),
    )
    assert len(rows) == 2, rows
    phrases = describe_verification(rows)
    # страница не помечена — фраза взята из записи на улике, а не из догадки
    assert any("часть прочитанного — пересказ первоисточника: 1" in p for p in phrases), phrases

    without = [{k: v for k, v in dict(r).items() if k != "scope"} for r in rows]
    assert not any("пересказ" in p for p in describe_verification(without)), without


# ─── dry-run и идемпотентность переатрибуции ───────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_plans_without_writing_anything(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI))
    await _apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT)

    report = await _run_backfill(scratch_url, store, dry_run=True)
    assert report.dry_run and report.changed_evidence == 0
    decided = [row for row in report.rows if row.now_status == "derivative"]
    assert len(decided) == 2 and all(row.primary_name == "Росстат" for row in decided), report.rows
    assert all("будет записано" in row.action or "изменений нет" in row.action for row in report.rows), report.rows

    assert all(o["origin"] is None for o in await _origins(scratch_url, STATEMENT_ROSSTAT))
    assert not await _all(scratch_url, "SELECT id FROM reassessment_jobs")
    assert await _current_assessment(scratch_url, STATEMENT_ROSSTAT) == ("E3", "supported")


@pytest.mark.asyncio
async def test_second_backfill_changes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    envelopes = await _seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI))
    await _apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT)

    first = await _run_backfill(scratch_url, store)
    assert first.changed_evidence == 2, first.rows
    head_before = await _scalar(
        scratch_url,
        "SELECT h.current_assessment_id::text FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = :s AND h.assessment_state = 'current'",
        {"s": STATEMENT_ROSSTAT},
    )
    events_before = len(
        await _all(scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'")
    )

    second = await _run_backfill(scratch_url, store)
    assert not second.changed_source_ids and second.changed_evidence == 0
    assert (second.affected_claims, second.invalidated_heads, second.jobs_created) == (0, 0, 0)
    assert second.worker is None
    assert all(
        row.action == "изменений нет: решение уже записано ранее" for row in second.rows
    ), second.rows

    head_after = await _scalar(
        scratch_url,
        "SELECT h.current_assessment_id::text FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = :s AND h.assessment_state = 'current'",
        {"s": STATEMENT_ROSSTAT},
    )
    assert head_after == head_before
    graph_events = await _all(
        scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'"
    )
    assert len(graph_events) == events_before


# ─── CLI: команда с флагом уровня доказательства (паттерн test_cli_ask) ─────


def test_cli_evidence_level_flag_reports_was_and_became(
    migrated_db: tuple[str, Any],
    tmp_path: Path,
    live_fetch: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    envelopes = asyncio.run(_seed(scratch_url, store, (URL_SBERCIB, URL_VEDOMOSTI)))
    asyncio.run(_apply(scratch_url, _records(envelopes), STATEMENT_ROSSTAT))

    from hostctl.cli import main

    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path / "data"))

    runner = CliRunner()
    planned = runner.invoke(
        main,
        ["research-reattribute", "--since", SINCE, "--evidence-level", "--dry-run"],
        catch_exceptions=False,
    )
    assert planned.exit_code == 0, planned.output
    assert "(только план)" in planned.output
    assert "утверждение" in planned.output and URL_SBERCIB in planned.output
    assert "стало: derivative → Росстат" in planned.output
    assert asyncio.run(_scalar(scratch_url, "SELECT count(*) AS n FROM reassessment_jobs"))[0] == 0

    written = runner.invoke(
        main, ["research-reattribute", "--since", SINCE, "--evidence-level"], catch_exceptions=False
    )
    assert written.exit_code == 0, written.output
    assert "доказательств с новым решением: 2" in written.output, written.output
    assert "задач переоценки создано: 1" in written.output, written.output

    origins = asyncio.run(_origins(scratch_url, STATEMENT_ROSSTAT))
    assert sum(1 for o in origins if o["origin"] is not None) == 2, origins

    again = runner.invoke(
        main, ["research-reattribute", "--since", SINCE, "--evidence-level"], catch_exceptions=False
    )
    assert again.exit_code == 0, again.output
    assert "доказательств с новым решением: 0" in again.output, again.output
