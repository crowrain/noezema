"""Scenario (DB): приоритет записи значения над страничным указателем при группировке
независимости (T7.117, решение пользователя 2026-10-15 «вариант 1», ADR-0029 §22).

Реплей стендового случая c6729d4c в миниатюре: страница Банка России (домашний хост,
собственная публикация прогноза 6,0–7,0%), деловое издание (строгая атрибуция «ЦБ … подтвердил
свой прогноз … 6-7%») и ленточное издание-пересказ, у страницы которого страничный указатель
ведёт к Минфину (шаблонная фраза «Как следует из проектировок Минфина … 6,8 процента»), а
запись значения, собранная переатрибуцией (T7.116, pairing `known_producer_mentioned`),
называет Банк России — того же производителя, что и первая улика.

Было (правило T7.75/T7.78, страничный указатель старший): ДВЕ группы независимости —
{cbr.ru, interfax} и {lenta} (через Минфин-якорь) — E3 «supported»: тот самый стендовый дефект
витрины T7.116, третий пересказ одного прогноза выглядит независимым свидетельством.
Стало (T7.117): ОДНА группа — обе записи значения ведут к одному родителю (прочитанная
страница ЦБ), страничный указатель остаётся фолбэком и не участвует, когда запись есть:
закреплено фактическим прогоном — группы, оценка правил (E-уровень/эпистемический статус) и
основание участника `parent:<short12>` ведут к родителю по ЗАПИСИ, а не по указателю.

Заодно закреплено неизменное: строгая атрибуция interfax (указатель и запись называют один и
тот же источник — т6) не меняется; собственная публикация ЦБ без указателя и без записи
значения (т3 форма) не меняется; идемпотентность переатрибуции (третий прогон — «решение уже
записано ранее», ноль новых задач переоценки).

Сеть подменена (§6); staging, `MemoryService`, детекторы, группировка и правила — настоящие.
Прямых UPDATE доменных таблиц нет.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from packages.artifacts.store import FilesystemArtifactStore
from tests.scenario.test_research_evidence import _all, _scalar

pytestmark = [pytest.mark.scenario]

URL_CBR = "https://cbr.ru/structure/cdb/pub/cpd_2026-7/"
URL_IFX = "https://interfax.example/business/1121468"
URL_LENTA = "https://lenta.example/news/2026-10-08/sprognozirovano"

CBR_PAGE = [
    "Документальный прогноз годовой инфляции в России на 2026 год",
    "Банк России опубликовал обновлённый документальный прогноз: годовая инфляция в России "
    "в 2026 году составит 6,0–7,0%.",
    "Дальнейшая динамика инфляции будет сопровождаться жёсткими условиями денежной политики.",
]
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
    "Как следует из проектировок Минфина, инфляцию в 2026 году ожидают на уровне 6,8 процента.",
]

STATEMENT_CBR = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."

NOT_COLLECTED = "изменений нет: атрибуция значения не собрана"
DECIDED = "изменений нет: решение уже записано ранее"
WILL_WRITE = "будет записано происхождение значения улики"

PAIRING_STRICT = "value_and_primary_in_fragment"
PAIRING_KNOWN = "known_producer_mentioned"


class FakeFetchClient:
    """Детерминированная подмена FetchClient (тот же контракт, что в репродюсерах T7.107/T7.116)."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        pages = {URL_CBR: CBR_PAGE, URL_IFX: IFX_PAGE, URL_LENTA: LENTA_PAGE}
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


def _records(envelopes: list[dict[str, Any]], pages: dict[str, list[str]]) -> list[Any]:
    from apps.orchestrator.evidence import observation_to_evidence
    from tests.scenario.test_research_evidence import _research_obs

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
    scratch_url: str, store: FilesystemArtifactStore
) -> tuple[uuid.UUID, dict[str, uuid.UUID]]:
    """Fetch cbr.ru → interfax → lenta (порядок важен: указатель interfax резолвится на
    УЖЕ прочитанную страницу ЦБ, а не на якорь) и обычный commit-граница T7.59."""

    from apps.research_proxy.service import ResearchProxyService
    from tests.scenario.test_research_evidence import _apply, _section, _set_section

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        envelopes = [dict(await service.fetch(url)) for url in (URL_CBR, URL_IFX, URL_LENTA)]
    finally:
        await engine.dispose()
    pages = {URL_CBR: CBR_PAGE, URL_IFX: IFX_PAGE, URL_LENTA: LENTA_PAGE}
    source_ids = {str(env["final_url"]): uuid.UUID(str(env["source_id"])) for env in envelopes}
    await _apply(scratch_url, _records(envelopes, pages), STATEMENT_CBR)
    row = await _scalar(
        scratch_url,
        "SELECT id::text AS id FROM claims WHERE statement = " + _quoted(STATEMENT_CBR),
    )
    assert row is not None, "утверждение не записано"
    return uuid.UUID(str(row[0])), source_ids


