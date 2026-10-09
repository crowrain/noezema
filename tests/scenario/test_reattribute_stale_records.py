"""Scenario (DB): переатрибуция решений, записанных ПРЕЖНЕЙ версией метода значений (T7.85).

Стендовый пропуск (`391c4388`, «наблюдаемая инфляция 14,5%», E3/0.75): forbes.ru и kommersant.ru
пересказывают один и тот же опубликованный ЦБ опрос «инФОМ», но фраза первоисточника стоит в
предыдущем предложении того же блока («…следует из опроса «инФОМ», опубликованного ЦБ.»), а значение —
дальше. Метод значений v1 знал pairing только внутри одного фрагмента, записи о происхождении значения
не появились вовсе (`value_attributions: []` в журнале сессии), и два пересказа остались двумя
группами независимости.

Что закреплено этим сценарием (сам новый pairing на живых стендовых текстах закреплён отдельно —
`tests/unit/test_attribution_fixtures_t785.py`):

1. запись, принятая прежней версией метода, ПЕРЕСМАТРИВАЕТСЯ, а не пропускается как «уже решено»; в
   план печатается «было → станет» вместе с методом прежнего решения;
2. уже существующий указатель первоисточника НЕ подменяется: при том же `primary_key` свежий прогон
   берёт записанного родителя, а не заводит новый якорь (на стенде — `33912509`);
3. источники под коррекцией оператора (ADR-0031) не переписываются ни в страничном режиме, ни в
   поуровневом, и в dry-run это отдельная строка плана;
4. если свежий метод отказался решать, прежняя запись улики СОХРАНЯЕТСЯ: знание не удаляется, оператор
   получает честную строку «проверьте вручную»;
5. пересчёт делает только существующий каскад §11.3 (`apply_source_graph_change` + рабочий переоценки),
   а повторный прогон идемпотентен;
6. «первоисточник + два его пересказа» не оценивается выше, чем одна независимая группа;
7. (T7.85a) записи, помеченные методом v2 (то окно публикации, поведение которого изменилось),
   пересматриваются текущим методом на том же основании «метки различаются»; с T7.85b и T7.85c
   тем же предикатом пересматриваются и метки v3, и v4 — прежних эпох в базе может быть несколько.

Сеть подменена детерминированным FakeFetchClient (§6); staging, `MemoryService`, группировка, правила и
переатрибуция — настоящие. «Эпоха v1» (и, с T7.85a/T7.85b/T7.85c, эпохи v2…v4) моделируется
тем же приёмом, что
`broken_v1_fetch` в T7.77/T7.81: подменяется метка метода, которой пишется запись. Прямых UPDATE
доменных таблиц в тесте нет.

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
from apps.research_proxy.value_attribution import STATUS_SELF_PRIMARY, ValueAttributionDecision
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
V1_VALUE_METHOD = "host-value-attribution-v1"
#: метка эпохи T7.85 (окно публикации без вето); переатрибуция T7.85a обязана пересматривать
#: записи и с этой меткой — основание ровно то же, что для v1: метка ≠ текущий метод
V2_VALUE_METHOD = "host-value-attribution-v2"
#: метка эпохи T7.85a (окно публикации с вето «другого источника числа»): с T7.85b она тоже
#: ПРЕЖНЯЯ и обязана пересматриваться тем же предикатом, что v1 и v2
V3_VALUE_METHOD = "host-value-attribution-v3"
#: метка эпохи T7.85b (окно публикации плюс сокращённое имя организации и глагол фиксации):
#: с T7.85c она тоже ПРЕЖНЯЯ и пересматривается тем же предикатом, что v1, v2 и v3
V4_VALUE_METHOD = "host-value-attribution-v4"
#: текущий метод значений (T7.85c): им пишется запись после пересмотра прежних эпох v2…v4
CURRENT_VALUE_METHOD = "host-value-attribution-v5"

# ─── тексты: та же синтаксическая форма, что у стендовых страниц (фикстуры T7.85) ───
# абзац нормализованного текста = одна строка; страница первоисточника — на своём home host
PAGE_ROSSTAT = [
    "Федеральная служба государственной статистики.",
    "Годовая инфляция в России по итогам 2025 года (декабрь к декабрю 2024) составила 5,59%.",
]
# страница называет ДВА разных первоисточника: страничный детектор честно отказывается решать
# (`ambiguous_primaries`) и указателя не ставит — решение возможно только по конкретному значению
PAGE_VEDOMOSTI = [
    "По\xa0данным Росстата, годовая инфляция в России по итогам 2025 года составила 5,59%.",
    "По данным Федеральной налоговой службы, сборы налогов в 2025 году выросли на 8,4%.",
]
PAGE_EXPERT = [
    "Годовая инфляция в России замедлилась до 5,59%, следует из данных Росстата.",
    "Как сообщил Банк России, ключевая ставка в декабре 2025 года составляла 16,5%.",
]
# собственная страница первоисточника: пересказом самой себя она быть не может
PAGE_CBR_HOME = [
    "Банк России опубликовал результаты декабрьского мониторинга инфляционных ожиданий.",
    "Оценка наблюдаемой населением годовой инфляции в декабре 2025 года составила 14,5% — по данным Банка России.",
]
# форма стендовой пропуска: фраза первоисточника — в предыдущем предложении того же блока, а
# значение (14,5%) стоит дальше; второй блок называет другой первоисточник → страница неоднозначна
PAGE_FORBES = [
    "Инфляционные ожидания россиян выросли до 13,7%, следует из опроса «инФОМ», опубликованного ЦБ. "
    "Оценка наблюдаемой населением годовой инфляции в декабре 2025 года составила 14,5%.",
    "По данным Росстата, официальная инфляция за 2025 год составила 5,59%.",
]
PAGE_KOMMERSANT = [
    "Самый заметный рост ожиданий — с 15,4% до 15,6%, следует из опубликованных данных опроса, "
    "проводимого «инФОМ» по заказу Банка России. Оценка наблюдаемой населением годовой инфляции "
    "в декабре 2025 года осталась на уровне 14,5%.",
    "По данным Росстата, официальная инфляция за 2025 год составила 5,59%.",
]

URL_ROSSTAT = "https://www.rosstat.gov.ru/operativnye-dannye-2025"
URL_VEDOMOSTI = "https://vedomosti.example/inflyaciya-2025-goda"
URL_EXPERT = "https://expert.example/godovaya-inflyaciya-5-59"
URL_CBR_HOME = "https://www.cbr.ru/press/monitoring-dekabrya"
URL_FORBES = "https://forbes.example/552221-ozhidaniya"
URL_KOMMERSANT = "https://kommersant.example/doc/8294535"

PAGES: dict[str, list[str]] = {
    URL_ROSSTAT: PAGE_ROSSTAT,
    URL_VEDOMOSTI: PAGE_VEDOMOSTI,
    URL_EXPERT: PAGE_EXPERT,
    URL_CBR_HOME: PAGE_CBR_HOME,
    URL_FORBES: PAGE_FORBES,
    URL_KOMMERSANT: PAGE_KOMMERSANT,
}

STATEMENT_559 = "Годовая инфляция в России по итогам 2025 года составила 5,59%."
STATEMENT_145 = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5%."
)

OFFICIAL_URLS = (URL_ROSSTAT, URL_VEDOMOSTI, URL_EXPERT)
POLL_URLS = (URL_CBR_HOME, URL_FORBES, URL_KOMMERSANT)


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.75–T7.78)."""

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
    """fetch + staging одним шагом: оценка соответствует числу групп прочитанных страниц."""

    envelopes = await _seed(scratch_url, store, urls)
    await _apply(scratch_url, _records(envelopes), statement)
    return envelopes


