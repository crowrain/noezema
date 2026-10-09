"""Scenario (DB): переатрибуция решений битого детектора v1 и честный пересчёт (T7.77).

Репродюсер стендового пропуска: страница набрана неразрывными пробелами («По\\xa0данным
Росстата»), детектор v1 с литеральными шаблонами фразу не увидел — источник остался без
указателя, утверждение получило E3 «подтверждено независимо» по двум группам. Команда
`hostctl research-reattribute` перечитывает журнал окна, принимает решение заново детектором
v2 по сохранённому нормализованному тексту и проставляет указатель **тем же кодом**, что и при
fetch; дальше работают только существующие механизмы: каскад `apply_source_graph_change`
(снимает голову оценки) и рабочий переоценки (rules engine понижает E3/2 группы до честной
оценки при одной группе).

Сценарий намеренно моделирует состояние эпохи v1: fetch исполняется настоящим путём, но
детектор подменён stub'ом «не увидел атрибуцию», а журнал помечен методом v1 — ровно так,
как на стенде после деплоя T7.75. Никаких прямых UPDATE доменных таблиц в тесте нет.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.reattribution import reattribute_window
from apps.research_proxy.source_attribution import (
    STATUS_NO_VALUE_ATTRIBUTION,
    AttributionDecision,
)
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_research_evidence import (
    _all,
    _apply,
    _research_obs,
    _scalar,
    _section,
    _set_section,
)

pytestmark = [pytest.mark.scenario]

SINCE = datetime(2000, 1, 1, tzinfo=UTC)

# тексты с живой типографикой: NBSP внутри фразы — как на sbercib.ru (стенд T7.77)
RETAIL_NBSP_A = (
    "По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59% "
    "(после 9,52% в\xa02024 году)."
)
RETAIL_NBSP_B = (
    "По\xa0данным Росстата годовая инфляция замедлилась до 5,6%\xa0— об этом сообщил "
    "ведомственный обзор."
)

URL_SBERCIB = "https://sbercib.example/inflyaciya-2025"
URL_VEDOMOSTI = "https://vedomosti.example/inflyaciya-2025-goda"

PAGES: dict[str, str] = {URL_SBERCIB: RETAIL_NBSP_A, URL_VEDOMOSTI: RETAIL_NBSP_B}
RETAIL_URLS = (URL_SBERCIB, URL_VEDOMOSTI)

STATEMENT = "Инфляция в России по итогам 2025 года составила около 5,6%."


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в T7.75-репродюсере)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        body = PAGES[url]
        data = f"<html><body><p>{body}</p></body></html>".encode()
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
def broken_v1_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch эпохи v1: сеть подменена, детектор «не видит» NBSP, журнал помечен v1."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    monkeypatch.setattr(svc, "ATTRIBUTION_METHOD_VERSION", "host-source-attribution-v1")
    monkeypatch.setattr(
        svc,
        "detect_source_attribution",
        lambda *, canonical_uri, text: AttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION),
    )


async def _seed_v1_era(scratch_url: str, store: FilesystemArtifactStore) -> list[dict[str, Any]]:
    """Настоящий fetch-путь с stub-детектором + staging: утверждение с E3 по двум группам."""

    from apps.research_proxy.service import ResearchProxyService

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        envelopes = [dict(await service.fetch(url)) for url in RETAIL_URLS]
    finally:
        await engine.dispose()

    assert all(env["attribution_status"] == STATUS_NO_VALUE_ATTRIBUTION for env in envelopes), envelopes
    audit = await _all(
        scratch_url,
        "SELECT payload->>'attribution_method' AS method FROM audit_events "
        "WHERE type = 'research_fetch_completed' ORDER BY sequence",
    )
    assert [r["method"] for r in audit] == ["host-source-attribution-v1"] * 2

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
    await _apply(scratch_url, records, STATEMENT)
    return envelopes


def _quoted(statement: str) -> str:
    return "'" + statement.replace("'", "''") + "'"


async def _current_assessment(scratch_url: str) -> tuple[str, str]:
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, h.epistemic_status FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "WHERE c.statement = :s AND h.assessment_state = 'current'",
        {"s": STATEMENT},
    )
    assert row is not None, "у утверждения нет текущей оценки"
    return str(row[0]), str(row[1])


async def _current_head_id(scratch_url: str) -> str:
    row = await _scalar(
        scratch_url,
        "SELECT h.current_assessment_id::text FROM claim_assessment_heads h "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = :s AND h.assessment_state = 'current'",
        {"s": STATEMENT},
    )
    assert row is not None
    return str(row[0])


async def _group_snapshot(scratch_url: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT m.source_id, m.group_id, m.basis FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(STATEMENT),
    )


async def _run(factory: Any, store: FilesystemArtifactStore, *, dry_run: bool = False) -> Any:
    return await reattribute_window(factory, store, since=SINCE, dry_run=dry_run)


# ─── главный случай: указатель + каскад + честная оценка ──────────────────


@pytest.mark.asyncio
async def test_reattribute_marks_parents_and_regrades_honestly(
    migrated_db: tuple[str, Any], tmp_path: Path, broken_v1_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v1_era(scratch_url, store)

    # состояние эпохи v1: два пересказа без указателя — две группы, E3 «подтверждено»
    before = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id FROM sources WHERE canonical_uri IN "
        f"({_quoted(URL_SBERCIB)}, {_quoted(URL_VEDOMOSTI)}) ORDER BY canonical_uri",
    )
    assert [r["parent_source_id"] for r in before] == [None, None]
    members_before = await _group_snapshot(scratch_url)
    assert len({str(m["group_id"]) for m in members_before}) == 2, members_before
    assert await _current_assessment(scratch_url) == ("E3", "supported")

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        report = await _run(factory, store)
    finally:
        await engine.dispose()

    # 1. указатель проставлен тем же кодом, что и при fetch: общий якорь Росстата
    assert report.changed_source_ids and len(report.changed_source_ids) == 2
    anchors = await _all(
        scratch_url,
        "SELECT id, canonical_uri FROM sources WHERE metadata->>'declared_primary_anchor' = 'true'",
    )
    assert len(anchors) == 1 and anchors[0]["canonical_uri"] == "https://rosstat.gov.ru/"
    after = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id, metadata->'derivative_of' AS d FROM sources "
        "WHERE canonical_uri IN (" + _quoted(URL_SBERCIB) + ", " + _quoted(URL_VEDOMOSTI) + ") "
        "ORDER BY canonical_uri",
    )
    assert len(after) == 2
    for row in after:
        assert str(row["parent_source_id"]) == str(anchors[0]["id"])
        record = dict(row["d"])
        # решение принято отложенным прогоном текущим страничным детектором — видно из provenance
        # (T7.85: метод v3; окно пересмотра с этой же правки — «любой метод, кроме текущего»)
        assert record["method"] == "host-source-attribution-v3"
        assert record["written_by"] == "research-reattribute"
        assert "Росстат" in record["basis_fragment"]

    # 2. таблица команды: было (из журнала v1) → стало (решение v2)
    by_uri = {row.canonical_uri: row for row in report.rows}
    assert set(by_uri) == {URL_SBERCIB, URL_VEDOMOSTI}
    for uri, row in by_uri.items():
        assert (row.was_status, row.now_status) == ("no_value_attribution", "derivative"), uri
        assert row.primary_name == "Росстат"
        assert row.action == "указатель проставлен"

    # 3. пересчёт — только существующим каскадом §11.3 и рабочим переоценки
    graph_events = await _all(
        scratch_url, "SELECT payload FROM audit_events WHERE type = 'source_graph_changed'"
    )
    assert len(graph_events) == 1
    assert report.affected_claims == 1 and report.invalidated_heads >= 1 and report.jobs_created == 1
    assert report.worker is not None and report.worker.completed == 1

    # 4. rules engine честно понизил оценку: две группы склеились в одну через parent
    members = await _group_snapshot(scratch_url)
    assert len(members) == 2, members
    assert len({str(m["group_id"]) for m in members}) == 1, members
    assert all(str(m["basis"]).startswith("parent:") for m in members), members
    assert await _current_assessment(scratch_url) == ("E1", "hypothesis")
    # рабочим переоценки произведён rules engine и записан в его же событии журнала
    recheck = await _scalar(
        scratch_url,
        "SELECT payload->'reasons', payload->>'grade', payload->>'epistemic_status' "
        "FROM audit_events WHERE type = 'reassessment_job_completed' "
        "AND payload->>'claim_id' = (SELECT id::text FROM claims WHERE statement = :s) "
        "ORDER BY occurred_at DESC LIMIT 1",
        {"s": STATEMENT},
    )
    assert recheck is not None, "рабочий не оставил записи о переоценке"
    reasons = list(recheck[0] or [])
    assert (recheck[1], recheck[2]) == ("E1", "hypothesis"), recheck
    assert "insufficient_independence" in reasons, reasons


# ─── идемпотентность: повторный запуск ничего не меняет ───────────────────


@pytest.mark.asyncio
async def test_second_run_changes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, broken_v1_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v1_era(scratch_url, store)

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        first = await _run(factory, store)
        head_after_first = await _current_head_id(scratch_url)
        events_after_first = len(
            await _all(scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'")
        )

        second = await _run(factory, store)
    finally:
        await engine.dispose()

    assert first.changed_source_ids and not second.changed_source_ids
    assert all(row.action == "указатель уже проставлен ранее — изменений нет" for row in second.rows), second.rows
    assert (second.affected_claims, second.invalidated_heads, second.jobs_created) == (0, 0, 0)
    assert second.worker is None

    # ни новой разметки, ни нового каскада, ни новой оценки
    assert await _current_head_id(scratch_url) == head_after_first
    events_after_second = len(
        await _all(scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'")
    )
    assert events_after_second == events_after_first
    marked = await _all(
        scratch_url, "SELECT id FROM sources WHERE metadata ? 'derivative_of'"
    )
    assert len(marked) == 2


# ─── dry-run: план без записи ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(
    migrated_db: tuple[str, Any], tmp_path: Path, broken_v1_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v1_era(scratch_url, store)

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        report = await _run(factory, store, dry_run=True)
    finally:
        await engine.dispose()

    assert not report.changed_source_ids and report.dry_run
    assert all(row.action == "будет проставлен указатель" for row in report.rows), report.rows
    parents = await _all(
        scratch_url,
        "SELECT parent_source_id FROM sources WHERE canonical_uri IN "
        f"({_quoted(URL_SBERCIB)}, {_quoted(URL_VEDOMOSTI)})",
    )
    assert [r["parent_source_id"] for r in parents] == [None, None]
    assert not await _all(scratch_url, "SELECT id FROM reassessment_jobs")
    assert await _current_assessment(scratch_url) == ("E3", "supported")


# ─── CLI: команда поднимает свою БД и печатает таблицу (паттерн test_cli_ask) ──


def test_cli_research_reattribute_wires_and_reports(
    migrated_db: tuple[str, Any],
    tmp_path: Path,
    broken_v1_fetch: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    scratch_url, _engine = migrated_db
    # тот же путь артефактов, что вычислит команда по NOEZEMA_DATA_ROOT (ловушка §7 про env)
    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    asyncio.run(_seed_v1_era(scratch_url, store))

    from hostctl.cli import main

    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path / "data"))

    runner = CliRunner()
    result = runner.invoke(
        main, ["research-reattribute", "--since", "2000-01-01T00:00:00+00:00"], catch_exceptions=False
    )
    assert result.exit_code == 0, result.output
    # таблица: canonical URI → было (журнал v1) → стало (решение v2)
    assert URL_SBERCIB in result.output and URL_VEDOMOSTI in result.output
    assert "было: no_value_attribution" in result.output
    assert "стало: derivative → Росстат" in result.output
    assert "указатель проставлен" in result.output

    parents = asyncio.run(
        _all(
            scratch_url,
            "SELECT parent_source_id FROM sources WHERE canonical_uri IN "
            f"({_quoted(URL_SBERCIB)}, {_quoted(URL_VEDOMOSTI)})",
        )
    )
    assert all(r["parent_source_id"] is not None for r in parents), parents

    bad = runner.invoke(main, ["research-reattribute", "--since", "не дата"])
    assert bad.exit_code == 2
    assert "--since" in bad.output
