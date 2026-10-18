"""Scenario (DB): T7.120a (дефект приёмки T7.120, ADR-0029 §24) — replay со стенда .87.

Что закреплено (номера взяты из постановки T7.120a):

- т4: replay утверждения `cc75e97f` («инфляционные ожидания 14,2%») на двух пересказах опроса
  инФОМ, заказанного и опубликованного Банком России: обе улики получают ОДНОГО родителя —
  корень семьи `cbr`, группы сворачиваются в одну (E1/hypothesis). До правки обе страницы
  были `ambiguous_primaries` («инФОМ» и «Банк России» в одном окне читались как два
  первоисточника), и свёртки не происходило вовсе.
- т6: replay утверждения `83e7a1c8` («hh-индекс … 9,6», страница hr-ratings.com): сохранённая
  запись эпохи v13 «derivative → Росстат» с основанием «По данным Росстата, в декабре 2008
  года число безработных выросло на четверть.» НЕ проходит pairing-гейт текущего метода по
  своему основанию (числа 9,6 в нём нет — ровно то, что требует гейт значения T7.120),
  поэтому переатрибуция её СНИМАЕТ: строка плана «снята: …», записи после применения нет,
  метки «Росстат» нет, группы пересчитаны каскадом §11.3. Путь записи — тот же, что снятие
  указателя T7.98 (guarded UPDATE под FOR UPDATE, прежний метод, не под коррекцией оператора);
  нового AuditEventType и миграции нет.
- т7: та же страница, другая улика с записью эпохи, которая гейт ПРОХОДИТ (её основание
  содержит значение утверждения): при отказе свежего метода запись СОХРАНЯЕТСЯ — правило
  «знание прежней эпохи не удаляется» (T7.85) сниманием ложных записей не ослаблено.

Сеть — FakeFetchClient (§6); staging, группировка, правила и переатрибуция настоящие.
Страница hr-ratings — копия стендовой нормализованной страницы (фикстура с sha-гартом).
«Эпоха v13» моделируется подменой метки метода и решения ровно тем же приёмом, что
`_backfill_as_v1` в `tests/scenario/test_reattribute_stale_records.py`.
"""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.reattribution import NO_DECISION_YET
from apps.research_proxy.value_attribution import ValueAttributionDecision
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

SINCE_ISO = "2000-01-01T00:00:00+00:00"
#: метка эпохи стенда (переатрибуция T7.120 пересматривала записи v13; текущая — v15)
V13_VALUE_METHOD = "host-value-attribution-v13"

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t7120a"
HR_SHA = "2c3b5ad2679fdf01"  # sha256(hr_ratings_rynok_truda.normalized.txt)

URL_INTERFAX = "https://interfax.example/business/1117709"
URL_KOMMERSANT = "https://kommersant.example/doc/8294535"
URL_HR = "https://hr-ratings.com/analytics/rynok-truda-bolshoj-krizis-v-istorii-rossii"

PAGE_INTERFAX = [
    'Инфляционные ожидания населения РФ в сентябре выросли с 13,7% до 14,2%, следует из '
    'опроса "инФОМ", проведенного по заказу Банка России.',
    "Интерфакс продолжает следить за публикациями регулятора.",
]
PAGE_KOMMERSANT = [
    "Самый заметный рост ожиданий — до 14,2%, следует из опубликованных 30 сентября данных "
    "опроса, проводимого «инФОМ» по заказу Банка России. Оценка инфляционных ожиданий в "
    "сентябре 2026 года остановилась на 14,2%.",
]


def _hr_page() -> list[str]:
    """Копия стендовой страницы hr-ratings.com (T7.120a) — та же фикстура, что в unit-тестах."""
    path = FIXTURE_DIR / "hr_ratings_rynok_truda.normalized.txt"
    raw = path.read_bytes()
    assert hashlib.sha256(raw).hexdigest().startswith(HR_SHA), (
        f"фикстура {path.name} больше не стендовая страница ({URL_HR})"
    )
    return raw.decode("utf-8").splitlines()


PAGES: dict[str, list[str]] = {
    URL_INTERFAX: PAGE_INTERFAX,
    URL_KOMMERSANT: PAGE_KOMMERSANT,
    URL_HR: _hr_page(),
}