def _since() -> Any:
    from datetime import UTC, datetime

    return datetime.fromisoformat(SINCE_ISO.replace("Z", "+00:00")).replace(tzinfo=UTC)


async def _run_backfill(
    scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool = False
) -> Any:
    """Поуровневая переатрибуция окна ТЕКУЩИМ методом."""

    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_evidence_window(factory, store, since=_since(), dry_run=dry_run)
    finally:
        await engine.dispose()


async def _run_page_backfill(
    scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool = False
) -> Any:
    from apps.research_proxy.reattribution import reattribute_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_window(factory, store, since=_since(), dry_run=dry_run)
    finally:
        await engine.dispose()


async def _backfill_as_v1(
    scratch_url: str, store: FilesystemArtifactStore, *, stale_method: str = V1_VALUE_METHOD
) -> Any:
    """Тот же прогон, но запись помечена ПРЕЖНЕЙ версией метода — состояние эпохи `stale_method`
    (v1 по умолчанию; с T7.85a тем же приёмом моделируется эпоха v2).

    Метод значений v1 не знал окна публикации; здесь моделируется ровно то, от чего зависит
    переатрибуция: метка записи, отличная от текущей (тот же приём, что `broken_v1_fetch`)."""

    import apps.research_proxy.reattribution as reattr
    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    current_value_method = reattr.VALUE_ATTRIBUTION_METHOD_VERSION
    current_page_method = reattr.CURRENT_VALUE_METHOD_VERSION
    reattr.CURRENT_VALUE_METHOD_VERSION = stale_method
    reattr.VALUE_ATTRIBUTION_METHOD_VERSION = stale_method
    try:
        return await reattribute_evidence_window(factory, store, since=_since())
    finally:
        reattr.CURRENT_VALUE_METHOD_VERSION = current_page_method
        reattr.VALUE_ATTRIBUTION_METHOD_VERSION = current_value_method
        await engine.dispose()


