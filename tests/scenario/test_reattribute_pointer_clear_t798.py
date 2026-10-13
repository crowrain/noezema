"""Scenario (DB): T7.98 — снятие страничного указателя, противоречащего свежему методу.

Репродюсер состояния стенда .92 (сессия ca2484b2, контроль c3d5cd03): страница-агрегатор
(internationalinvestment.biz) размечена методом v3 как производная Росстата — на странице есть
шаблон «По данным Росстата». Утверждение «Банк России повысил прогноз инфляции на 2026 год до
6–7%» читается с этой страницы и ещё с sbercib.ru: две группы → E3 «подтверждено независимо»,
причём группа Росстата — ложная (прогноз ЦБ на чужой странице приписан Росстату).

Метод v4 видит на той же странице ДВА первоисточника (шаблон Росстата + relay-прогноз ЦБ) →
`ambiguous_primaries`. Команда `hostctl research-reattribute` снимает хостовый указатель
(ADR-0029, узкая реализация «варианта C» из AGENTS §7 T7.75): только `derivative_of`, только
положительное противоречие, источники под коррекцией оператора не трогает. Затем
`--evidence-level` записывает происхождение значения: обе улики — пересказ ЦБ → одна группа →
E1 `insufficient_independence` вместо нечестного E3.

Состояние эпохи v3 моделируется настоящим путём: fetch со stub-детектором (журнал помечен v3),
staging без записей о происхождении значения — ровно как на стенде, где детектор значений v5
честно отказался решать на всех фрагментах.
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
from apps.research_proxy.reattribution import (
    reattribute_evidence_window,
    reattribute_window,
)
from apps.research_proxy.source_attribution import (
    STATUS_DERIVATIVE,
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

# тексты — синтаксис стендовых страниц (фикстуры T7.98), абзац = одна строка
INTL_TEXT = [
    "По данным Росстата, инфляция в январе 2026 года составила 0,17%.",
    "Банк России также повысил оценку инфляции: вместо прежнего прогноза 5,1–5,6% "
    "ожидается рост цен на уровне 6–7% в 2026 году.",
    "Альфа-Банк повысил прогноз инфляции по итогам 2026 года до 7,5%.",
]
SBERCIB_TEXT = [
    "По данным Росстата, инфляция в январе 2026 года составила 0,17%.",
    "24 июля Банк России повысил прогноз инфляции на 2026 год до 6–7%. "
    "Ранее регулятор ожидал рост цен на 4,5–5,5%.",
]
GUARD_TEXT = ["По данным Росстата, годовая инфляция 2025 года составила 5,59%."]

URL_INTL = "https://internationalinvestment.example/8583-alfa-bank.html"
URL_SBERCIB = "https://sbercib.example/inflyatsiya-2026"
URL_GUARD = "https://nbj.example/inflyaciya-2026"

PAGES: dict[str, str] = {URL_INTL: INTL_TEXT[0] + " " + INTL_TEXT[1] + " " + INTL_TEXT[2],
                          URL_SBERCIB: SBERCIB_TEXT[0] + " " + SBERCIB_TEXT[1],
                          URL_GUARD: GUARD_TEXT[0]}

STATEMENT_CBR = "Банк России повысил прогноз инфляции на 2026 год до 6–7%."
STATEMENT_ALFA = "Альфа-Банк повысил прогноз инфляции по итогам 2026 года до 7,5%."
STATEMENT_559 = "Годовая инфляция в России по итогам 2025 года составила 5,59%."


class FakeFetchClient:
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
def v3_era_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch эпохи v3: intl и nbj «производны Росстата», sbercib без решения, журнал — v3."""

    import apps.research_proxy.service as svc

    def _stub(*, canonical_uri: str, text: str) -> AttributionDecision:
        if canonical_uri in (URL_INTL, URL_GUARD):
            return AttributionDecision(
                status=STATUS_DERIVATIVE,
                primary_key="rosstat",
                primary_name="Росстат",
                parent_uri="https://rosstat.gov.ru/",
                basis_fragment="По данным Росстата, инфляция в январе 2026 года составила 0,17%.",
            )
        return AttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION)

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    monkeypatch.setattr(svc, "ATTRIBUTION_METHOD_VERSION", "host-source-attribution-v3")
    monkeypatch.setattr(svc, "detect_source_attribution", _stub)


