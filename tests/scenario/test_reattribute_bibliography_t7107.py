"""Scenario (DB): replay переатрибуцией стендового дефекта T7.107 (ADR-0029 дополнение).

Стенд: утверждение «безработица 2,2%» (7cfbe942) — пересказ на блог-площадке с шаблонной плашкой
«Данная публикация является личным мнением автора…» и строкой «Источники: Росстат и данные портала
HeadHunter.». Прежний метод значений не мог приписать её Росстату: плашка вызывала `own_assessment`
на пустом месте, строку-список не читал ни строгий режим, ни режим публикации — хотя ДРУГАЯ улика
того же утверждения (деловое издание) уже была приписана Росстату строгим режимом.

Что закреплено прогоном переатрибуции (`reattribute_evidence_window`, сеть — FakeFetchClient):

1. первый прогон пишет только строгие решения (новая запись другой улики ещё не в БД — третий
   режим догоняет следующим прогоном: знание собирается из записей, а не из планов);
2. второй прогон видит известный производитель по другой улике того же утверждения и пишет запись
   pairing `bibliography_known_producer` (тот же родитель-якорь, тот же каскад §11.3: вторая
   страница сворачивается в ту же группу независимости, оценка падает E3→E1);
3. повторный прогон идемпотентен: запись текущего метода не пересматривается;
4. чужие утверждения той же страницы не затронуты (для них известного производителя нет —
   записи отсутствуют, оценки равны);
5. гвард «без цепочек»: записью нового pairing ключ производства НЕ пополняется — специально
   посаженная запись `bibliography_known_producer` на третью улику не даёт третьей странице
   приписать себе Росстата (сборщик ключей читает pairing строгого режима и режима публикации);
6. сборщики ключей обоих вызывающих (`reattribution._known_claim_primaries` и оркестратор)
   возвращают ровно ключи строгого/публикационного режимов.

Сеть подменена (§6); staging, `MemoryService`, группировка и правила — настоящие. Прямых UPDATE
доменных таблиц нет; посаженная улика — INSERT (тот же посев, что в T7.101).
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
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
CURRENT_VALUE_METHOD = "host-value-attribution-v7"
VALUE_SCHEMA = "host-value-attribution-v1"
PAIRING_STRICT = "value_and_primary_in_fragment"
PAIRING_BIBLIOGRAPHY = "bibliography_known_producer"

NOT_COLLECTED = "изменений нет: атрибуция значения не собрана"
DECIDED = "изменений нет: решение уже записано ранее"
WILL_WRITE = "будет записано происхождение значения улики"

PLAQUE = (
    "Данная публикация является личным мнением автора. "
    "Мнение владельца сайта может не совпадать с мнением автора."
)

URL_INTERFAX = "https://interfax.example/business/1119461"
URL_SMARTLAB = "https://smart-lab.example/blog/1349924"
URL_HH = "https://hh.example/analitika-truda"
URL_EXPERT_TAX = "https://expert.example/sbory-nalogov-2025"
URL_TAXBLOG = "https://smart-lab.example/blog/1400001"
URL_POISON = "https://venom.example/otzyvy-nalogi"

PAGES: dict[str, list[str]] = {
    URL_INTERFAX: [
        "Уровень безработицы в России опустился до 2,2%, следует из данных Росстата.",
        "Это минимальное значение за последние годы.",
    ],
    URL_SMARTLAB: [
        "Блог инвестора. О безработице.",
        "Раньше писал про уровень выше, сейчас он опустился до 2,2%.",
        "Настроения на рынке труда медленно улучшаются третий квартал подряд.",
        "Зарплаты растут быстрее цен, но не везде одинаково.",
        "Число резюме выросло на 13%, а вакансий — на 22%; резюме — 8,7 млн.",
        PLAQUE,
        "Источники: Росстат и данные портала HeadHunter.",
    ],
    URL_HH: [
        "Аналитика портала HeadHunter.",
        "Число резюме выросло на 13%, вакансий — на 22%.",
        "Общее число резюме — 8,7 млн единиц.",
    ],
    URL_EXPERT_TAX: [
        "Сборы налогов в 2025 году выросли на 8,4%, следует из данных ФНС.",
        "Ведомство объясняет рост изменениями администрирования.",
    ],
    URL_TAXBLOG: [
        "Блог инвестора. О налогах.",
        "Доходы бюджета растут несколько лет подряд.",
        "Бизнес обсуждает изменение ставок.",
        "Суммы сборов постоянно обновляются на сайте ведомства.",
        "За год сборы налогов выросли на 8,4%.",
        PLAQUE,
        "Источники: Росстат и данные портала HeadHunter.",
    ],
}

STATEMENT_22 = "Уровень безработицы в России опустился до 2,2%."
STATEMENT_RESUMES = "Число резюме на портале HeadHunter выросло на 13%, а вакансий — на 22%."
STATEMENT_TAX = "Сборы налогов в России выросли на 8,4%."


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.85)."""

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
) -> None:
    envelopes = await _seed(scratch_url, store, urls)
    await _apply(scratch_url, _records(envelopes), statement)