async def _run_backfill(scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool) -> Any:
    from apps.research_proxy.reattribution import reattribute_evidence_window

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        return await reattribute_evidence_window(
            factory, store, since=datetime(2000, 1, 1, tzinfo=UTC), dry_run=dry_run
        )
    finally:
        await engine.dispose()


async def _groups_of_claim(scratch_url: str) -> list[dict[str, str]]:
    """Участники текущей группы независимости утверждения: source_id → (uri, group, basis)."""

    return [
        {str(k): str(v) for k, v in dict(r).items()}
        for r in await _all(
            scratch_url,
            "SELECT m.source_id::text AS sid, src.canonical_uri AS uri, m.group_id AS grp, m.basis "
            "FROM source_independence_members m "
            "JOIN sources src ON src.id = m.source_id "
            "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
            "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
            "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
            "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(STATEMENT_CBR),
        )
    ]


async def _group_count(scratch_url: str) -> int:
    return len({row["grp"] for row in await _groups_of_claim(scratch_url)})


async def _assessed(scratch_url: str) -> tuple[str, str, float]:
    row = await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status, a.confidence "
        "FROM claim_assessment_heads h JOIN claim_assessments a ON a.id = h.current_assessment_id "
        "JOIN claims c ON c.id = h.claim_id "
        "WHERE h.assessment_state = 'current' AND c.statement = " + _quoted(STATEMENT_CBR),
    )
    assert row is not None
    return str(row[0]), str(row[1]), float(row[2])


async def _record_of(scratch_url: str, source_id: uuid.UUID) -> dict[str, Any] | None:
    row = await _scalar(
        scratch_url,
        "SELECT scope -> 'value_attribution' AS origin FROM evidence WHERE source_id = :s",
        {"s": str(source_id)},
    )
    assert row is not None, f"улики источника {source_id} нет"
    return None if row[0] is None else dict(row[0])


async def _pointer_of(scratch_url: str, source_id: uuid.UUID) -> str | None:
    row = await _scalar(
        scratch_url, "SELECT parent_source_id::text AS p FROM sources WHERE id = :s", {"s": str(source_id)}
    )
    assert row is not None
    return None if row[0] is None else str(row[0])


def _row_for(rows: list[dict[str, str]], needle: str) -> dict[str, str]:
    hits = [row for row in rows if needle in row["uri"]]
    assert len(hits) == 1, f"{needle}: {hits}"
    return hits[0]