STATEMENT_142 = "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2%."
#: формулировка `83e7a1c8` со стенда — «голое» десятичное значение без измеренного профиля
STATEMENT_83 = "По данным HR-Ratings, hh-индекс в России в 2026 году составил 9,6."
#: контрастная улика той же страницы: запись эпохи, которая гейт проходит (значение в основании есть)
STATEMENT_PRES = "По данным HeadHunter, hh-индекс в России в январе 2026 года составил 9,6."

#: ложное основание сохранённой записи `83e7a1c8` (стендовый текст эпохи v13): числа 9,6 в нём нет
FALSE_BASIS_83 = "По данным Росстата, в декабре 2008 года число безработных выросло на четверть."
#: честное основание контрастной записи: значение утверждения в нём стоит текстовой формой
TRUE_BASIS_PRES = "По данным HeadHunter, hh-индекс в России в январе 2026 года составил 9,6."


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в T7.85/T7.120)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        paragraphs = "".join(f"<p>{line}</p>" for line in PAGES[url] if line.strip())
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


def _records(envelopes: list[dict[str, Any]]) -> list[Any]:
    from apps.orchestrator.evidence import observation_to_evidence

    records = [
        observation_to_evidence(
            _research_obs(
                env["final_url"],
                str(env["source_id"]),
                str(env["original_sha256"]),
                str(env["normalized_sha256"]),
                normalized_text="\n".join(PAGES[env["final_url"]]),
            ),
            {"url": env["final_url"]},
        )
        for env in envelopes
    ]
    assert all(rec is not None for rec in records)
    return records


async def _seed_claim(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...], statement: str
) -> list[dict[str, Any]]:
    envelopes = await _seed(scratch_url, store, urls)
    await _apply(scratch_url, _records(envelopes), statement)
    return envelopes


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


async def _stamp_era_v13(
    scratch_url: str,
    store: FilesystemArtifactStore,
    decisions_by_statement: dict[str, ValueAttributionDecision],
) -> Any:
    """Состояние эпохи v13 (стенд T7.120): записи пишутся с меткой прежнего метода и ровно
    теми решениями, которые писала та эпоха (тот же приём, что `_backfill_as_v1`)."""

    import apps.research_proxy.reattribution as reattr
    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    current_value_method = reattr.CURRENT_VALUE_METHOD_VERSION
    current_label = reattr.VALUE_ATTRIBUTION_METHOD_VERSION
    original_attribution = reattr.attribute_value_in_fragment
    reattr.CURRENT_VALUE_METHOD_VERSION = V13_VALUE_METHOD
    reattr.VALUE_ATTRIBUTION_METHOD_VERSION = V13_VALUE_METHOD

    def _era_decision(
        *, claim_statement: str | None = None, **kwargs: Any
    ) -> ValueAttributionDecision:
        if claim_statement in decisions_by_statement:
            return decisions_by_statement[claim_statement]
        return original_attribution(claim_statement=claim_statement, **kwargs)

    reattr.attribute_value_in_fragment = _era_decision
    try:
        return await reattribute_evidence_window(factory, store, since=_since())
    finally:
        reattr.CURRENT_VALUE_METHOD_VERSION = current_value_method
        reattr.VALUE_ATTRIBUTION_METHOD_VERSION = current_label
        reattr.attribute_value_in_fragment = original_attribution
        await engine.dispose()


async def _records_of(scratch_url: str, statement: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT e.id, s.canonical_uri, e.scope -> 'value_attribution' AS origin "
        "FROM evidence e JOIN claims c ON c.id = e.claim_id "
        "LEFT JOIN sources s ON s.id = e.source_id WHERE c.statement = " + _quoted(statement)
        + " ORDER BY s.canonical_uri",
    )


async def _group_ids(scratch_url: str, statement: str) -> set[str]:
    rows = await _all(
        scratch_url,
        "SELECT m.group_id FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(statement),
    )
    return {str(r["group_id"]) for r in rows}


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


async def _graph_event_count(scratch_url: str) -> int:
    row = await _scalar(
        scratch_url, "SELECT count(*) FROM audit_events WHERE type = 'source_graph_changed'"
    )
    assert row is not None
    return int(row[0])


