"""Scenario (DB): T7.109 (часть в проверки т8) — переатрибуция видит равнозначность чисел
и оба конца диапазона (решение пользователя 2026-10-13).

Репродюсер состояния стендовой улики (interfax.ru/business/1121468): утверждение
«Прогноз Банка России по инфляции на 2026 год составляет 6,0–7,0%.» читалось с interfax-страницы
и со страницы самого ЦБ. Эпоха v5: детектор значений на interfax честно отказывался — диапазон
на странице («6-7%») даёт измеренное «7», а значение утверждения канонизировалось в «7.0»,
и реле на этом стыке молчало; записей о происхождении значения нет ни у одной улики.

Метод v8: хвостовые нули дробной части не значимы (7 ≡ 7,0), а оба конца диапазона —
значения утверждения (6 и 7). Переатрибуция `--evidence-level` (тот же предикат пересмотра,
что и всегда: «метки нет ИЛИ метка ≠ текущий метод») обязана увидеть перемену сама, без
изменения своего кода: улика с interfax получает происхождение значения — пересказ Банка
России: родитель записи — прочитанная страница Банка России того же хоста (parent → Банк
России); страничную разметку прогон не меняет (T7.98). Улика со страницы
самого ЦБ — прежний `self_primary` (T7.104): записывается публикация производителя.
Повторный прогон пуст: решения текущего метода уже записаны.

Состояние эпохи v5 моделируется настоящим путём: fetch со stub-детектором (журнал помечен v5,
разметки `derivative_of` нет), staging двух утверждений без записей о значениях.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.reattribution import reattribute_evidence_window
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

REPO_ROOT = Path(__file__).resolve().parents[2]
INTERFAX_TEXT = (REPO_ROOT / "tests/fixtures/t7109/interfax_1121468.txt").read_text(encoding="utf-8")

# собственная публикация ЦБ — стендовый синтаксис (тот же замер, что в T7.98/T7.104)
CBR_TEXT = (
    "Банк России повысил прогноз инфляции на 2026 год до 6,0–7,0%. "
    "Ранее регулятор ожидал рост цен на 4,5–5,5%."
)

URL_INTERFAX = "https://interfax.example/1121468"
URL_CBR = "https://www.cbr.ru/press_center/forecast-2026"

PAGES: dict[str, str] = {URL_INTERFAX: INTERFAX_TEXT, URL_CBR: CBR_TEXT}

STATEMENT_RANGE = "Прогноз Банка России по инфляции на 2026 год составляет 6,0–7,0%."


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
def v5_era_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fetch эпохи v5: обе страницы без решения детектора, журнал помечен v5."""

    import apps.research_proxy.service as svc

    def _stub(*, canonical_uri: str, text: str) -> AttributionDecision:
        return AttributionDecision(status=STATUS_NO_VALUE_ATTRIBUTION)

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)
    monkeypatch.setattr(svc, "ATTRIBUTION_METHOD_VERSION", "host-source-attribution-v5")
    monkeypatch.setattr(svc, "detect_source_attribution", _stub)


def _quoted(statement: str) -> str:
    return "'" + statement.replace("'", "''") + "'"


async def _source_row(scratch_url: str, uri: str) -> dict[str, Any]:
    row = await _scalar(
        scratch_url,
        "SELECT id::text AS id, parent_source_id::text AS parent, "
        "  metadata ? 'derivative_of' AS marked FROM sources WHERE canonical_uri = :u",
        {"u": uri},
    )
    assert row is not None, uri
    return {"id": str(row[0]), "parent": None if row[1] is None else str(row[1]), "marked": bool(row[2])}


async def _group_snapshot(scratch_url: str, statement: str) -> list[Any]:
    return await _all(
        scratch_url,
        "SELECT m.source_id, m.group_id, m.basis FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claim_assessment_heads h ON h.current_assessment_id = a.id AND h.assessment_state = 'current' "
        "JOIN claims c ON c.id = h.claim_id WHERE c.statement = " + _quoted(statement),
    )


async def _seed_v5_era(scratch_url: str, store: FilesystemArtifactStore) -> dict[str, dict[str, str]]:
    """Настоящий fetch-путь со stub-детектором v5 + staging утверждения без записей значений."""

    from apps.research_proxy.service import ResearchProxyService

    await _set_section(scratch_url, _section())
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        envelopes = {url: dict(await service.fetch(url)) for url in (URL_INTERFAX, URL_CBR)}
    finally:
        await engine.dispose()

    audit = await _all(
        scratch_url,
        "SELECT payload->>'attribution_method' AS method FROM audit_events "
        "WHERE type = 'research_fetch_completed' ORDER BY sequence",
    )
    assert [r["method"] for r in audit] == ["host-source-attribution-v5"] * 2, audit

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
        for env in (envelopes[URL_INTERFAX], envelopes[URL_CBR])
    ]
    await _apply(scratch_url, records, STATEMENT_RANGE)
    return envelopes


