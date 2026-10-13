"""Scenario (DB): запись о собственной публикации производителя (T7.101, ADR-0029 дополнение).

Стендовые репродюсеры (фикстура `~/fixtures-t7101`, копии нормализованных артефактов базы .92
в `tests/fixtures/t7101/`):

* `c6729d4c` «Прогноз Банка России по годовой инфляции … 6,0–7,0%» — две страницы самого ЦБ
  (`cbr.ru/…/CPD_2026-7/` и `www.cbr.ru/…/Infl_exp_26-09/`), значение атрибутировано самому
  себе на домашнем хосте (`self_primary`). До правки витрина молчала «Подтверждено слабо»:
  пересказа нет, записи о происхождении значения нет, указателя нет. С правкой переатрибуция
  пишет на каждой улике `value_self_publication` (без родителя — первоисточник не может быть
  родителем самого себя), и витрина показывает «Опубликовано производителем» с именем из
  словаря.
* ловушка T7.87 обязана выдержать: страница ЦБ `CPD_2025-12` с цифрой Росстата 5,59% —
  значение на странице не атрибутировано вовсе (`value_not_attributed`), self_primary не
  возникает, записи нет ни при каких условиях.
* честность ADR-0035 §15: `Infl_exp_25-12` с «Аналитики прогнозируют…» (стендовые 2d010d53 /
  58e2d5d4) — self_primary с вето третьего актора: план печатает отказ, запись не пишется.

Что закреплено: dry-run → apply → повтор dry-run пустой (идемпотентность); группировка,
роли улик, оценки и причины оценки НЕ меняются (снимки до и после побайтово равны; каскада
для такой записи нет — `changed_source_ids` пуст, рабочих не заведено); запись переносится
при пересоздании скоупа улики (T7.17); витрина читает запись как указатель (метка карточки
меняется, уровень/grade/причины — нет). Сеть подменена FakeFetchClient (§6); staging,
`MemoryService`, группировка, правила и переатрибуция — настоящие.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_research_evidence import (
    _all,
    _apply,
    _research_obs,
    _scalar,
    _section,
    _set_section,
)
from tests.scenario.test_web_answer_api import _client
from tests.scenario.test_web_knowledge import _make_app

pytestmark = [pytest.mark.scenario]

SINCE_ISO = "2000-01-01T00:00:00+00:00"
WEAK_LABEL = "Подтверждено слабо"
CURRENT_VALUE_METHOD = "host-value-attribution-v10"
SELF_SCHEMA = "host-value-self-publication-v1"

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t7101"

URL_CPD_2026_7 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/"
URL_INFL_26_09 = "https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/"
URL_INFL_25_12 = "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/"
URL_CPD_2025_12 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/"

FORECAST_URLS = (URL_CPD_2026_7, URL_INFL_26_09)

#: statements стендовых утверждений — дословно из фикстуры (значения в них — часть решения)
STATEMENT_607 = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STATEMENT_63 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)
STATEMENT_559 = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)


def _fixture(sha_prefix: str) -> str:
    files = [p for p in FIXTURE_DIR.glob(f"{sha_prefix}*.txt*") if p.name.startswith(sha_prefix)]
    assert len(files) == 1, (sha_prefix, files)
    path = files[0]
    if path.name.endswith(".json"):
        # страница с CJK-ссылками сохранена JSON-экранированной (diff только ASCII);
        # декод — побайтово исходный нормализованный текст
        data = json.loads(path.read_text(encoding="ascii")).encode("utf-8")
        sha_name = path.name[: -len(".txt.json")]
    else:
        data = path.read_bytes()
        sha_name = path.name[: -len(".txt")]
    assert hashlib.sha256(data).hexdigest() == sha_name, sha_prefix
    return data.decode("utf-8")


PAGES: dict[str, str] = {
    URL_CPD_2026_7: _fixture("65af8c97e7f9"),
    URL_INFL_26_09: _fixture("2bb395fbe861"),
    URL_INFL_25_12: _fixture("7e2eb44100d5"),
    URL_CPD_2025_12: _fixture("218e9a1ea998"),
}


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.75–T7.85)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url].splitlines() if line.strip())
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
    from apps.orchestrator.evidence import observation_to_evidence

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


async def _seed_claim(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...], statement: str
) -> uuid.UUID:
    """fetch + staging одним шагом; возвращает id утверждения."""

    envelopes = await _seed(scratch_url, store, urls)
    await _apply(scratch_url, _records(envelopes), statement)
    claim_id = await _scalar(
        scratch_url,
        "SELECT id::text AS id FROM claims WHERE statement = " + _quoted(statement),
    )
    assert claim_id is not None
    return uuid.UUID(str(claim_id[0]))


def _since() -> Any:
    from datetime import UTC, datetime

    return datetime.fromisoformat(SINCE_ISO.replace("Z", "+00:00")).replace(tzinfo=UTC)


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


async def _recommit(
    scratch_url: str, records: list[Any], statement: str
) -> Any:
    """Тот же staging-коммит повторно (тот же финальный транзакционный путь, что `_apply`,
    но утверждение и улики дедуплицируются: claims_reused, а не claims_created). Нужен для
    проверки переноса записей (T7.17): скоуп улики выводится из провенанса заново."""

    from sqlalchemy import select

    from packages.domain.models.config import ORMConfigSnapshot
    from packages.domain.models.sessions import ORMSession
    from packages.domain.schemas.staging import CuratorProposal
    from packages.domain.services.audit import AuditService
    from packages.domain.services.reserve import HostReserveService
    from packages.domain.services.staging import StagingService
    from packages.memory.service import MemoryService

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, db.begin():
            snap = (
                (await db.execute(select(ORMConfigSnapshot).limit(1))).scalars().first()
            )
            assert snap is not None
            sid = uuid.uuid4()
            await db.execute(
                text(
                    "INSERT INTO sessions (id, state, config_snapshot_id, started_at) "
                    "VALUES (:id, 'exploring', :c, now())"
                ),
                {"id": str(sid), "c": str(snap.id)},
            )
            o_session = (
                (await db.execute(select(ORMSession).where(ORMSession.id == sid)))
                .scalars()
                .first()
            )
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
                        "scope": {"event": "apollo11"},
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
                db, audit, o_session, "claim", proposal.claims[0].model_dump(mode="json"),
                proposed_claims=1,
            )
            for link in proposal.evidence_links:
                await staging.record(
                    db, audit, o_session, "evidence", link.model_dump(mode="json"),
                    proposed_evidence=1,
                )
            result = await service.apply_claim_staging(db, audit, o_session, records)
            assert result.problems == (), f"commit boundary problems: {result.problems}"
            assert result.claims_reused == 1 and result.evidence_deduped == len(records), result
            return result
    finally:
        await engine.dispose()


async def _scopes_of(scratch_url: str, statement: str) -> dict[str, dict[str, Any]]:
    """{canonical_uri: {"self": запись | None, "value": запись | None}} по уликам утверждения."""

    rows = await _all(
        scratch_url,
        "SELECT s.canonical_uri AS uri, e.scope -> 'value_self_publication' AS self_rec, "
        "e.scope -> 'value_attribution' AS value_rec FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(statement),
    )
    return {
        str(r["uri"]): {
            "self": dict(r["self_rec"]) if r["self_rec"] is not None else None,
            "value": dict(r["value_rec"]) if r["value_rec"] is not None else None,
        }
        for r in rows
    }


async def _group_snapshot(scratch_url: str, statement: str) -> list[tuple[str, str, str]]:
    """Снимок группировки: (basis, group_id, canonical_uri) по членам групп действующей оценки."""

    return sorted(
        (
            str(r["basis"]),
            str(r["group_id"]),
            str(r["uri"]),
        )
        for r in await _all(
            scratch_url,
            "SELECT m.basis AS basis, m.group_id AS group_id, s.canonical_uri AS uri "
            "FROM source_independence_members m "
            "JOIN sources s ON s.id = m.source_id "
            "WHERE m.snapshot_id = ("
            "  SELECT a.source_independence_snapshot_id FROM claim_assessments a "
            "  JOIN claim_assessment_heads h ON h.current_assessment_id = a.id "
            "  JOIN claims c ON c.id = a.claim_id "
            "  WHERE c.statement = " + _quoted(statement) + " AND h.assessment_state = 'current'"
            ")",
        )
    )


async def _assessment_snapshot(scratch_url: str, statement: str) -> tuple[Any, ...]:
    """Действующая оценка: grade/epistemic/confidence + причины из события оценки
    (те же два типа ленты, что читает витрина: `apps/web/knowledge.py`)."""

    rows = await _all(
        scratch_url,
        "SELECT a.effective_grade AS g, a.epistemic_status AS e, a.confidence AS f, "
        "a.id::text AS aid FROM claim_assessments a "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id "
        "JOIN claims c ON c.id = a.claim_id WHERE c.statement = " + _quoted(statement) +
        " AND h.assessment_state = 'current'",
    )
    assert len(rows) == 1, rows
    reasons = await _all(
        scratch_url,
        "SELECT r.reason FROM (SELECT payload FROM audit_events "
        "WHERE type IN ('claim_assessed', 'reassessment_job_completed') "
        "AND payload->>'assessment_id' = '" + str(rows[0]["aid"]) + "' "
        "ORDER BY occurred_at DESC, sequence DESC LIMIT 1) l "
        "LEFT JOIN LATERAL jsonb_array_elements_text("
        "COALESCE(l.payload->'reasons', '[]'::jsonb)) WITH ORDINALITY AS r(reason, n) ON TRUE "
        "ORDER BY r.n",
    )
    return (
        str(rows[0]["g"]),
        str(rows[0]["e"]),
        float(str(rows[0]["f"])),
        tuple(str(r["reason"]) for r in reasons),
    )


async def _sources_snapshot(scratch_url: str) -> list[tuple[str, str | None, bool]]:
    return sorted(
        (str(r["uri"]), str(r["parent"]) if r["parent"] is not None else None, bool(r["marked"]))
        for r in await _all(
            scratch_url,
            "SELECT canonical_uri AS uri, parent_source_id AS parent, "
            "metadata ? 'derivative_of' AS marked FROM sources",
        )
    )


async def _label(scratch_url: str, claim_id: uuid.UUID) -> dict[str, Any]:
    """Метка витрины (карточка утверждения из `/knowledge`) — надёжность действующей оценки."""

    app, engine = await _make_app(scratch_url)
    try:
        async with _client(app) as client:
            r = await client.get(f"/api/v1/knowledge/claims/{claim_id}")
            assert r.status_code == 200
            return dict(r.json()["reliability"])
    finally:
        await engine.dispose()


# ─── 1: dry-run → apply → повтор; группировка и оценки не тронуты ──────────────────


@pytest.mark.asyncio
async def test_own_forecast_pages_get_self_publication_record(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, FORECAST_URLS, STATEMENT_607)
    groups_before = await _group_snapshot(scratch_url, STATEMENT_607)
    assessment_before = await _assessment_snapshot(scratch_url, STATEMENT_607)
    sources_before = await _sources_snapshot(scratch_url)
    assert {g[0] for g in groups_before} == {"domain:cbr.ru"}, groups_before  # одна группа
    assert await _scopes_of(scratch_url, STATEMENT_607) == {
        URL_CPD_2026_7: {"self": None, "value": None},
        URL_INFL_26_09: {"self": None, "value": None},
    }, "до переатрибуции записей нет (оркестратор self_primary не пишет)"

    # dry-run: оператор видит, ЧТО будет записано
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    rows = {row.canonical_uri: row for row in plan.rows}
    assert set(rows) == set(FORECAST_URLS), plan.rows
    for uri in FORECAST_URLS:
        assert rows[uri].was_status == "записи о происхождении значения нет", rows[uri]
        assert rows[uri].now_status == "self_primary", rows[uri]
        assert rows[uri].action == (
            "будет записано: собственная публикация производителя Банк России"
        ), rows[uri]
        assert rows[uri].primary_name == "Банк России", rows[uri]
    assert plan.dry_run and plan.changed_evidence == 0 and not plan.changed_source_ids
    assert await _scopes_of(scratch_url, STATEMENT_607) == {
        URL_CPD_2026_7: {"self": None, "value": None},
        URL_INFL_26_09: {"self": None, "value": None},
    }, "dry-run ничего не пишет"

    # apply
    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows
    # каскада нет: страница не приписана другому первоисточнику, группировка не менялась
    assert not report.changed_source_ids and report.jobs_created == 0 and report.worker is None, report
    scopes = await _scopes_of(scratch_url, STATEMENT_607)
    for uri in FORECAST_URLS:
        record = scopes[uri]["self"]
        assert record is not None, scopes
        assert record["schema"] == SELF_SCHEMA, record
        assert record["primary_key"] == "cbr" and record["primary_name"] == "Банк России", record
        # адрес записи — адрес прочитанной страницы (витрина проверит его о домашнем хосте)
        assert record["primary_uri"] == uri, record
        assert record["method"] == CURRENT_VALUE_METHOD, record
        assert "По прогнозу Банка России" in record["basis_fragment"], record
        # родителя нет по построению: первоисточник не может быть родителем самого себя
        assert "parent_source_id" not in record, record
        # записи о производной атрибуции при этом нет
        assert scopes[uri]["value"] is None, scopes

    # группировка, оценки, роли и строки источников — побайтово те же
    assert await _group_snapshot(scratch_url, STATEMENT_607) == groups_before
    assert await _assessment_snapshot(scratch_url, STATEMENT_607) == assessment_before
    assert await _sources_snapshot(scratch_url) == sources_before

    # повтор идемпотентен: улика уже решена текущей записью — «изменений нет»
    again = await _run_backfill(scratch_url, store, dry_run=True)
    again_rows = {row.canonical_uri: row for row in again.rows}
    assert set(again_rows) == set(FORECAST_URLS), again.rows
    for uri in FORECAST_URLS:
        assert again_rows[uri].was_status == (
            f"собственная публикация → Банк России ({CURRENT_VALUE_METHOD})"
        ), again_rows[uri]
        assert again_rows[uri].action == "изменений нет: собственная публикация уже записана", again_rows[uri]
    second = await _run_backfill(scratch_url, store)
    assert second.changed_evidence == 0 and not second.changed_source_ids, second.rows


# ─── 2: ловушка T7.87 — страница ЦБ с цифрой Росстата не получает записи ────────────


@pytest.mark.asyncio
async def test_t787_trap_page_gets_no_record(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, (URL_CPD_2025_12,), STATEMENT_559)

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    row = next(r for r in plan.rows if r.canonical_uri == URL_CPD_2025_12)
    assert row.now_status == "value_not_attributed", row
    assert row.action == "изменений нет: атрибуция значения не собрана", row

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0, report.rows
    scopes = await _scopes_of(scratch_url, STATEMENT_559)
    assert scopes[URL_CPD_2025_12] == {"self": None, "value": None}, scopes


# ─── 3: вето третьего актора (ADR-0035 §15) — план честен, записи нет ───────────────


@pytest.mark.asyncio
async def test_third_actor_veto_keeps_record_unwritten(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, (URL_INFL_25_12,), STATEMENT_63)

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    row = next(r for r in plan.rows if r.canonical_uri == URL_INFL_25_12)
    assert row.now_status == "self_primary", row  # детектор честен: страница сама себе первоисточник
    assert row.action == (
        "изменений нет: собственная публикация не записана — "
        "значение названо оценкой третьего актора (ADR-0035 §15)"
    ), row

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0, report.rows
    assert (await _scopes_of(scratch_url, STATEMENT_63))[URL_INFL_25_12] == {"self": None, "value": None}


# ─── 4: витрина — метка меняется, оценка нет ────────────────────────────────────────


@pytest.mark.asyncio
async def test_showcase_label_flips_after_record(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    claim_id = await _seed_claim(scratch_url, store, FORECAST_URLS, STATEMENT_607)

    before = await _label(scratch_url, claim_id)
    assert before["label"] == WEAK_LABEL, before

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows

    after = await _label(scratch_url, claim_id)
    assert after["label"] == PRODUCER_LABEL, after
    assert "Банк России" in after["hint"], after
    # витрина не назначает: уровень, grade и причины — те же
    assert after["level"] == LEVEL_WEAK and after["level"] == before["level"], after
    assessment = await _assessment_snapshot(scratch_url, STATEMENT_607)
    assert assessment[0] == "E1" and assessment[1] == "hypothesis", assessment
    assert assessment[3] == ("insufficient_independence",), assessment


# ─── 5: запись переносится при пересоздании скоупа улики (T7.17) ────────────────────


@pytest.mark.asyncio
async def test_record_survives_evidence_scope_rebuild(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, FORECAST_URLS, STATEMENT_607)
    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows
    written = await _scopes_of(scratch_url, STATEMENT_607)
    assert all(rec["self"] is not None for rec in written.values()), written

    # тот же staging-коммит заново: скоуп улики выводится из провенанса заново —
    # без переноса (carry_value_attribution) запись молча исчезла бы (ловушка T7.17)
    envelopes = await _seed(scratch_url, store, FORECAST_URLS)
    await _recommit(scratch_url, _records(envelopes), STATEMENT_607)

    after = await _scopes_of(scratch_url, STATEMENT_607)
    for uri in FORECAST_URLS:
        assert after[uri]["self"] == written[uri]["self"], (uri, after[uri])