@pytest.mark.asyncio
async def test_value_record_now_groups_ahead_of_the_page_pointer(
    migrated_db: tuple[str, Any], tmp_path: Path, live_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")

    claim_id, src = await _seed_claim(scratch_url, store)
    assert str(claim_id)
    cbr_id, ifx_id, lenta_id = src[URL_CBR], src[URL_IFX], src[URL_LENTA]

    # ─── было: указатель interfax ведёт к прочитанной странице ЦБ; лента отделена
    #     (указателем на Минфин-якорь либо честным отказом детектора — обе формы дают
    #     одну и ту же прежнюю картину: две группы, E3) ─────────────────────────────
    ifx_pointer = await _pointer_of(scratch_url, ifx_id)
    assert ifx_pointer == str(cbr_id), "указатель interfax должен вести к прочитанной странице ЦБ"
    lenta_pointer = await _pointer_of(scratch_url, lenta_id)
    assert lenta_pointer is not None, "страница ленты должна быть помечена Минфином (якорь-узел)"
    assert lenta_pointer != str(cbr_id)

    groups_before_map = {row["sid"]: row["grp"] for row in await _groups_of_claim(scratch_url)}
    assert len(set(groups_before_map.values())) == 2, groups_before_map
    assert groups_before_map[str(ifx_id)] == groups_before_map[str(cbr_id)]
    assert groups_before_map[str(lenta_id)] != groups_before_map[str(cbr_id)]
    grade_before, status_before, conf_before = await _assessed(scratch_url)
    assert (grade_before, status_before) == ("E3", "supported"), (grade_before, status_before)
    lenta_basis_before = _row_for(await _groups_of_claim(scratch_url), "lenta.example")["basis"]

    # ─── прогон 1: строгая атрибуция interfax записана; лента — не собрана ──────────
    plan1 = await _run_backfill(scratch_url, store, dry_run=True)
    actions1 = {r.canonical_uri: r.action for r in plan1.rows}
    ifx1 = next(a for uri, a in actions1.items() if "interfax" in uri)
    lenta1 = next(a for uri, a in actions1.items() if "lenta" in uri)
    assert ifx1 == WILL_WRITE, ifx1
    assert lenta1 == NOT_COLLECTED, lenta1
    apply1 = await _run_backfill(scratch_url, store, dry_run=False)
    assert apply1.changed_evidence >= 1
    record_ifx = await _record_of(scratch_url, ifx_id)
    assert record_ifx is not None and record_ifx["pairing"] == PAIRING_STRICT
    assert str(record_ifx["parent_source_id"]) == str(cbr_id)
    assert await _record_of(scratch_url, lenta_id) is None
    # группировка ещё не менялась: запись interfax называет того же родителя, что и её указатель
    assert await _group_count(scratch_url) == 2
    assert (await _assessed(scratch_url))[:2] == (grade_before, status_before)

    # ─── прогон 2: известный производитель по другой улике того же утверждения ──────
    plan2 = await _run_backfill(scratch_url, store, dry_run=True)
    actions2 = {r.canonical_uri: r.action for r in plan2.rows}
    lenta2 = next(a for uri, a in actions2.items() if "lenta" in uri)
    assert lenta2 == WILL_WRITE, lenta2
    apply2 = await _run_backfill(scratch_url, store, dry_run=False)
    assert apply2.changed_evidence >= 1

    record_lenta = await _record_of(scratch_url, lenta_id)
    assert record_lenta is not None and record_lenta["pairing"] == PAIRING_KNOWN
    parent_lenta = str(record_lenta["parent_source_id"])
    assert parent_lenta == str(cbr_id), "запись значения обязана указать прочитанную страницу ЦБ"
    # страничный указатель строки ленты НЕ тронут: он живёт в sources, запись — на улике
    assert await _pointer_of(scratch_url, lenta_id) == lenta_pointer != parent_lenta

    # ─── стало: во вход группировщика идёт родитель ПО ЗАПИСИ (T7.117) ──────────────
    rows_after = await _groups_of_claim(scratch_url)
    assert len({row["grp"] for row in rows_after}) == 1, f"запись значения обязана свернуть группы: {rows_after}"
    lenta_after = _row_for(rows_after, "lenta.example")
    assert lenta_after["basis"] == f"parent:{parent_lenta[:12]}", lenta_after["basis"]
    assert lenta_after["basis"] != lenta_basis_before
    grade_after, status_after, conf_after = await _assessed(scratch_url)
    # фактическая оценка правил тестового снапшота: одна группа ниже порога
    # минимального числа независимых групп — E1 «hypothesis» (тот же итог, что T7.78/T7.98)
    assert (grade_after, status_after) == ("E1", "hypothesis"), (grade_after, status_after)
    assert conf_after < conf_before

    # ─── прогон 3: идемпотентность; оценка уже по новому правилу ────────────────────
    plan3 = await _run_backfill(scratch_url, store, dry_run=True)
    actions3 = {r.canonical_uri: r.action for r in plan3.rows}
    lenta3 = next(a for uri, a in actions3.items() if "lenta" in uri)
    assert lenta3 == DECIDED, lenta3
    apply3 = await _run_backfill(scratch_url, store, dry_run=False)
    assert apply3.changed_evidence == 0 and apply3.jobs_created == 0
    assert await _group_count(scratch_url) == 1

    # т6: interfax — указатель и запись называют ОДИН источник: поведение не изменилось
    assert await _pointer_of(scratch_url, ifx_id) == str(cbr_id)
    record_ifx_after = await _record_of(scratch_url, ifx_id)
    assert str(record_ifx_after["parent_source_id"]) == str(cbr_id)

    # т3-форма: собственная публикация ЦБ — ни указателя, ни записи значения; группа не менялась
    assert await _pointer_of(scratch_url, cbr_id) is None
    assert await _record_of(scratch_url, cbr_id) is None