async def _records_of(scratch_url: str, statement: str) -> list[Any]:
    """Записи о происхождении значения на уликах утверждения (то, что лежит в `evidence.scope`)."""

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


async def _sources_count(scratch_url: str) -> int:
    row = await _scalar(scratch_url, "SELECT count(*) FROM sources")
    assert row is not None
    return int(row[0])


# ─── 1 + 2 + 5: прежняя запись пересматривается, указатель остаётся, повтор пустой ──


@pytest.mark.asyncio
async def test_stale_value_record_is_reconsidered_and_existing_parent_survives(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, OFFICIAL_URLS, STATEMENT_559)
    assert len(await _group_ids(scratch_url, STATEMENT_559)) == 3, "три разных хоста — три группы"
    assert await _current_assessment(scratch_url, STATEMENT_559) == ("E3", "supported")

    era = await _backfill_as_v1(scratch_url, store)
    assert era.changed_evidence == 2, era.rows  # vedomosti + expert; страница Росстата — не пересказ
    sources_before = await _sources_count(scratch_url)

    written_era = {str(r["canonical_uri"]): r["origin"] for r in await _records_of(scratch_url, STATEMENT_559)}
    assert all(
        dict(written_era[u])["method"] == V1_VALUE_METHOD for u in (URL_VEDOMOSTI, URL_EXPERT)
    ), written_era
    anchor = str(dict(written_era[URL_VEDOMOSTI])["parent_source_id"])
    assert written_era[URL_ROSSTAT] is None, written_era[URL_ROSSTAT]

    # план ТЕКУЩЕГО метода: прежняя запись — не пропуск; «было» печатается вместе с методом
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    stale = [row for row in plan.rows if row.canonical_uri in (URL_VEDOMOSTI, URL_EXPERT)]
    assert len(stale) == 2, plan.rows
    assert all(row.was_method == V1_VALUE_METHOD for row in stale), plan.rows
    assert all(row.was_status == f"derivative → Росстат ({V1_VALUE_METHOD})" for row in stale), plan.rows
    assert all(row.now_status == "derivative" for row in stale), plan.rows
    assert all(
        row.action == "будет записано прежнее происхождение значения улики" for row in stale
    ), plan.rows
    own_row = next(row for row in plan.rows if row.canonical_uri == URL_ROSSTAT)
    assert own_row.action == "изменений нет: атрибуция значения не собрана", own_row
    assert own_row.was_method == "", own_row
    # dry-run ничего не пишет
    assert plan.dry_run and not plan.changed_source_ids and plan.changed_evidence == 0
    assert await _sources_count(scratch_url) == sources_before

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows
    written = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_559)
        if r["origin"] is not None
    }
    for uri in (URL_VEDOMOSTI, URL_EXPERT):
        record = written[uri]
        assert record["method"] == CURRENT_VALUE_METHOD, record
        assert record["primary_key"] == "rosstat", record
        # тот же первоисточник → тот же родитель: существующий указатель не подменяется новым якорем
        assert str(record["parent_source_id"]) == anchor, record
        assert record["pairing"] == "value_and_primary_in_fragment", record
        assert record["schema"] == "host-value-attribution-v1", record
    own = next(r for r in await _records_of(scratch_url, STATEMENT_559) if r["canonical_uri"] == URL_ROSSTAT)
    assert own["origin"] is None, "страница первоисточника получила запись о пересказе самой себя"
    assert await _sources_count(scratch_url) == sources_before, "появился второй якорь первоисточника"

    # пересчёт — только существующим каскадом §11.3 и рабочим переоценки
    graphs = await _all(scratch_url, "SELECT id FROM audit_events WHERE type = 'source_graph_changed'")
    assert len(graphs) == 2, graphs  # прогон эпохи v1 + текущий прогон
    assert report.jobs_created >= 1 and report.worker is not None and report.worker.completed >= 1

    after = await _group_ids(scratch_url, STATEMENT_559)
    # якорь Росстата (rosstat.gov.ru) и страница Росстата сворачиваются по домену gov.ru (ловушка
    # T7.75), два пересказа — через общего родителя: одна группа вместо трёх
    assert len(after) == 1, after
    assert await _current_assessment(scratch_url, STATEMENT_559) == ("E1", "hypothesis")

    # повторный прогон идемпотентен: записи текущего метода не перезаписываются никогда
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    for uri in (URL_VEDOMOSTI, URL_EXPERT):
        row = next(r for r in again.rows if r.canonical_uri == uri)
        assert row.action == "изменений нет: решение уже записано ранее", row
        assert row.was_method == CURRENT_VALUE_METHOD, row
    assert (again.affected_claims, again.invalidated_heads, again.jobs_created) == (0, 0, 0)