def _quoted(statement: str) -> str:
    return "'" + statement.replace("'", "''") + "'"


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
    assert row is not None, f"у утверждения нет текущей оценки: {statement}"
    return str(row[0]), str(row[1])


async def _source_row(scratch_url: str, uri: str) -> dict[str, Any]:
    row = await _scalar(
        scratch_url,
        "SELECT id::text AS id, parent_source_id::text AS parent, "
        "  metadata ? 'derivative_of' AS marked FROM sources WHERE canonical_uri = :u",
        {"u": uri},
    )
    assert row is not None, uri
    return {"id": str(row[0]), "parent": None if row[1] is None else str(row[1]), "marked": bool(row[2])}


async def _anchor_id(scratch_url: str, uri: str) -> str:
    row = await _scalar(
        scratch_url,
        "SELECT id::text FROM sources WHERE canonical_uri = :u "
        "  AND metadata->>'declared_primary_anchor' = 'true'",
        {"u": uri},
    )
    assert row is not None, f"якорь {uri} не создан"
    return str(row[0])


async def _seed_v3_era(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, dict[str, str]]:
    """Настоящий fetch-путь со stub-детектором v3 + staging трёх утверждений без записей значений."""

    from apps.research_proxy.service import ResearchProxyService

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        envelopes = {
            url: dict(await service.fetch(url))
            for url in (URL_INTL, URL_SBERCIB, URL_GUARD)
        }
    finally:
        await engine.dispose()

    audit = await _all(
        scratch_url,
        "SELECT payload->>'attribution_method' AS method FROM audit_events "
        "WHERE type = 'research_fetch_completed' ORDER BY sequence",
    )
    assert [r["method"] for r in audit] == ["host-source-attribution-v3"] * 3, audit

    from apps.orchestrator.evidence import observation_to_evidence

    def _records(urls: tuple[str, ...]) -> list[Any]:
        return [
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
            for url, env in ((u, envelopes[u]) for u in urls)
        ]

    await _apply(scratch_url, _records((URL_INTL, URL_SBERCIB)), STATEMENT_CBR)
    await _apply(scratch_url, _records((URL_INTL,)), STATEMENT_ALFA)
    await _apply(scratch_url, _records((URL_GUARD,)), STATEMENT_559)
    return envelopes


def _factory(scratch_url: str) -> tuple[async_sessionmaker[Any], Any]:
    engine = create_async_engine(scratch_url)
    return async_sessionmaker(engine, expire_on_commit=False), engine


# ─── главный случай: снятие указателя + улики → одна группа ЦБ → E1 ────────