def _since() -> Any:
    return datetime.fromisoformat(SINCE_ISO.replace("Z", "+0:00")).replace(tzinfo=UTC)


async def _run_backfill(scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool = False) -> Any:
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


async def _known_keys_reattribution(scratch_url: str, claim_id: uuid.UUID) -> frozenset[str]:
    """Прямой вызов сборщика переатрибуции (его же SQL читает план)."""

    import apps.research_proxy.reattribution as reattr

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            return await reattr._known_claim_primaries(
                db, claim_id=claim_id, exclude_evidence_id=uuid.uuid4()
            )
    finally:
        await engine.dispose()


async def _known_keys_orchestrator(scratch_url: str, claim_id: uuid.UUID, tmp_path: Path) -> frozenset[str]:
    """Тот же предикат у оркестратора (сбор для записываемой улики без исключения)."""

    from apps.orchestrator.executor import StubToolExecutor
    from apps.orchestrator.orchestrator import Orchestrator
    from apps.research_proxy.service import ResearchProxyService

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        orchestrator = Orchestrator(
            session_factory=factory,
            gateway=None,  # тип не используется: метод читает только `db` и `claim_id`
            profile=None,
            executor=StubToolExecutor(tmp_path / "ws"),
            research_service=ResearchProxyService(factory, FilesystemArtifactStore(tmp_path / "art")),
        )
        async with factory() as db:
            return await orchestrator._known_claim_primaries(db, claim_id)
    finally:
        await engine.dispose()


# ─── главный прогон: стендовый дефект закрыт переатрибуцией ──────────────────────