# ─── 6: первоисточник + два его пересказа не дороже одной независимой группы ──────


@pytest.mark.asyncio
async def test_primary_plus_two_retellings_is_not_graded_above_one_group(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """Форма дефекта `391c4388` (E3/0.75): страница ЦБ плюс два пересказа одного опроса ЦБ."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, POLL_URLS, STATEMENT_145)
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 3
    assert await _current_assessment(scratch_url, STATEMENT_145) == ("E3", "supported")

    # dry-run до первой записи: «было» честно говорит, что решения не было
    from apps.research_proxy.reattribution import NO_DECISION_YET

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    by_url = {row.canonical_uri: row for row in plan.rows}
    assert all(row.was_status == NO_DECISION_YET for row in plan.rows), plan.rows
    poll_row = by_url[URL_FORBES]
    assert (poll_row.now_status, poll_row.primary_name) == ("derivative", "Банк России"), poll_row
    assert poll_row.action == "будет записано происхождение значения улики", poll_row
    own_plan_row = by_url[URL_CBR_HOME]
    assert own_plan_row.now_status == STATUS_SELF_PRIMARY, own_plan_row
    assert own_plan_row.action == "изменений нет: атрибуция значения не собрана", own_plan_row

    report = await _run_backfill(scratch_url, store)
    origins = await _records_of(scratch_url, STATEMENT_145)
    decided = {str(r["canonical_uri"]): dict(r["origin"]) for r in origins if r["origin"] is not None}
    assert set(decided) == {URL_FORBES, URL_KOMMERSANT}, decided
    assert all(rec["primary_key"] == "cbr" for rec in decided.values()), decided
    # слабое основание видно оператору: это окно публикации, а не строгий pairing
    assert all(rec["pairing"] == "publication_window" for rec in decided.values()), decided
    assert all(rec["method"] == CURRENT_VALUE_METHOD for rec in decided.values()), decided
    assert len({str(rec["parent_source_id"]) for rec in decided.values()}) == 1, decided

    own = next(r for r in origins if r["canonical_uri"] == URL_CBR_HOME)
    assert own["origin"] is None, "собственная страница ЦБ стала пересказом сама себя"
    own_row = next(row for row in report.rows if row.canonical_uri == URL_CBR_HOME)
    assert own_row.now_status == STATUS_SELF_PRIMARY, own_row

    assert report.changed_evidence == 2 and report.jobs_created >= 1, report.rows
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 1
    # ровно то же, что даёт одна независимая группа: ложное «подтверждено независимо» снято
    assert await _current_assessment(scratch_url, STATEMENT_145) == ("E1", "hypothesis")


# ─── 3: коррекция оператора (ADR-0031) автоматический прогон не перешагивает ──────


@pytest.mark.asyncio
async def test_operator_corrected_sources_are_left_alone_in_both_modes(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    envelopes = await _seed_claim(scratch_url, store, OFFICIAL_URLS, STATEMENT_559)
    by_url = {env["final_url"]: uuid.UUID(str(env["source_id"])) for env in envelopes}
    left, right = by_url[URL_VEDOMOSTI], by_url[URL_EXPERT]

    era = await _backfill_as_v1(scratch_url, store)
    assert era.changed_evidence == 2, era.rows
    written_era = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_559)
        if r["origin"] is not None
    }

    # коррекция оператора: та же строка графа, что пишет dispute_claim (актёр operator:*)
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
            {"id": uuid.uuid4(), "a": "operator:hostctl", "f": left, "t": right},
        )
        await apply_source_graph_change(
            db, AuditService(db), source_ids=frozenset({left, right}), actor="operator:hostctl"
        )

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    corrected_rows = [row for row in plan.rows if "коррекцией оператора" in row.action]
    assert {row.canonical_uri for row in corrected_rows} == {URL_VEDOMOSTI, URL_EXPERT}, plan.rows
    assert all(row.was_method == V1_VALUE_METHOD for row in corrected_rows), corrected_rows

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0 and not report.changed_source_ids, report.rows
    written_after = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_559)
        if r["origin"] is not None
    }
    for uri in (URL_VEDOMOSTI, URL_EXPERT):
        assert written_after[uri] == written_era[uri], "запись оператора переписана автоматическим прогоном"

    # страничный режим: те же источники под коррекцией не трогаются (строка плана есть и там)
    page_report = await _run_page_backfill(scratch_url, store, dry_run=True)
    page_rows = [row for row in page_report.rows if row.canonical_uri in (URL_VEDOMOSTI, URL_EXPERT)]
    assert all(row.action == "под коррекцией оператора (ADR-0031) — не трогаем" for row in page_rows), page_rows

    parents = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id FROM sources WHERE canonical_uri IN "
        f"({_quoted(URL_VEDOMOSTI)}, {_quoted(URL_EXPERT)})",
    )
    assert all(row["parent_source_id"] is None for row in parents), parents


# ─── 4: свежий метод отказался — прежняя запись улики остаётся ────────────────────


@pytest.mark.asyncio
async def test_refusal_of_current_method_keeps_the_previous_record(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, OFFICIAL_URLS, STATEMENT_559)
    era = await _backfill_as_v1(scratch_url, store)
    assert era.changed_evidence == 2, era.rows
    written_era = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_559)
        if r["origin"] is not None
    }

    # отказ текущего метода моделируется на детекторе (не в БД): прежнее знание не должно исчезнуть
    import apps.research_proxy.reattribution as reattr

    monkeypatch.setattr(
        reattr,
        "attribute_value_in_fragment",
        lambda **kwargs: ValueAttributionDecision(status=STATUS_SELF_PRIMARY),
    )

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    stale = [row for row in plan.rows if row.canonical_uri in (URL_VEDOMOSTI, URL_EXPERT)]
    assert len(stale) == 2, plan.rows
    assert all(row.action.startswith("прежняя запись сохранена") for row in stale), stale

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 0 and not report.changed_source_ids, report.rows
    written_after = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_559)
        if r["origin"] is not None
    }
    for uri in (URL_VEDOMOSTI, URL_EXPERT):
        assert written_after[uri] == written_era[uri], "запись удалена или перезаписана отказом"


# ─── 7: T7.85a — записи эпохи v2 пересматриваются точно так же, как эпоха v1 ───────


@pytest.mark.asyncio
async def test_records_tagged_v2_method_are_reconsidered_by_current_version(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """T7.85a поднял метод значений до v3 (в окне публикации действует вето «другого источника
    числа»). Записи, помеченные v2 — решения того же оконного режима, поведение которого
    изменилось, — обязаны быть пересмотрены, а не пропущены как «уже решено»: основание отбора
    ровно то же, что для эпохи v1 (метка записи ≠ текущий метод)."""

    import apps.research_proxy.reattribution as reattr

    assert reattr.CURRENT_VALUE_METHOD_VERSION == CURRENT_VALUE_METHOD, "отбор устаревших меток"

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, POLL_URLS, STATEMENT_145)
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 3

    # эпоха v2: те же страницы, то же окно публикации — но запись помечена v2
    era = await _backfill_as_v1(scratch_url, store, stale_method=V2_VALUE_METHOD)
    assert era.changed_evidence == 2, era.rows
    written_era = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_145)
        if r["origin"] is not None
    }
    assert set(written_era) == {URL_FORBES, URL_KOMMERSANT}, written_era
    assert all(rec["method"] == V2_VALUE_METHOD for rec in written_era.values()), written_era
    assert all(rec["pairing"] == "publication_window" for rec in written_era.values()), written_era

    # текущий метод (v3) не считает их «уже решёнными»: пересмотр прежнего решения виден в плане
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    stale = [row for row in plan.rows if row.canonical_uri in (URL_FORBES, URL_KOMMERSANT)]
    assert len(stale) == 2, plan.rows
    assert all(row.was_method == V2_VALUE_METHOD for row in stale), stale
    assert all(
        row.was_status == f"derivative → Банк России ({V2_VALUE_METHOD})" for row in stale
    ), stale
    # синтетические страницы подставки чисты: вето не сработало, решение то же — пересмотренное
    assert all(row.action == "будет записано прежнее происхождение значения улики" for row in stale), stale

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows
    written_after = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_145)
        if r["origin"] is not None
    }
    assert all(rec["method"] == CURRENT_VALUE_METHOD for rec in written_after.values()), written_after
    assert all(rec["primary_key"] == "cbr" for rec in written_after.values()), written_after
    assert all(rec["pairing"] == "publication_window" for rec in written_after.values()), written_after
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 1
    assert await _current_assessment(scratch_url, STATEMENT_145) == ("E1", "hypothesis")

    # повторный прогон идемпотентен: записи текущего метода не перезаписываются никогда
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows


# ─── 8: T7.85b — прежние метки v2 И v3 пересматриваются одним и тем же предикатом ─────


async def _relabel_value_method(scratch_url: str, canonical_uri: str, method: str) -> None:
    """Точечная смена метки метода в записи улики: так выглядит страница, переатрибутированная
    эпохой v3, рядом со страницей эпохи v2 (тот же приём моделирования прежней эпохи, что
    `_backfill_as_v1`, но на одном адресе)."""

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE evidence SET scope = jsonb_set(scope, '{value_attribution,method}', "
                    "CAST(:method AS jsonb)) WHERE id IN ("
                    "  SELECT e.id FROM evidence e JOIN sources s ON s.id = e.source_id "
                    "  WHERE s.canonical_uri = :uri AND e.evidence_kind = 'source_assertion' "
                    "    AND jsonb_typeof(e.scope -> 'value_attribution') = 'object')"
                ),
                {"method": f'"{method}"', "uri": canonical_uri},
            )
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_records_tagged_v3_and_v4_methods_are_reconsidered_by_current_version(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    """T7.85b поднял метод значений до v4 (сокращённое имя организации вне словаря и глагол
    фиксации/измерения в том же окне), T7.85c — до v5 (окно публикации только для русского текста
    и латинское имя собственного вне словаря). В базе рядом лежат записи ДВУХ прежних эпох — v3
    (окно с вето T7.85a) и v4 (окно с признаками T7.85b); эпоха v2 закреплена предыдущим тестом.
    Предикат отбора не менялся: метка записи ≠ текущий метод, поэтому обе обязаны быть пересмотрены,
    а не пропущены как «уже решено» (STATUS T7.85b, STATUS T7.85c)."""

    import apps.research_proxy.reattribution as reattr

    assert reattr.CURRENT_VALUE_METHOD_VERSION == CURRENT_VALUE_METHOD, "отбор устаревших меток"

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, POLL_URLS, STATEMENT_145)
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 3

    era = await _backfill_as_v1(scratch_url, store, stale_method=V2_VALUE_METHOD)
    assert era.changed_evidence == 2, era.rows
    await _relabel_value_method(scratch_url, URL_FORBES, V3_VALUE_METHOD)
    await _relabel_value_method(scratch_url, URL_KOMMERSANT, V4_VALUE_METHOD)
    written_era = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_145)
        if r["origin"] is not None
    }
    assert written_era[URL_FORBES]["method"] == V3_VALUE_METHOD, written_era
    assert written_era[URL_KOMMERSANT]["method"] == V4_VALUE_METHOD, written_era

    # обе метки прежние (v3 и v4) → обе строки в плане пересмотра; «было» печатается со своей меткой
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    stale = {
        str(row.canonical_uri): row
        for row in plan.rows
        if row.canonical_uri in (URL_FORBES, URL_KOMMERSANT)
    }
    assert set(stale) == {URL_FORBES, URL_KOMMERSANT}, plan.rows
    assert stale[URL_FORBES].was_method == V3_VALUE_METHOD, stale
    assert stale[URL_KOMMERSANT].was_method == V4_VALUE_METHOD, stale
    assert all(
        row.was_status == f"derivative → Банк России ({row.was_method})" for row in stale.values()
    ), stale
    # страницы подставки чисты: новые признаки вето на них не сработали, решение то же — пересмотренное
    assert all(
        row.action == "будет записано прежнее происхождение значения улики" for row in stale.values()
    ), stale
    assert plan.dry_run and plan.changed_evidence == 0 and not plan.changed_source_ids, plan.rows

    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 2, report.rows
    written_after = {
        str(r["canonical_uri"]): dict(r["origin"])
        for r in await _records_of(scratch_url, STATEMENT_145)
        if r["origin"] is not None
    }
    assert set(written_after) == {URL_FORBES, URL_KOMMERSANT}, written_after
    assert all(rec["method"] == CURRENT_VALUE_METHOD for rec in written_after.values()), written_after
    assert all(rec["primary_key"] == "cbr" for rec in written_after.values()), written_after
    assert all(rec["pairing"] == "publication_window" for rec in written_after.values()), written_after
    assert len(await _group_ids(scratch_url, STATEMENT_145)) == 1
    assert await _current_assessment(scratch_url, STATEMENT_145) == ("E1", "hypothesis")

    # повторный прогон идемпотентен: ни v3-, ни v4-метка больше не считается устаревшей
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    retouched = [row for row in again.rows if row.canonical_uri in (URL_FORBES, URL_KOMMERSANT)]
    assert len(retouched) == 2, again.rows
    assert all(row.was_method == CURRENT_VALUE_METHOD for row in retouched), retouched
    assert all(row.action == "изменений нет: решение уже записано ранее" for row in retouched), retouched