def _factory(scratch_url: str) -> tuple[async_sessionmaker[Any], Any]:
    engine = create_async_engine(scratch_url)
    return async_sessionmaker(engine, expire_on_commit=False), engine


@pytest.mark.asyncio
async def test_range_claim_evidence_gets_parent_after_v8_reattribution(
    migrated_db: tuple[str, Any], tmp_path: Path, v5_era_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _seed_v5_era(scratch_url, store)

    # состояние эпохи v5: ни у одной улики нет записей о происхождении значения,
    # обе страницы без указателя, две одиночные группы
    origins = await _all(
        scratch_url,
        "SELECT s.canonical_uri AS uri, e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(STATEMENT_RANGE),
    )
    assert len(origins) == 2 and all(r["origin"] is None for r in origins), origins
    assert (await _source_row(scratch_url, URL_INTERFAX))["parent"] is None
    assert {str(g["basis"]) for g in await _group_snapshot(scratch_url, STATEMENT_RANGE)} == {"single"}

    factory, engine = _factory(scratch_url)
    try:
        # dry-run: свежий метод v8 видит то, чего не видел v5
        plan = await reattribute_evidence_window(factory, store, since=SINCE, dry_run=True)
        rows = {r.canonical_uri: r for r in plan.rows if r.claim_statement == STATEMENT_RANGE}
        assert set(rows) == {URL_INTERFAX, URL_CBR}, plan.rows
        interfax_row = rows[URL_INTERFAX]
        assert interfax_row.action == "будет записано происхождение значения улики", plan.rows
        assert interfax_row.primary_name == "Банк России"
        assert interfax_row.now_status == "derivative"
        cbr_row = rows[URL_CBR]
        assert cbr_row.action == (
            "будет записано: собственная публикация производителя Банк России"
        ), plan.rows
        assert plan.changed_evidence == 0
    finally:
        await engine.dispose()

    factory, engine = _factory(scratch_url)
    try:
        report = await reattribute_evidence_window(factory, store, since=SINCE)
        assert report.changed_evidence == 2, report.rows
        assert report.jobs_created >= 1 and report.worker is not None
    finally:
        await engine.dispose()

    # т8 главное: улика с interfax получила происхождение значения — пересказ Банка России,
    # её родитель (в записи) — прочитанная страница того же хоста: `resolve_primary_source`
    # берёт стендовый cbr.ru-документ (якорь создавался бы, если бы страницы ЦБ в окне не было).
    # Страничной разметки evidence-уровень не пишет: `sources.parent_source_id` interfax
    # остаётся пустым (T7.98: переатрибуция указатели снимает, но не проставляет), группа
    # независимости строится по записям (`_value_attribution_parents`, source_graph.py).
    cbr_page = (await _source_row(scratch_url, URL_CBR))["id"]
    interfax_after = await _source_row(scratch_url, URL_INTERFAX)
    assert interfax_after["parent"] is None and not interfax_after["marked"]
    # страница самого ЦБ остаётся собой: склейки с самой собой нет, есть запись о публикации
    cbr_after = await _source_row(scratch_url, URL_CBR)
    assert cbr_after["parent"] is None and not cbr_after["marked"]

    origins = await _all(
        scratch_url,
        "SELECT s.canonical_uri AS uri, e.scope -> 'value_attribution' AS origin, "
        "e.scope -> 'value_self_publication' AS selfp FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(STATEMENT_RANGE),
    )
    by_uri = {str(r["uri"]): r for r in origins}
    record = dict(by_uri[URL_INTERFAX]["origin"])
    assert record["primary_key"] == "cbr" and record["primary_name"] == "Банк России"
    assert record["method"] == "host-value-attribution-v17"
    assert record["pairing"] == "value_and_primary_in_fragment"
    assert str(record["parent_source_id"]) == cbr_page
    assert "6-7" in record["basis_fragment"].lower()  # диапазон прочитан на странице целиком
    assert by_uri[URL_INTERFAX]["selfp"] is None

    self_record = dict(by_uri[URL_CBR]["selfp"])
    assert by_uri[URL_CBR]["origin"] is None
    assert self_record["primary_key"] == "cbr"
    assert self_record["method"] == "host-value-attribution-v17"

    # группа независимости: interfax теперь в группе прочитанной страницы ЦБ, страница ЦБ — одиночна
    groups = {str(g["basis"]) for g in await _group_snapshot(scratch_url, STATEMENT_RANGE)}
    assert groups == {f"parent:{cbr_page[:12]}", "single"}, groups

    factory, engine = _factory(scratch_url)
    try:
        again = await reattribute_evidence_window(factory, store, since=SINCE)
        assert again.changed_evidence == 0 and not again.changed_source_ids, again.rows
        actions = {r.canonical_uri: r.action for r in again.rows if r.claim_statement == STATEMENT_RANGE}
        assert actions[URL_INTERFAX] == "изменений нет: решение уже записано ранее", again.rows
        assert actions[URL_CBR] == "изменений нет: собственная публикация уже записана", again.rows
    finally:
        await engine.dispose()