# ─── т4: replay cc75e97f — один общий родитель (корень семьи) и одна группа ────────


@pytest.mark.asyncio
async def test_infom_retellings_replay_into_one_cbr_group_t7120a(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Было (982ffc5, эпоха T7.120): обе страницы `ambiguous_primaries` — «инФОМ» и «Банк России»
    в одном окне читались как два первоисточника: ни страничного указателя, ни записей значения,
    две независимые группы. Стало (T7.120a): семья склеена — уже на чтении обе страницы получают
    указатель на один якорь Банка России, переатрибуция дописывает записи значения к тому же
    родителю, и утверждение живёт в ОДНОЙ группе E1/hypothesis (grade считает только rules engine)."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, (URL_INTERFAX, URL_KOMMERSANT), STATEMENT_142)
    # T7.120a заметна уже на чтении: страничные указатели обоих пересказов ведут на один якорь ЦБ
    page_parents = {
        str(r["parent_source_id"])
        for r in await _all(
            scratch_url,
            "SELECT canonical_uri, parent_source_id FROM sources WHERE canonical_uri IN ("
            + _quoted(URL_INTERFAX) + ", " + _quoted(URL_KOMMERSANT) + ")",
        )
    }
    assert len(page_parents) == 1 and "None" not in page_parents, page_parents
    assert len(await _group_ids(scratch_url, STATEMENT_142)) == 1, "обе страницы склеены семьёй"

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    rows = {
        row.canonical_uri: row
        for row in plan.rows
        if row.canonical_uri in (URL_INTERFAX, URL_KOMMERSANT)
    }
    assert set(rows) == {URL_INTERFAX, URL_KOMMERSANT}, plan.rows
    for row in rows.values():
        assert row.was_status == NO_DECISION_YET, row
        assert (row.now_status, row.primary_name) == ("derivative", "Банк России"), row
        assert row.action == "будет записано происхождение значения улики", row
    assert plan.dry_run and plan.changed_evidence == 0, plan.rows

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows

    written = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_142)
        if r["origin"] is not None
    }
    assert set(written) == {URL_INTERFAX, URL_KOMMERSANT}, written
    assert all(rec["primary_key"] == "cbr" for rec in written.values()), written
    assert all(rec["primary_name"] == "Банк России" for rec in written.values()), written
    # ОДИН родитель на обе пересказы: группировщик T7.117 видит корень семьи, а не два центра;
    # переатрибуция переиспользует тот же якорь страничного указателя, не плодя второго родителя
    parents = {str(rec["parent_source_id"]) for rec in written.values()}
    assert parents == page_parents, (parents, page_parents)
    anchor_uri = await _scalar(
        scratch_url, "SELECT canonical_uri FROM sources WHERE id = :id", {"id": next(iter(parents))}
    )
    assert anchor_uri is not None and str(anchor_uri[0]) == "https://cbr.ru/", anchor_uri

    groups = await _group_ids(scratch_url, STATEMENT_142)
    assert len(groups) == 1, groups
    assert await _current_assessment(scratch_url, STATEMENT_142) == ("E1", "hypothesis")
    # пересчёт — только существующим каскадом §11.3
    assert await _graph_event_count(scratch_url) >= 1
    assert report.jobs_created >= 1 and report.worker is not None and report.worker.completed >= 1

    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    for uri in (URL_INTERFAX, URL_KOMMERSANT):
        row = next(r for r in again.rows if r.canonical_uri == uri)
        assert row.action == "изменений нет: решение уже записано ранее", row


# ─── т6 + т7: снятие ложной записи 83e7a1c8 и сохранение честной записи той же страницы ──