@pytest.mark.asyncio
async def test_retelling_page_is_attributed_by_known_producer_of_the_same_claim(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(scratch_url, store, (URL_INTERFAX, URL_SMARTLAB), STATEMENT_22)
    await _seed_claim(scratch_url, store, (URL_HH, URL_SMARTLAB), STATEMENT_RESUMES)
    await _seed_claim(scratch_url, store, (URL_EXPERT_TAX, URL_TAXBLOG), STATEMENT_TAX)

    # ── прогон 1: пишутся только строгие решения; блог ещё не приписан никому ──
    first = await _run_backfill(scratch_url, store)
    assert first.changed_evidence == 2, first.rows  # interfax (Росстат) + expert (ФНС)
    strict = await _record_of(scratch_url, STATEMENT_22, URL_INTERFAX)
    assert strict is not None and strict["primary_key"] == "rosstat", strict
    assert strict["pairing"] == PAIRING_STRICT and strict["method"] == CURRENT_VALUE_METHOD, strict
    anchor_rosstat = str(strict["parent_source_id"])
    tax_strict = await _record_of(scratch_url, STATEMENT_TAX, URL_EXPERT_TAX)
    assert tax_strict is not None and tax_strict["primary_key"] == "fns", tax_strict

    by_url = {row.canonical_uri: row for row in first.rows if row.claim_statement == STATEMENT_22}
    blog_row = by_url[URL_SMARTLAB]
    # плашка больше не `own_assessment`: честный отказ строгого режима без известного ключа
    assert blog_row.now_status == "no_value_attribution", blog_row
    assert blog_row.action == NOT_COLLECTED, blog_row
    assert await _record_of(scratch_url, STATEMENT_22, URL_SMARTLAB) is None

    # ── прогон 2: производитель утверждения уже записан другой уликой ──
    from apps.research_proxy.reattribution import NO_DECISION_YET

    plan = await _run_backfill(scratch_url, store, dry_run=True)
    rows_a = {row.canonical_uri: row for row in plan.rows if row.claim_statement == STATEMENT_22}
    blog_plan = rows_a[URL_SMARTLAB]
    assert blog_plan.was_status == NO_DECISION_YET, blog_plan
    assert blog_plan.now_status == "derivative", blog_plan
    assert blog_plan.primary_name == "Росстат", blog_plan
    assert blog_plan.action == WILL_WRITE, blog_plan
    assert rows_a[URL_INTERFAX].action == DECIDED, rows_a[URL_INTERFAX]
    # чужое утверждение той же страницы: известного производителя нет — не трогать
    rows_b = {row.canonical_uri: row for row in plan.rows if row.claim_statement == STATEMENT_RESUMES}
    assert len(rows_b) == 2 and all(row.action == NOT_COLLECTED for row in rows_b.values()), rows_b
    tax_plan = {row.canonical_uri: row for row in plan.rows if row.claim_statement == STATEMENT_TAX}
    assert len(tax_plan) == 2, tax_plan
    assert tax_plan[URL_TAXBLOG].action == NOT_COLLECTED, tax_plan  # назван не известный утверждению X

    sources_before = await _sources_count(scratch_url)
    report = await _run_backfill(scratch_url, store)
    assert report.changed_evidence == 1, report.rows
    record = await _record_of(scratch_url, STATEMENT_22, URL_SMARTLAB)
    assert record is not None, "запись нового pairing не появилась"
    assert record["schema"] == VALUE_SCHEMA, record
    assert record["pairing"] == PAIRING_BIBLIOGRAPHY, record
    assert record["method"] == CURRENT_VALUE_METHOD, record
    assert record["primary_key"] == "rosstat", record
    # тот же первоисточник → тот же родитель-якорь: второй якорь не заводится
    assert str(record["parent_source_id"]) == anchor_rosstat, record
    assert "Источники: Росстат" in record["basis_fragment"], record
    assert await _sources_count(scratch_url) == sources_before, "появился новый якорь"
    assert report.jobs_created >= 1 and report.worker is not None, report.worker

    # группировка: обе страницы — дети одного якоря Росстата, одна группа вместо двух
    assert len(await _group_ids(scratch_url, STATEMENT_22)) == 1
    assert await _current_assessment(scratch_url, STATEMENT_22) == ("E1", "hypothesis")

    # чужое утверждение той же страницы не затронуто: ни записи, ни просадки оценки
    assert await _record_of(scratch_url, STATEMENT_RESUMES, URL_SMARTLAB) is None
    assert await _record_of(scratch_url, STATEMENT_RESUMES, URL_HH) is None
    assert len(await _group_ids(scratch_url, STATEMENT_RESUMES)) == 2
    assert await _current_assessment(scratch_url, STATEMENT_RESUMES) == ("E3", "supported")

    # запись о собственной публикации плашка не порождает
    self_rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM evidence e WHERE e.scope ? 'value_self_publication'",
    )
    assert int(self_rows[0]["n"]) == 0, self_rows

    # ── прогон 3: идемпотентность ──
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    row = next(
        r for r in again.rows if r.canonical_uri == URL_SMARTLAB and r.claim_statement == STATEMENT_22
    )
    assert row.action == DECIDED and row.was_method == CURRENT_VALUE_METHOD, row
    assert (again.affected_claims, again.invalidated_heads, again.jobs_created) == (0, 0, 0)

    # ── гвард «без цепочек»: посаженная запись bibliography_known_producer не кормит сборку ──
    claim_tax = await _claim_id(scratch_url, STATEMENT_TAX)
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata) "
                    "VALUES (:id, 'external_url', :uri, 'poison-hash-0000', '{}')"
                ),
                {"id": str(uuid.uuid4()), "uri": URL_POISON},
            )
            poison_source = (
                await conn.execute(
                    text("SELECT id FROM sources WHERE canonical_uri = :uri"), {"uri": URL_POISON}
                )
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
                    "scope, source_id, chunk_id) "
                    "VALUES (:id, :c, 'supports', 'source_assertion', :h, CAST(:sc AS JSONB), :s, 'chunk-0')"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "c": str(claim_tax),
                    "h": "identity-poison-0001",
                    "sc": json.dumps(
                        {
                            "scope_schema": "host-scope-v1",
                            "value_attribution": {
                                "schema": VALUE_SCHEMA,
                                "primary_key": "rosstat",
                                "primary_name": "Росстат",
                                "primary_uri": "https://rosstat.gov.ru/",
                                "parent_source_id": anchor_rosstat,
                                "method": CURRENT_VALUE_METHOD,
                                "basis_fragment": "Источники: Росстат и данные портала HeadHunter.",
                                "pairing": PAIRING_BIBLIOGRAPHY,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "s": str(poison_source),
                },
            )
    finally:
        await engine.dispose()

    # сборщики обоих вызывающих читают только строгий/публикационный pairing
    assert await _known_keys_reattribution(scratch_url, claim_tax) == frozenset({"fns"})
    assert await _known_keys_orchestrator(scratch_url, claim_tax, tmp_path) == frozenset({"fns"})
    assert await _known_keys_orchestrator(scratch_url, await _claim_id(scratch_url, STATEMENT_22), tmp_path) == (
        frozenset({"rosstat"})
    )

    # посадка не породила атрибуции: блог налогов остаётся честным отказом
    poisoned_plan = await _run_backfill(scratch_url, store, dry_run=True)
    tax_rows = {row.canonical_uri: row for row in poisoned_plan.rows if row.claim_statement == STATEMENT_TAX}
    # у посаженной улики нет журнала чтения — она вне окна переатрибуции («нечего проверять»);
    # её наличие меняет только сборку ключей, и она в ней не учтена (см. asserts выше)
    assert len(tax_rows) == 2 and URL_POISON not in tax_rows, tax_rows
    assert tax_rows[URL_TAXBLOG].action == NOT_COLLECTED, tax_rows[URL_TAXBLOG]
    assert poisoned_plan.changed_evidence == 0, poisoned_plan.rows
    assert await _record_of(scratch_url, STATEMENT_TAX, URL_TAXBLOG) is None