@pytest.mark.asyncio
async def test_pointer_clear_then_value_records_merge_groups_to_e1(
    migrated_db: tuple[str, Any], tmp_path: Path, v3_era_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v3_era(scratch_url, store)

    rosstat_anchor = await _anchor_id(scratch_url, "https://rosstat.gov.ru/")

    # состояние стенда: intl и nbj — «производные Росстата», sbercib без указателя;
    # CBR-прогноз: две группы (parent:Росстат + single) → нечестный E3
    intl_before = await _source_row(scratch_url, URL_INTL)
    assert intl_before["parent"] == rosstat_anchor and intl_before["marked"]
    groups_before = await _group_snapshot(scratch_url, STATEMENT_CBR)
    assert len({str(g["group_id"]) for g in groups_before}) == 2, groups_before
    assert {str(g["basis"]) for g in groups_before} == {f"parent:{rosstat_anchor[:12]}", "single"}
    assert await _current_assessment(scratch_url, STATEMENT_CBR) == ("E3", "supported")
    # Альфа-Банк (собственная оценка) на той же странице — тоже в группе Росстата (дефект актёра)
    alfa_groups_before = await _group_snapshot(scratch_url, STATEMENT_ALFA)
    assert {str(g["basis"]) for g in alfa_groups_before} == {f"parent:{rosstat_anchor[:12]}"}

    factory, engine = _factory(scratch_url)
    try:
        # 1. dry-run: план «снять», база не тронута
        plan = await reattribute_window(factory, store, since=SINCE, dry_run=True)
        intl_rows = [r for r in plan.rows if r.canonical_uri == URL_INTL]
        assert len(intl_rows) == 1, plan.rows
        assert intl_rows[0].action == (
            "указатель будет снят: свежий метод видит на странице два первоисточника"
        ), plan.rows
        assert intl_rows[0].now_status == "ambiguous_primaries"
        assert not plan.changed_source_ids
        assert (await _source_row(scratch_url, URL_INTL))["parent"] == rosstat_anchor

        # 2. боевой прогон: указатель снят, каскад и переоценка работают существующим кодом
        report = await reattribute_window(factory, store, since=SINCE)
        applied = {r.canonical_uri: r.action for r in report.rows}
        assert applied[URL_INTL] == "указатель снят", report.rows
        assert applied[URL_SBERCIB] == "изменений нет", report.rows  # свежий отказ, разметки не было
        assert applied[URL_GUARD] == "указатель уже проставлен ранее — изменений нет", report.rows
        assert report.changed_source_ids and len(report.changed_source_ids) == 1
        assert report.jobs_created >= 1 and report.worker is not None
    finally:
        await engine.dispose()

    intl_after = await _source_row(scratch_url, URL_INTL)
    assert intl_after["parent"] is None and not intl_after["marked"]
    guard_after = await _source_row(scratch_url, URL_GUARD)
    assert guard_after["parent"] == rosstat_anchor and guard_after["marked"]  # не кандидат на снятие

    # промежуточное состояние: обе улики CBR-прогноза одиночны — по-прежнему две группы, E3
    groups_mid = await _group_snapshot(scratch_url, STATEMENT_CBR)
    assert {str(g["basis"]) for g in groups_mid} == {"single"}
    assert await _current_assessment(scratch_url, STATEMENT_CBR) == ("E3", "supported")
    # Альфа: группа больше не приписана Росстату
    alfa_groups_mid = await _group_snapshot(scratch_url, STATEMENT_ALFA)
    assert {str(g["basis"]) for g in alfa_groups_mid} == {"single"}

    factory, engine = _factory(scratch_url)
    try:
        # 3. улики: dry-run плана поуровневой атрибуции
        plan_ev = await reattribute_evidence_window(factory, store, since=SINCE, dry_run=True)
        cbr_rows = [r for r in plan_ev.rows if r.claim_statement == STATEMENT_CBR]
        assert len(cbr_rows) == 2 and all(
            r.action == "будет записано происхождение значения улики" and r.primary_name == "Банк России"
            for r in cbr_rows
        ), plan_ev.rows
        alfa_rows = [r for r in plan_ev.rows if r.claim_statement == STATEMENT_ALFA]
        assert len(alfa_rows) == 1 and alfa_rows[0].action == (
            "изменений нет: атрибуция значения не собрана"
        ), plan_ev.rows
        assert alfa_rows[0].now_status == "value_not_attributed"  # собственная оценка Альфы — не пересказ
        assert plan_ev.changed_evidence == 0

        # 4. боевая запись происхождения значения: две улики CBR-прогноза → ЦБ,
        #    улика 5,59% на nbj — честный пересказ Росстата (её страница и есть пересказ)
        report_ev = await reattribute_evidence_window(factory, store, since=SINCE)
        assert report_ev.changed_evidence == 3, report_ev.rows
        assert report_ev.jobs_created >= 1 and report_ev.worker is not None
    finally:
        await engine.dispose()

    cbr_anchor = await _anchor_id(scratch_url, "https://cbr.ru/")

    # обе улики — пересказ ЦБ: одна группа, честная оценка E1 вместо E3
    groups_after = await _group_snapshot(scratch_url, STATEMENT_CBR)
    assert len({str(g["group_id"]) for g in groups_after}) == 1, groups_after
    assert {str(g["basis"]) for g in groups_after} == {f"parent:{cbr_anchor[:12]}"}
    assert await _current_assessment(scratch_url, STATEMENT_CBR) == ("E1", "hypothesis")

    origins = await _all(
        scratch_url,
        "SELECT s.canonical_uri, e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(STATEMENT_CBR),
    )
    for row in origins:
        record = dict(row["origin"])
        assert record["primary_key"] == "cbr" and record["primary_name"] == "Банк России"
        assert record["method"] == "host-value-attribution-v10"
        assert record["pairing"] == "value_and_primary_in_fragment"
        assert str(record["parent_source_id"]) == cbr_anchor
        assert "Банк России" in record["basis_fragment"]
    # Альфа: записи о происхождении значения нет (собственная оценка не приписана никому)
    alfa_origin = await _scalar(
        scratch_url,
        "SELECT e.scope -> 'value_attribution' FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = :s",
        {"s": STATEMENT_ALFA},
    )
    assert alfa_origin is None or alfa_origin[0] is None

    # 5. идемпотентность: повторные прогоны ничего не меняют
    factory, engine = _factory(scratch_url)
    try:
        again_page = await reattribute_window(factory, store, since=SINCE)
        assert not again_page.changed_source_ids, again_page.rows
        assert {r.canonical_uri: r.action for r in again_page.rows}[URL_INTL] == "изменений нет"
        again_ev = await reattribute_evidence_window(factory, store, since=SINCE)
        assert again_ev.changed_evidence == 0 and not again_ev.changed_source_ids
        assert all("решение уже записано ранее" in r.action for r in again_ev.rows
                   if r.claim_statement == STATEMENT_CBR), again_ev.rows
    finally:
        await engine.dispose()


# ─── коррекция оператора (ADR-0031): ни указатель, ни улики не трогаем ─────


@pytest.mark.asyncio
async def test_operator_corrected_source_is_left_alone(
    migrated_db: tuple[str, Any], tmp_path: Path, v3_era_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v3_era(scratch_url, store)
    rosstat_anchor = await _anchor_id(scratch_url, "https://rosstat.gov.ru/")

    guard = await _source_row(scratch_url, URL_GUARD)
    assert guard["parent"] == rosstat_anchor

    from packages.domain.services.audit import AuditService
    from packages.memory.source_graph import apply_source_graph_change

    factory, engine = _factory(scratch_url)
    try:
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO source_graph_corrections "
                    "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
                    "VALUES (:id, :a, 'merge', :f, :t, 'rules-v1', true)"
                ),
                {"id": uuid.uuid4(), "a": "operator:hostctl", "f": guard["id"], "t": rosstat_anchor},
            )
            await apply_source_graph_change(
                db, AuditService(db), source_ids=frozenset({uuid.UUID(guard["id"])}),
                actor="operator:hostctl",
            )

        page = await reattribute_window(factory, store, since=SINCE)
        guard_rows = [r for r in page.rows if r.canonical_uri == URL_GUARD]
        assert len(guard_rows) == 1 and "коррекцией оператора" in guard_rows[0].action, page.rows
        assert guard["id"] not in {str(s) for s in page.changed_source_ids}

        ev = await reattribute_evidence_window(factory, store, since=SINCE)
        guard_ev = [r for r in ev.rows if r.canonical_uri == URL_GUARD]
        assert len(guard_ev) == 1 and "улики не переписываем" in guard_ev[0].action, ev.rows
        assert ev.changed_evidence == 2, ev.rows  # только улики CBR-прогноза (intl + sbercib)
    finally:
        await engine.dispose()

    # указатель nbj → Росстат цел; запись значения на улике nbj не появилась
    guard_after = await _source_row(scratch_url, URL_GUARD)
    assert guard_after["parent"] == rosstat_anchor and guard_after["marked"]
    nbj_origin = await _scalar(
        scratch_url,
        "SELECT e.scope -> 'value_attribution' FROM evidence e JOIN sources s ON s.id = e.source_id "
        "WHERE s.canonical_uri = :u",
        {"u": URL_GUARD},
    )
    assert nbj_origin is None or nbj_origin[0] is None
