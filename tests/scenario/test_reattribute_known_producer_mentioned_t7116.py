"""Scenario (DB): replay переатрибуцией стендового дефекта T7.116 (часть а, ADR-0029 дополнение).

Стенд (сессия 53be7fd6): утверждение c6729d4c «Прогноз Банка России по годовой инфляции в
России на 2026 год составляет 6,0–7,0%». Улика делового издания (interfax-форма: «превысила
прогноз ЦБ … (6-7%)», «ЦБ … подтвердил свой прогноз … на уровне 6-7%») записана строгим
режимом (derivative → Банк России). Пересказ той же формулировки на ленточном издании не
содержит ни шаблона атрибуции, ни глагола сообщения: прежний метод значений отказывал,
пересказ оставался независимой группой — E3 «Проверено» в третий раз за сессию.

Что закреплено прогоном переатрибуции (`reattribute_evidence_window`, сеть — FakeFetchClient):

1. первый прогон пишет строгие решения; новый режим догоняет следующим прогоном (знание
   собирается из записей, а не из планов);
2. второй прогон видит известного производителя по другой улике того же утверждения и пишет
   запись pairing `known_producer_mentioned` — той же формой, что строгий режим (тот же
   родитель-якорь), без нового ключа scope; вторая страница сворачивается в ту же группу
   независимости;
3. повторный прогон идемпотентен: запись текущего метода не пересматривается;
4. гвард «без цепочек»: сборщики ключей (`reattribution._known_claim_primaries` и сборка
   оркестратора) читают pairing строгого режима и режима публикации — запись нового режима
   knowledge НЕ пополняет (посаженная запись + контрольная посадка тем же pairing строгого
   режима доказывают, что исключение идёт от pairing, а не от совпадения ключей);
5. собственная публикация новым режимом не порождается (страница не домашняя для X).

Сеть подменена (§6); staging, `MemoryService`, группировка и правила — настоящие. Прямых
UPDATE доменных таблиц нет; посаженные улики — INSERT (тот же посев, что в T7.107). Страницы
сидируются на заведомо разных доменах (ловушка T7.75).
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
CURRENT_VALUE_METHOD = "host-value-attribution-v17"
VALUE_SCHEMA = "host-value-attribution-v1"
PAIRING_STRICT = "value_and_primary_in_fragment"
PAIRING_NEW = "known_producer_mentioned"

NOT_COLLECTED = "изменений нет: атрибуция значения не собрана"
DECIDED = "изменений нет: решение уже записано ранее"
WILL_WRITE = "будет записано происхождение значения улики"

URL_IFX = "https://interfax.example/business/1121468"
URL_LENTA = "https://lenta.example/news/2026-10-08/sprognozirovano"
URL_CHAIN_PORTAL = "https://portal.example/inflyaciya-2027"
URL_POISON = "https://venom.example/otzyvy-2027"

IFX_PAGE = [
    "Годовая инфляция в России на 5 октября превысила прогноз ЦБ на 2026 год (6-7%).",
    "ЦБ 11 сентября подтвердил свой прогноз по инфляции в 2026 году на уровне 6-7%.",
    "Регулятор отдельно сообщил о проинфляционных рисках и жесткости денежной политики.",
]
LENTA_PAGE = [
    "Спрогнозировано решение Банка России по инфляции",
    "Инвестбанкир Коган: ЦБ, вероятно, повысит прогноз по инфляции до 7-7,5 %",
    (
        "На фоне роста инфляции, годовые показатели которой превысили 7 процентов, "
        "выйдя за пределы запланированного Банком России коридора в 6-7 процентов, "
        "регулятор, вероятно, повысит прогноз темпов роста цен, считает инвестбанкир "
        "Евгений Коган."
    ),
    "Он предположил, что прогноз будет поднят до 7,0-7,5 процента.",
]
#: страница пересказа для гварда «без цепочек»: значение есть, словарный алиас назван,
#: собственной пары нет — приписывать можно только по УЖЕ записанному знанию о производителе
CHAIN_PAGE = [
    "Сборы налогов в Тармении за 2027 год выросли на 8,4% по сводке отраслевого союза.",
    "Ранее об этом говорил Росстат в своём обзоре фискальной статистики.",
]

STATEMENT_CBR = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STATEMENT_CHAIN = "Сборы налогов в Тармении за 2027 год выросли на 8,4%."


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.107)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        pages = {URL_IFX: IFX_PAGE, URL_LENTA: LENTA_PAGE, URL_CHAIN_PORTAL: CHAIN_PAGE}
        paragraphs = "".join(f"<p>{line}</p>" for line in pages[url] if line.strip())
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


async def _known_keys_orchestrator(
    scratch_url: str, claim_id: uuid.UUID, tmp_path: Path
) -> frozenset[str]:
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


async def _plant(
    scratch_url: str, *, claim_id: uuid.UUID, uri: str, tag: str, pairing: str, primary_key: str
) -> None:
    """Посадка улики с записью об атрибуции (INSERT вместо UPDATE, посев T7.107/T7.101)."""

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO sources (id, source_type, canonical_uri, content_hash, metadata) "
                    "VALUES (:id, 'external_url', :uri, :ch, '{}')"
                ),
                {"id": str(uuid.uuid4()), "uri": uri, "ch": f"poison-hash-{tag}"},
            )
            source_id = (
                await conn.execute(text("SELECT id FROM sources WHERE canonical_uri = :uri"), {"uri": uri})
            ).scalar_one()
            await conn.execute(
                text(
                    "INSERT INTO evidence (id, claim_id, relation, evidence_kind, identity_hash, "
                    "scope, source_id, chunk_id) "
                    "VALUES (:id, :c, 'supports', 'source_assertion', :h, CAST(:sc AS JSONB), :s, :k)"
                ),
                {
                    "id": str(uuid.uuid4()),
                    "c": str(claim_id),
                    "h": f"identity-poison-{tag}",
                    "sc": json.dumps(
                        {
                            "scope_schema": "host-scope-v1",
                            "value_attribution": {
                                "schema": VALUE_SCHEMA,
                                "primary_key": primary_key,
                                "primary_name": "Росстат",
                                "primary_uri": "https://rosstat.gov.ru/",
                                "parent_source_id": str(uuid.uuid4()),
                                "method": CURRENT_VALUE_METHOD,
                                "basis_fragment": "говорил Росстат в своём обзоре",
                                "pairing": pairing,
                            },
                        },
                        ensure_ascii=False,
                    ),
                    "s": str(source_id),
                    "k": f"chunk-poison-{tag}",
                },
            )
    finally:
        await engine.dispose()


# ─── т8: стендовый случай закрыт переатрибуцией ─────────────────────────────────


@pytest.mark.asyncio
async def test_known_producer_mentioned_mode_attributes_the_retelling_page(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    await _seed_claim(
        scratch_url,
        store,
        (URL_IFX, URL_LENTA),
        STATEMENT_CBR,
        {URL_IFX: IFX_PAGE, URL_LENTA: LENTA_PAGE},
    )
    await _seed_claim(
        scratch_url, store, (URL_CHAIN_PORTAL,), STATEMENT_CHAIN, {URL_CHAIN_PORTAL: CHAIN_PAGE}
    )

    # стартовое состояние: пересказ — отдельная группа независимости, записей нет
    assert len(await _group_ids(scratch_url, STATEMENT_CBR)) == 2
    assert await _record_of(scratch_url, STATEMENT_CBR, URL_IFX) is None
    assert await _record_of(scratch_url, STATEMENT_CBR, URL_LENTA) is None

    # ── прогон 1: пишутся только строгие решения; пересказ ещё не приписан ──
    first = await _run_backfill(scratch_url, store)
    assert first.changed_evidence == 1, first.rows  # только деловое издание ( → Банк России )
    strict_row = next(r for r in first.rows if r.canonical_uri == URL_IFX and r.claim_statement == STATEMENT_CBR)
    assert strict_row.now_status.startswith("derivative"), strict_row
    assert strict_row.primary_name == "Банк России", strict_row
    ifx_record = await _record_of(scratch_url, STATEMENT_CBR, URL_IFX)
    assert ifx_record is not None and ifx_record["primary_key"] == "cbr", ifx_record
    assert ifx_record["pairing"] == PAIRING_STRICT, ifx_record
    assert ifx_record["method"] == CURRENT_VALUE_METHOD, ifx_record
    anchor_cbr = str(ifx_record["parent_source_id"])
    lenta_row = next(r for r in first.rows if r.canonical_uri == URL_LENTA and r.claim_statement == STATEMENT_CBR)
    assert lenta_row.action == NOT_COLLECTED, lenta_row
    assert await _record_of(scratch_url, STATEMENT_CBR, URL_LENTA) is None

    # ── прогон 2: производитель утверждения уже записан другой уликой ──
    plan = await _run_backfill(scratch_url, store, dry_run=True)
    lenta_plan = next(r for r in plan.rows if r.canonical_uri == URL_LENTA and r.claim_statement == STATEMENT_CBR)
    assert lenta_plan.now_status == "derivative", lenta_plan
    assert lenta_plan.primary_name == "Банк России", lenta_plan
    assert lenta_plan.action == WILL_WRITE, lenta_plan

    sources_before = await _scalar(scratch_url, "SELECT count(*) FROM sources")
    second = await _run_backfill(scratch_url, store)
    assert second.changed_evidence == 1, second.rows
    assert second.jobs_created >= 1 and second.worker is not None, second.worker
    lenta_record = await _record_of(scratch_url, STATEMENT_CBR, URL_LENTA)
    assert lenta_record is not None, "пересказ не приписан известному производителю"
    assert lenta_record["schema"] == VALUE_SCHEMA, lenta_record
    assert lenta_record["primary_key"] == "cbr", lenta_record
    assert lenta_record["method"] == CURRENT_VALUE_METHOD, lenta_record
    # форма записи строгого режима: родитель-якорь тот же, pairing — новый
    assert lenta_record["pairing"] == PAIRING_NEW, lenta_record
    assert str(lenta_record["parent_source_id"]) == anchor_cbr, (lenta_record, ifx_record)
    assert "Банка России" in lenta_record["basis_fragment"], lenta_record
    assert await _scalar(scratch_url, "SELECT count(*) FROM sources") == sources_before, "появился новый якорь"

    # пересказ свернулся в ту же группу независимости: одна группа вместо двух
    assert len(await _group_ids(scratch_url, STATEMENT_CBR)) == 1

    # собственная публикация новым режимом не порождается (страница не домашняя для Банка России)
    self_rows = await _all(
        scratch_url,
        "SELECT count(*) AS n FROM evidence e WHERE e.scope ? 'value_self_publication'",
    )
    assert int(self_rows[0]["n"]) == 0, self_rows

    # ── прогон 3: идемпотентность ──
    again = await _run_backfill(scratch_url, store)
    assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
    row = next(r for r in again.rows if r.canonical_uri == URL_LENTA and r.claim_statement == STATEMENT_CBR)
    assert row.action == DECIDED and row.was_method == CURRENT_VALUE_METHOD, row
    assert (again.affected_claims, again.invalidated_heads, again.jobs_created) == (0, 0, 0)

    # ── т7 (гвард «без цепочек»): сборщики обоих вызывающих читают pairing строгого
    #    режима и режима публикации, запись нового режима знание не пополняет ──
    claim_chain = await _claim_id(scratch_url, STATEMENT_CBR)
    assert "cbr" in await _known_keys_reattribution(scratch_url, claim_chain)
    assert await _known_keys_orchestrator(scratch_url, claim_chain, tmp_path) == frozenset({"cbr"})

    await _plant(
        scratch_url,
        claim_id=await _claim_id(scratch_url, STATEMENT_CHAIN),
        uri=URL_POISON,
        tag="new",
        pairing=PAIRING_NEW,
        primary_key="rosstat",
    )
    # изолированная посадка нового режима: сборщик пуст (исключение идёт от pairing)
    claim_new_only = await _claim_id(scratch_url, STATEMENT_CHAIN)
    assert await _known_keys_reattribution(scratch_url, claim_new_only) == frozenset()
    assert await _known_keys_orchestrator(scratch_url, claim_new_only, tmp_path) == frozenset()

    # страница пересказа того же утверждения при этом остаётся честным отказом:
    # запись нового режима не приписала ей Росстата (посаженная улика вне окна чтения,
    # а из записанного нового pairing знание не собирается)
    poisoned_plan = await _run_backfill(scratch_url, store, dry_run=True)
    chain_rows = {r.canonical_uri: r for r in poisoned_plan.rows if r.claim_statement == STATEMENT_CHAIN}
    assert chain_rows[URL_CHAIN_PORTAL].action == NOT_COLLECTED, chain_rows
    assert poisoned_plan.changed_evidence == 0, poisoned_plan.rows
    assert await _record_of(scratch_url, STATEMENT_CHAIN, URL_CHAIN_PORTAL) is None

    # контрольная посадка того же производителя pairing строгого режима: тот же сборщик
    # ключ видит — значит исключение вызвано pairing, а не совпадением ключей
    await _plant(
        scratch_url,
        claim_id=claim_new_only,
        uri="https://mirror.example/otzyvy-2027b",
        tag="strict",
        pairing=PAIRING_STRICT,
        primary_key="rosstat",
    )
    assert await _known_keys_reattribution(scratch_url, claim_new_only) == frozenset({"rosstat"})
    assert await _known_keys_orchestrator(scratch_url, claim_new_only, tmp_path) == frozenset({"rosstat"})