@pytest.mark.asyncio
async def test_false_value_record_is_removed_and_honest_record_survives_t7120a(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Дефект `83e7a1c8`: сохранённая запись «derivative → Росстат (v13)» с основанием про
    декабрь 2008 года не проходит pairing-гейт текущего метода (значения 9,6 в ней нет) —
    переатрибуция снимает её тем же путём записи, что снятие указателя T7.98. Запись той же
    страницы, которая гейт проходит (контрастная улика с числом 9,6 в своём основании), при
    отказе свежего метода остаётся неприкосновенной."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    envelopes = await _seed_claim(scratch_url, store, (URL_HR,), STATEMENT_83)
    # вторая улика той же страницы: staging той же прочитанной строки источника под другим утверждением
    await _apply(scratch_url, _records(envelopes), STATEMENT_PRES)

    before_events = await _graph_event_count(scratch_url)
    era = await _stamp_era_v13(
        scratch_url,
        store,
        {
            STATEMENT_83: ValueAttributionDecision(
                status="derivative",
                primary_key="rosstat",
                primary_name="Росстат",
                pairing="value_and_primary_in_fragment",
                basis_fragment=FALSE_BASIS_83,
                claim_values=("9.6",),
            ),
            STATEMENT_PRES: ValueAttributionDecision(
                status="derivative",
                primary_key="rosstat",
                primary_name="Росстат",
                pairing="publication_window",
                basis_fragment=TRUE_BASIS_PRES,
                claim_values=("9.6",),
            ),
        },
    )
    assert era.changed_evidence == 2, era.rows
    stamped = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_83)
        if r["origin"] is not None
    }
    assert stamped and all(rec["method"] == V13_VALUE_METHOD for rec in stamped.values()), stamped
    # запись эпохи склеивала улику с якорем Росстата — именно эта связь и будет снята
    era_parents = {str(rec["parent_source_id"]) for rec in stamped.values()}
    assert len(era_parents) == 1, era_parents
    era_anchor = await _scalar(
        scratch_url,
        "SELECT canonical_uri FROM sources WHERE id = :id",
        {"id": next(iter(era_parents))},
    )
    assert era_anchor is not None and str(era_anchor[0]) == "https://rosstat.gov.ru/", era_anchor

    # план ТЕКУЩЕГО метода (v15): ложная запись — «снята», честная — «сохранена»
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    rows_83 = [
        row for row in plan.rows if row.canonical_uri == URL_HR and row.claim_statement == STATEMENT_83
    ]
    rows_pres = [
        row for row in plan.rows if row.canonical_uri == URL_HR and row.claim_statement == STATEMENT_PRES
    ]
    assert len(rows_83) == 1 and len(rows_pres) == 1, plan.rows
    removal_row = rows_83[0]
    assert removal_row.was_status == f"derivative → Росстат ({V13_VALUE_METHOD})", removal_row
    assert removal_row.now_status == "own_assessment", removal_row  # свежий метод отказался решать
    assert removal_row.action.startswith("будет снята: значение утверждения отсутствует"), plan.rows
    kept_row = rows_pres[0]
    assert kept_row.action.startswith("прежняя запись сохранена"), plan.rows
    assert plan.dry_run and plan.changed_evidence == 0, plan.rows
    assert (await _records_of(scratch_url, STATEMENT_83))[0]["origin"] is not None, "dry-run что-то пишет"

    report = await _run_backfill(scratch_url, store)
    # изменена ровно одна улика — снятие; сохранённая запись не тронута
    assert report.changed_evidence == 1, report.rows
    changed_sources = {str(sid) for sid in report.changed_source_ids}
    assert changed_sources == {str(envelopes[0]["source_id"])}, changed_sources

    after_83 = await _records_of(scratch_url, STATEMENT_83)
    assert all(r["origin"] is None for r in after_83), after_83  # записи нет
    names_83 = {
        str(dict(r["origin"])["primary_name"]) for r in after_83 if r["origin"] is not None
    }
    assert "Росстат" not in names_83, names_83  # метки снятой эпохи не осталось

    after_pres = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_PRES)
        if r["origin"] is not None
    }
    assert after_pres[URL_HR]["method"] == V13_VALUE_METHOD, after_pres
    assert after_pres[URL_HR]["primary_name"] == "Росстат", after_pres  # запись эпохи цела

    # связь снята: улика больше не ссылается на якорь эпохи; её группа пересчитана каскадом §11.3
    assert len(await _group_ids(scratch_url, STATEMENT_83)) == 1, "у единственного источника — своя группа"
    assert await _current_assessment(scratch_url, STATEMENT_83) == ("E1", "hypothesis")
    assert await _graph_event_count(scratch_url) >= before_events + 2  # прогон эпохи + снятие

    # повторный прогон идемпотентен: снятая запись не возникает, сохранённая не снимается
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    row_83 = next(r for r in again.rows if r.canonical_uri == URL_HR and r.claim_statement == STATEMENT_83)
    assert row_83.action == "изменений нет: атрибуция значения не собрана", row_83
    assert row_83.was_method == "", row_83
    row_pres = next(r for r in again.rows if r.canonical_uri == URL_HR and r.claim_statement == STATEMENT_PRES)
    assert row_pres.action.startswith("прежняя запись сохранена"), row_pres


