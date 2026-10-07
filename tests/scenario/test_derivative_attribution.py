"""Scenario (DB): пересказ релиза не даёт второй группы независимости (T7.75, ADR-0029 B).

Репродюсер стендового дефекта (claim `9266248e`, «инфляция 2025 = 5,59%»): три новости с разных
регистрируемых доменов пересказывают один релиз Росстата. До T7.75 каждая была отдельной группой
независимости, и утверждение получало E3 «подтверждено независимо». Здесь тот же путь — настоящий
`research.fetch` (сеть подменена детерминированным FakeFetchClient), настоящий staging и настоящий
rules engine, — но теперь:

- хост проставляет новой строке источника указатель происхождения (`sources.parent_source_id`): на
  уже прочитанную страницу первоисточника, а если первоисточник не прочитан — на декларированный
  якорь (строка без контента и без момента чтения);
- `group_source_graph` собирает все пересказы в ОДНУ группу с основанием `parent:<id>`; логика
  группировки не менялась — изменились только входные данные;
- rules engine (единственный производитель grade) даёт E1/hypothesis с причиной
  `insufficient_independence` вместо E3.

Противоположный случай проверен отдельно: независимая оценка, которая упоминает первоисточник и
спорит с ним, остаётся отдельной группой. И третий: повторный fetch уже прочитанной страницы ничего
задним числом не дописывает (идемпотентность T7.11 сохранена).
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.evidence import observation_to_evidence
from apps.research_proxy.fetch import FetchResult
from apps.web.reliability import describe_verification
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

RETAIL_A = (
    "Инфляция в России по итогам 2025 года составила 5,59%, сообщается в релизе Росстата. "
    "Релиз опубликован 16 января 2026 года."
)
RETAIL_B = (
    "По данным Росстата, потребительские цены в 2025 году выросли на 5,59%. "
    "Об этом говорится в опубликованных материалах ведомства."
)
RETAIL_C = (
    "Как сообщает Федеральная служба государственной статистики, "
    "годовая инфляция достигла 5,59% по итогам декабря."
)
INDEPENDENT_STUDY = (
    "Независимая оценка инфляции 2025 года: по нашей методике показатель составил 6,4% — "
    "в отличие от официальных данных Росстата, которые мы пересчитали по своей корзине."
)

URL_INTERFAX = "https://interfax.example/news/inflyaciya-2025"
URL_EXPERT = "https://expert.example/economics/inflyaciya"
URL_RIA = "https://ria.example/2026/01/inflyaciya"
URL_STUDY = "https://research.example.org/inflation-estimate"

PAGES: dict[str, str] = {
    URL_INTERFAX: RETAIL_A,
    URL_EXPERT: RETAIL_B,
    URL_RIA: RETAIL_C,
    URL_STUDY: INDEPENDENT_STUDY,
}

RETAIL_URLS = (URL_INTERFAX, URL_EXPERT, URL_RIA)

ANCHOR_SQL = (
    "SELECT id, canonical_uri, content_hash, retrieved_at FROM sources "
    "WHERE metadata->>'declared_primary_anchor' = 'true' ORDER BY id"
)


class FakeFetchClient:
    """Детерминированная подмена FetchClient: контракт тот же, сети нет (§6)."""

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
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeFetchClient)


def _quoted(statement: str) -> str:
    return "'" + statement.replace("'", "''") + "'"


async def _assessment(scratch_url: str, statement: str) -> Any:
    return await _scalar(
        scratch_url,
        "SELECT a.effective_grade, a.epistemic_status FROM claim_assessments a "
        "JOIN claims c ON c.id = a.claim_id WHERE c.statement = :s "
        "ORDER BY a.created_at DESC LIMIT 1",
        {"s": statement},
    )


async def _assessment_reasons(scratch_url: str, statement: str) -> list[str]:
    row = await _scalar(
        scratch_url,
        "SELECT a.payload->'reasons' FROM audit_events a WHERE a.type = 'claim_assessed' "
        "AND a.payload->>'claim_id' = (SELECT id::text FROM claims WHERE statement = :s) "
        "ORDER BY a.sequence DESC LIMIT 1",
        {"s": statement},
    )
    assert row is not None, "в журнале нет claim_assessed для этого утверждения"
    return list(row[0] or [])


async def _group_members(scratch_url: str, statement: str) -> list[Any]:
    """Члены снимка независимости текущей оценки утверждения (группа + основание)."""

    return await _all(
        scratch_url,
        "SELECT m.source_id, m.group_id, m.basis FROM source_independence_members m "
        "JOIN source_independence_snapshots s ON s.id = m.snapshot_id "
        "JOIN claim_assessments a ON a.source_independence_snapshot_id = s.id "
        "JOIN claims c ON c.id = a.claim_id WHERE c.statement = " + _quoted(statement),
    )


async def _fetch_envelopes(
    scratch_url: str, store: FilesystemArtifactStore, urls: tuple[str, ...]
) -> list[dict[str, Any]]:
    """Настоящий путь `research.fetch`, но с полным конвертом: в нём есть новые поля T7.75."""

    from apps.research_proxy.service import ResearchProxyService

    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    service = ResearchProxyService(factory, store)
    try:
        return [dict(await service.fetch(url)) for url in urls]
    finally:
        await engine.dispose()


def _records(sources: list[dict[str, Any]]) -> list[Any]:
    records = [
        observation_to_evidence(
            _research_obs(
                s["final_url"],
                str(s["source_id"]),
                str(s["original_sha256"]),
                str(s["normalized_sha256"]),
                normalized_text=PAGES[s["final_url"]],
            ),
            {"url": s["final_url"]},
        )
        for s in sources
    ]
    assert all(r is not None for r in records)
    return records


# ─── стендовый репродюсер: три пересказа = одна группа, не E3 ─────────────

STATEMENT_RETOLD = "Инфляция в России по итогам 2025 года составила 5,59%."


@pytest.mark.asyncio
async def test_three_retellings_of_one_release_form_one_group(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    sources = await _fetch_envelopes(scratch_url, store, RETAIL_URLS)
    assert len(sources) == 3

    # 1. каждая новость опознана как пересказ, указатель проставлен каждой новой строке
    for env in sources:
        assert env["attribution_status"] == "derivative", env
        assert env["derivative_of"] is not None
        assert env["derivative_of"]["name"] == "Росстат"

    anchors = await _all(scratch_url, ANCHOR_SQL)
    assert len(anchors) == 1, "якорь первоисточника создаётся один раз на первоисточник"
    anchor = anchors[0]
    assert anchor["canonical_uri"] == "https://rosstat.gov.ru/"
    # честная запись: ни прочитанного контента, ни момента чтения у неё нет
    assert anchor["content_hash"] is None
    assert anchor["retrieved_at"] is None

    parents = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id FROM sources WHERE canonical_uri IN "
        f"({_quoted(URL_INTERFAX)}, {_quoted(URL_EXPERT)}, {_quoted(URL_RIA)}) ORDER BY canonical_uri",
    )
    assert len(parents) == 3
    assert {str(p["parent_source_id"]) for p in parents} == {str(anchor["id"])}

    # основание решения сохранено в provenance источника
    bases = await _all(
        scratch_url,
        "SELECT metadata->'derivative_of' AS d FROM sources WHERE parent_source_id IS NOT NULL",
    )
    assert len(bases) == 3
    for row in bases:
        record = dict(row["d"])
        assert record["key"] == "rosstat"
        assert record["name"] == "Росстат"
        assert record["method"] == "host-source-attribution-v1"
        assert "Росстат" in record["basis_fragment"] or "государственной статистики" in record[
            "basis_fragment"
        ]

    # 2. журнал: событие существующее, новые поля объясняют решение
    audit = await _scalar(
        scratch_url,
        "SELECT payload FROM audit_events WHERE type = 'research_fetch_completed' "
        "ORDER BY sequence DESC LIMIT 1",
    )
    assert audit is not None
    payload = dict(audit[0])
    assert payload["attribution_status"] == "derivative"
    assert payload["derivativity_written"] is True
    assert payload["derivative_of"]["name"] == "Росстат"

    # 3. staging + rules engine: три разных регистрируемых домена — одна группа
    await _apply(scratch_url, _records(sources), STATEMENT_RETOLD)
    members = await _group_members(scratch_url, STATEMENT_RETOLD)
    assert len(members) == 3, f"три источника обязаны быть в снимке, got {members}"
    assert len({str(m["group_id"]) for m in members}) == 1, (
        f"пересказы одного релиза должны быть одной группой, got {members}"
    )
    assert all(str(m["basis"]).startswith("parent:") for m in members), members

    row = await _assessment(scratch_url, STATEMENT_RETOLD)
    assert row is not None
    assert (row[0], row[1]) == ("E1", "hypothesis"), f"ожидались E1/hypothesis, got {tuple(row)}"
    reasons = await _assessment_reasons(scratch_url, STATEMENT_RETOLD)
    assert "insufficient_independence" in reasons, reasons


# ─── противоположный случай: независимая оценка не приклеивается ──────────

STATEMENT_STUDY = "Оценка инфляции 2025 года спорна: официальное и независимое значения различаются."


@pytest.mark.asyncio
async def test_independent_estimate_stays_a_separate_group(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    retold = await _fetch_envelopes(scratch_url, store, (URL_EXPERT,))
    study = await _fetch_envelopes(scratch_url, store, (URL_STUDY,))
    assert retold[0]["attribution_status"] == "derivative"
    # страница со своей оценкой упоминает Росстат — и всё равно не помечена
    assert study[0]["attribution_status"] == "own_assessment", study[0]
    assert study[0]["derivative_of"] is None

    rows = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id FROM sources ORDER BY canonical_uri",
    )
    by_uri = {str(r["canonical_uri"]): r["parent_source_id"] for r in rows}
    assert by_uri.get(URL_STUDY) is None, "независимое исследование не приклеено к первоисточнику"
    assert by_uri.get(URL_EXPERT) is not None

    await _apply(scratch_url, _records([*retold, *study]), STATEMENT_STUDY)
    members = await _group_members(scratch_url, STATEMENT_STUDY)
    assert len({str(m["group_id"]) for m in members}) == 2, (
        f"независимая оценка не сливается с пересказом, got {members}"
    )
    by_source = {str(m["source_id"]): m for m in members}
    study_member = by_source[study[0]["source_id"]]
    assert study_member["basis"] == "single", study_member


# ─── идемпотентный повтор ничего не переписывает задним числом ───────────

STATEMENT_RETRY = "Инфляция 2025 года (повторный fetch того же релиза)."


@pytest.mark.asyncio
async def test_refetch_of_a_retelling_does_not_rewrite_history(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    first = await _fetch_envelopes(scratch_url, store, RETAIL_URLS[:1])
    again = await _fetch_envelopes(scratch_url, store, RETAIL_URLS[:1])
    assert first[0]["source_id"] == again[0]["source_id"], "повтор обязан быть идемпотентным"
    assert again[0]["derivative_of"] is None, "указатель ставится только новой строке источника"

    flags = await _all(
        scratch_url,
        "SELECT payload->>'derivativity_written' AS written, payload->>'idempotent' AS idem "
        "FROM audit_events WHERE type = 'research_fetch_completed' ORDER BY sequence",
    )
    assert [f["written"] for f in flags] == ["true", "false"], flags
    assert [f["idem"] for f in flags] == ["false", "true"], flags

    assert len(await _all(scratch_url, ANCHOR_SQL)) == 1
    count = await _scalar(scratch_url, "SELECT count(*) AS n FROM sources")
    assert count[0] == 2, "пересказ + якорь: повтор не плодит новых строк"


# ─── карточка видит пересказ текстом с сервера ────────────────────────────


@pytest.mark.asyncio
async def test_verification_phrase_names_the_retelling(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_fetch: None
) -> None:
    """Карточка ответа (`apps/web/answer.py`) читает эти же колонки доказательств; фраза
    «как проверено» формулирует `apps/web/reliability.py` — из данных, а не из догадки."""

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())

    sources = await _fetch_envelopes(scratch_url, store, RETAIL_URLS[:2])
    await _apply(scratch_url, _records(sources), STATEMENT_RETOLD)

    rows = await _all(
        scratch_url,
        "SELECT e.relation, e.evidence_kind, s.source_type, s.canonical_uri, ar.sha256 AS artifact_sha256, "
        "s.parent_source_id, ps.canonical_uri AS parent_uri "
        "FROM evidence e LEFT JOIN sources s ON s.id = e.source_id "
        "LEFT JOIN sources ps ON ps.id = s.parent_source_id "
        "LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id "
        "JOIN claims c ON c.id = e.claim_id WHERE c.statement = " + _quoted(STATEMENT_RETOLD),
    )
    assert len(rows) == 2, rows

    phrases = describe_verification(rows)
    assert any("источников прочитано: 2" in p for p in phrases), phrases
    assert any("часть прочитанного — пересказ первоисточника: 2" in p for p in phrases), phrases

    # контроль: там, где указателя нет, фраза не появляется (presentation не выдумывает)
    without = [
        {k: v for k, v in dict(r).items() if k not in ("parent_uri", "parent_source_id")} for r in rows
    ]
    assert not any("пересказ" in p for p in describe_verification(without)), without