# ─── т7 отдельным тестом: запись, проходящая гейт, неприкосновенна в любом методе ──


@pytest.mark.asyncio
async def test_pairing_gate_passing_stale_record_is_preserved_t7120a(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Guard-тест (зелён и на 982ffc5): снятие T7.120a не расширено на случай «свежий метод
    отказался решать». Запись эпохи, чьё основание содержит значение утверждения (гейт
    проходит), при отказе свежего метода сохраняется без изменений — правило T7.85."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, (URL_HR,), STATEMENT_PRES)
    era = await _stamp_era_v13(
        scratch_url,
        store,
        {
            STATEMENT_PRES: ValueAttributionDecision(
                status="derivative",
                primary_key="rosstat",
                primary_name="Росстат",
                pairing="publication_window",
                basis_fragment=TRUE_BASIS_PRES,
                claim_values=("9.6",),
            )
        },
    )
    assert era.changed_evidence == 1, era.rows

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    row = next(r for r in plan.rows if r.canonical_uri == URL_HR and r.claim_statement == STATEMENT_PRES)
    assert row.now_status == "own_assessment", row  # свежий метод отказался решать
    assert row.action.startswith("прежняя запись сохранена"), row

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0 and not report.changed_source_ids, report.rows
    origin = next(r for r in await _records_of(scratch_url, STATEMENT_PRES))["origin"]
    assert origin is not None and dict(origin)["method"] == V13_VALUE_METHOD, origin
    assert dict(origin)["primary_name"] == "Росстат", origin


# ─── снятие не перешагивает коррекцию оператора (ADR-0031) ────────────────────────


@pytest.mark.asyncio
async def test_operator_corrected_source_is_not_stripped_of_its_record_t7120a(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Ложная запись под коррекцией оператора не снимается автоматическим прогоном: тот же
    guard (FOR UPDATE + NOT EXISTS source_graph_corrections), что защищает перезапись T7.85."""

    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    envelopes = await _seed_claim(scratch_url, store, (URL_HR,), STATEMENT_83)
    source_id = uuid.UUID(str(envelopes[0]["source_id"]))
    era = await _stamp_era_v13(
        scratch_url,
        store,
        {
            STATEMENT_83: ValueAttributionDecision(
                status="derivative",
                primary_key="rosstat",
                primary_name="Росстат",
                pairing="value_and_primary_in_fragment",
                basis_fragment=FALSE_BASIS_83,
                claim_values=("9.6",),
            )
        },
    )
    assert era.changed_evidence == 1, era.rows

    from packages.domain.services.audit import AuditService
    from packages.memory.source_graph import apply_source_graph_change

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO source_graph_corrections "
                "(id, actor, kind, from_source_id, to_source_id, rules_version, valid) "
                "VALUES (:id, :a, 'merge', :f, :t, 'rules-v1', true)"
            ),
            {"id": uuid.uuid4(), "a": "operator:hostctl", "f": source_id, "t": source_id},
        )
        await apply_source_graph_change(
            db, AuditService(db), source_ids=frozenset({source_id}), actor="operator:hostctl"
        )

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    row = next(r for r in plan.rows if r.canonical_uri == URL_HR)
    assert "коррекцией оператора" in row.action, row

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0 and not report.changed_source_ids, report.rows
    origin = next(r for r in await _records_of(scratch_url, STATEMENT_83))["origin"]
    assert origin is not None and dict(origin)["method"] == V13_VALUE_METHOD, origin
