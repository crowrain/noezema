"""Scenario (fake LLM + fake search + fake pages): новая сессия пишет запись о собственной
публикации (T7.104, ADR-0035 §19 дополнение).

Ограничение T7.101 (STATUS T7.101): «оркестратор при fetch запись не пишет (сервис read-only),
поэтому новые страницы получат запись только при перечитывании окна». T7.104 закрывает его:
новая сессия на фиксированной странице CPD_2026-7 (прогноз Банка России 6,0–7,0% — та же страница
стенда .92, что дала `c6729d4c`) получает `evidence.scope.value_self_publication` сразу при
создании улики — в той же форме и с теми же гейтами, что записала бы `research-reattribute
--evidence-level` для той же улики и формулировки (тот же кандидат `self_publication_candidate`,
тот же сборщик `self_publication_scope_for_fragment`, та же схема и метод).

Что проверяют тесты:

1. записанная форма идентична записи переатрибуции (побайтово, включая basis по полному тексту
   страницы); строка источника не размечена: записи нет там, где нет `self_primary`; витрина
   называет производителя сразу, без перечитывания окна;
2. идемпотентность между путями: после такой сессии `research-reattribute --evidence-level
   --dry-run` отвечает «уже записана» и не предлагает новых записей, прогон apply побайтово не
   меняет ни запись, ни группировку, ни оценку, ни строки источников;
3. гварды не пишут запись и в новой сессии: ловушка T7.87 (страница ЦБ с цифрой Росстата,
   CPD_2025-12, 5,59%), вето третьего актора (ADR-0035 §15: прогноз аналитиков на странице ЦБ,
   Infl_exp_25-12, 6,3%), производный пересказ на чужом сайте (только `value_attribution`, как
   раньше);
4. сравнение с прогоном БЕЗ записи (прежнее поведение: новый путь выключен): витрина молчит
   «Подтверждено слабо», grouping и оценка — ровно такие же, как в прогоне с записью;
   переатрибуция дописывает побайтово ту же запись и тоже ничего остального не меняет;
5. новый ключ невидим группировке и оценкам: на утверждении с тремя прочитанными страницами
   (две собственные + пересказ) прогон `--evidence-level` записывает обе собственные публикации
   и оставляет снимки групп, ролей улик, действующей оценки и строк источников равными;
6. перечитывание той же страницы той же уликой дописывает запись в существующую строку
   (тот же приём `packages/memory/service.py`, что T7.78 для `value_attribution`) и не заводит
   второй улики.

Сети нет: `apps.research_proxy.service.FetchClient` подменён полностью (и страница, и upstream
поиска). Фикстурные страницы отдаются абзацами `<p>` — побайтово тот же приём, что в
`tests/scenario/test_self_publication_t7101.py`: нормализованный текст артефакта равен фикстуре
(sha256 имени файла = `normalized_sha256`). Окно улики пересобирается T7.82 по дословной цитате
из финальной rationale исследователя — тест называет её явно, как это делает explorer-v8 на
стенде.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
from click.testing import CliRunner
from sqlalchemy.ext.asyncio import create_async_engine

from apps.research_proxy.fetch import FetchResult
from apps.research_proxy.value_attribution import self_publication_candidate
from apps.web.reliability import LEVEL_WEAK, PRODUCER_LABEL
from packages.artifacts.store import FilesystemArtifactStore
from tests.conftest import FakeLLM
from tests.scenario import test_self_publication_t7101 as own
from tests.scenario import test_two_sided_search_session as two
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question
from tests.scenario.test_self_publication_t7101 import (
    _assessment_snapshot,
    _group_snapshot,
    _quoted,
    _scopes_of,
    _sources_snapshot,
)
from tests.scenario.test_web_answer_api import _client
from tests.scenario.test_web_knowledge import _make_app

pytestmark = [pytest.mark.scenario]

FIXTURE_DIR = Path(__file__).resolve().parent.parent / "fixtures" / "t7101"

URL_CPD_2026_7 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/"
URL_INFL_26_09 = "https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/"
URL_INFL_25_12 = "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/"
URL_CPD_2025_12 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/"
URL_DERIVATIVE = "https://interfax.example/news/prognoz-cbr-2026"
URL_CBR_ANCHOR = "https://cbr.ru/"

STATEMENT_607 = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STATEMENT_63 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)
STATEMENT_559 = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)

QUESTION_607 = "Каков прогноз Банка России по годовой инфляции в России на 2026 год?"
QUESTION_63 = "Каков прогноз аналитиков по инфляции на конец 2025 года по декабрьскому опросу Банка России?"
QUESTION_559 = "Какова годовая инфляция в России по итогам 2025 года?"
QUESTION_DERIV = "Где опубликован прогноз инфляции на 2026 год: пересказ на информационном портале"

QUERY_607 = "Банк России прогноз годовой инфляции 2026"
QUERY_63 = "декабрьский макроэкономический опрос Банка России прогноз аналитиков"
QUERY_559 = "годовая инфляция по итогам 2025 года"
QUERY_DERIV = "пересказ прогноза инфляции 2026"

#: дословные цитаты исследователя — якоря T7.82-переселекции окна (сигнал снимается ПОСЛЕ
#: complete-раунда); каждая подстрока существует в фикстурном тексте страницы, иначе окно
#: осталось бы как при чтении и тест проверял бы не то окно
RATIONALE_607 = (
    'Прочитал бюллетень ЦБ: "Банк России будет принимать дальнейшие решения по ключевой '
    'ставке в зависимости от динамики инфляции и инфляционных ожиданий". Прогноз на 2026 год '
    "в тексте есть."
)
RATIONALE_63 = (
    'Обзор декабрьского опроса: "выросли у компаний большинства отраслей, почти не изменились '
    'в розничной торговле" — там же прогноз аналитиков 6,3%.'
)
RATIONALE_559 = 'Данные за декабрь: "Годовая инфляция в декабре уменьшилась до 5,59%".'
RATIONALE_DERIV = (
    'Пересказ: "По прогнозу Банка России, годовая инфляция в России в 2026 году составит '
    '6,0–7,0%".'
)

SINCE_ISO = "2000-01-01T00:00:00+00:00"
CURRENT_VALUE_METHOD = "host-value-attribution-v12"
SELF_SCHEMA = "host-value-self-publication-v1"
WEAK_LABEL = "Подтверждено слабо"

#: пересказ прогноза ЦБ на чужом сайте (тот же текст, что в unit-тесте T7.104)
DERIVATIVE_TEXT = (
    "По прогнозу Банка России, годовая инфляция в России в 2026 году составит 6,0–7,0%. "
    "Об этом говорится в опубликованных материалах ведомства."
)

#: побайтовые тексты страниц стенда `c6729d4c` и обеих ловушек
PAGES: dict[str, str] = {
    URL_CPD_2026_7: own._fixture("65af8c97e7f9"),
    URL_INFL_26_09: own._fixture("2bb395fbe861"),
    URL_INFL_25_12: own._fixture("7e2eb44100d5"),
    URL_CPD_2025_12: own._fixture("218e9a1ea998"),
    URL_DERIVATIVE: DERIVATIVE_TEXT,
}

#: страницы, отдаваемые абзацами построчно (нормализация возвращает фикстуру побайтово)
FIXTURE_URLS = {URL_CPD_2026_7, URL_INFL_26_09, URL_INFL_25_12, URL_CPD_2025_12}


class FakeEngineClient:
    """Подмена HTTP-слоя research proxy целиком: и страница, и upstream-поиск SearXNG."""

    def __init__(self, policy: Any) -> None:
        pass

    async def aclose(self) -> None:  # pragma: no cover - контракт
        pass

    async def fetch(self, url: str) -> FetchResult:
        parsed = urlparse(url)
        if parsed.path.endswith("/search"):
            query = (parse_qs(parsed.query).get("q") or [""])[0]
            data = json.dumps({"results": _hits_for(query)}, ensure_ascii=False).encode()
            content_type = "application/json"
        else:
            body = PAGES[url]
            if url in FIXTURE_URLS:
                paragraphs = "".join(f"<p>{line}</p>" for line in body.splitlines() if line.strip())
                data = f"<html><body>{paragraphs}</body></html>".encode()
            else:
                data = f"<html><body><p>{body}</p></body></html>".encode()
            content_type = "text/html"
        return FetchResult(
            url=url,
            final_url=url,
            content_type=content_type,
            data=data,
            sha256=hashlib.sha256(data).hexdigest(),
            redirects=0,
            elapsed_ms=1,
        )


def _hits_for(query: str) -> list[dict[str, str]]:
    lowered = query.lower()
    if "опрос" in lowered:
        return [
            {"url": URL_INFL_25_12, "title": "Макроэкономический опрос", "content": "прогноз аналитиков"}
        ]
    if "по итогам" in lowered:
        return [
            {
                "url": URL_CPD_2025_12,
                "title": "Денежно-кредитная политика: динамика",
                "content": "годовая инфляция",
            }
        ]
    if "пересказ" in lowered:
        return [
            {
                "url": URL_DERIVATIVE,
                "title": "Прогноз инфляции: пересказ",
                "content": "по прогнозу Банка России",
            }
        ]
    return [
        {"url": URL_CPD_2026_7, "title": "Динамика инфляции. Данные", "content": "годовая инфляция в России"}
    ]


@pytest.fixture()
def fake_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", FakeEngineClient)


def _complete(rationale: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": rationale,
            "decision": {"kind": "complete", "reason": "goal_reached"},
        }
    }


def _script_607() -> list[dict[str, Any]]:
    """Один и тот же скрипт сессии: поиск → читывание страницы ЦБ → готово → формулировка."""

    return [
        two._search(QUERY_607),
        two._fetch(URL_CPD_2026_7),
        _complete(RATIONALE_607),
        two._curator(STATEMENT_607, 1),
    ]


async def _run(
    engine: Any,
    scratch_url: str,
    fake_llm: FakeLLM,
    tmp_path: Path,
    question_text: str,
    script: list[dict[str, Any]],
    statement: str,
) -> uuid.UUID:
    """Одна полная сессия: активация профиля поиска, вопрос в очередь, fake-скрипт, run_session.

    Артефакты — в `<NOEZEMA_DATA_ROOT>/artifacts` (tmp_path/data), чтобы CLI-команда
    `research-reattribute` увидела ровно тот же каталог (AGENTS §7, ловушка env для store).
    """
    assert (await _run_online(engine, two._payload())).state == "active"
    question_id = await _seed_question(scratch_url, question_text)
    fake_llm.script(script)
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "data" / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded", session.termination_reason
    claim_id = await _scalar(
        scratch_url, "SELECT id::text AS id FROM claims WHERE statement = " + _quoted(statement)
    )
    assert claim_id is not None, "куратор не записал утверждение"
    return uuid.UUID(str(claim_id[0]))


def _factory(engine: Any) -> Any:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    return async_sessionmaker(engine, expire_on_commit=False)


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


def _reattribution_style_record(canonical_uri: str, statement: str) -> dict[str, Any]:
    """Как собирает запись apply переатрибуции: кандидат по ПОЛНОМУ тексту страницы →
    `build_self_publication` (тот же путь, что `apps/research_proxy/reattribution.py`)."""

    from apps.research_proxy.value_attribution import VALUE_ATTRIBUTION_METHOD_VERSION
    from packages.memory.scope import build_self_publication

    candidate = self_publication_candidate(
        canonical_uri=canonical_uri, text=PAGES[canonical_uri], claim_statement=statement
    )
    assert candidate is not None and not candidate.actor_veto
    record = build_self_publication(
        primary_key=candidate.primary_key,
        primary_name=candidate.primary_name,
        primary_uri=canonical_uri,
        method=VALUE_ATTRIBUTION_METHOD_VERSION,
        basis_fragment=candidate.basis_fragment,
    )
    assert record is not None
    return record.as_scope()


async def _reattribute(scratch_url: str, store: FilesystemArtifactStore, *, dry_run: bool) -> Any:
    """Прогон `--evidence-level` на тестовой БД (тот же путь, что команда hostctl)."""

    from apps.research_proxy.reattribution import reattribute_evidence_window

    db_engine = create_async_engine(scratch_url)
    try:
        return await reattribute_evidence_window(
            _factory(db_engine), store, since=_since(), dry_run=dry_run
        )
    finally:
        await db_engine.dispose()


async def _evidence_rows(scratch_url: str, statement: str) -> list[tuple[str, str, str]]:
    """Снимок улик утверждения: (relation, canonical_uri, identity_hash). Идентичность улики
    доказывает, что перечитывание не завело вторую строку (дедуп T7.34)."""

    rows = await _all(
        scratch_url,
        "SELECT e.relation AS relation, s.canonical_uri AS uri, e.identity_hash AS ih "
        "FROM evidence e JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id "
        "WHERE c.statement = " + _quoted(statement) + " ORDER BY s.canonical_uri, e.relation",
    )
    return [(str(r["relation"]), str(r["uri"]), str(r["ih"])) for r in rows]


async def _knowledge_snapshot(scratch_url: str, statement: str) -> dict[str, Any]:
    """Снимок знания: группировка, действующая оценка, строки источников, улики."""

    return {
        "groups": await _group_snapshot(scratch_url, statement),
        "assessment": await _assessment_snapshot(scratch_url, statement),
        "sources": await _sources_snapshot(scratch_url),
        "evidence": await _evidence_rows(scratch_url, statement),
    }


def _disable_new_path(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Выключить новый путь (прежнее поведение T7.101) и вернуть оригинал для возврата.

    `raising=False` с `getattr(..., None)`: на коде до T7.104 помощника нет вовсе, и тест обязан
    падать по поведению (записи нет), а не по отсутствию имени.
    """

    import apps.orchestrator.orchestrator as orch

    original = getattr(orch, "self_publication_scope_for_fragment", None)
    monkeypatch.setattr(
        orch, "self_publication_scope_for_fragment", lambda **kwargs: None, raising=False
    )
    return original, orch


def _restore_new_path(monkeypatch: pytest.MonkeyPatch, orch: Any, original: Any) -> None:
    monkeypatch.setattr(orch, "self_publication_scope_for_fragment", original, raising=False)


# ─── 1. новая сессия: запись на месте, витрина называет производителя сразу ──


@pytest.mark.asyncio
async def test_new_session_writes_self_publication_record(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    claim_id = await _run(
        engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607
    )

    scopes = await _scopes_of(scratch_url, STATEMENT_607)
    assert set(scopes) == {URL_CPD_2026_7}, scopes
    record = scopes[URL_CPD_2026_7]["self"]
    assert record is not None, "оркестратор не записал собственную публикацию при создании улики"
    assert record["schema"] == SELF_SCHEMA, record
    assert record["primary_key"] == "cbr" and record["primary_name"] == "Банк России", record
    # адрес записи — адрес прочитанной страницы (витрина проверит её о домашнем хосте)
    assert record["primary_uri"] == URL_CPD_2026_7, record
    assert record["method"] == CURRENT_VALUE_METHOD, record
    assert "По прогнозу Банка России" in record["basis_fragment"], record
    # родителя нет по построению: первоисточник не может быть родителем самого себя
    assert "parent_source_id" not in record and "pairing" not in record, record
    # записей о производной атрибуции нет: пересказа на этой странице нет
    assert scopes[URL_CPD_2026_7]["value"] is None, scopes

    # идентичность двух путей: та же запись, что записал бы apply переатрибуции по полному тексту
    assert record == _reattribution_style_record(URL_CPD_2026_7, STATEMENT_607), record

    # строка источника не размечена: запись не склейка, группировка её не читает
    sources = await _sources_snapshot(scratch_url)
    assert sources == [(URL_CPD_2026_7, None, False)], sources

    # группировка и оценка — прежние: одна группа (единственный прочитанный источник), E1;
    # причина названа правилами (одна поддерживающая улика < min_support_evidence=2)
    groups = await _group_snapshot(scratch_url, STATEMENT_607)
    assert {g[0] for g in groups} == {"single"}, groups
    assert len({g[1] for g in groups}) == 1, groups
    assessment = await _assessment_snapshot(scratch_url, STATEMENT_607)
    assert assessment[0] == "E1", assessment
    assert "insufficient_evidence" in assessment[3], assessment

    # витрина называет производителя сразу после сессии — без перечитывания окна
    label = await _label(scratch_url, claim_id)
    assert label["label"] == PRODUCER_LABEL, label
    assert "Банк России" in label["hint"], label
    assert label["level"] == LEVEL_WEAK, label


# ─── 2. кросс-путь идемпотентность: переатрибуция отвечает «уже записано» ────


def test_reattribute_after_session_proposes_nothing(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Синхронный тест: CLI-команда поднимает свой event loop (AGENTS §7, CliRunner-паттерн)."""

    scratch_url, engine = migrated_db
    asyncio.run(
        _run(engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607)
    )
    scopes_before = asyncio.run(_scopes_of(scratch_url, STATEMENT_607))
    assert scopes_before[URL_CPD_2026_7]["self"] is not None, scopes_before
    knowledge_before = asyncio.run(_knowledge_snapshot(scratch_url, STATEMENT_607))

    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path / "data"))
    from hostctl.cli import main

    runner = CliRunner()
    dry = runner.invoke(
        main,
        ["research-reattribute", "--evidence-level", "--since", SINCE_ISO, "--dry-run"],
        catch_exceptions=False,
    )
    assert dry.exit_code == 0, dry.output
    # та же улика перечитана полным текстом: решение то же, запись уже есть — план пуст
    assert "уже записана" in dry.output, dry.output
    assert "будет записано" not in dry.output, dry.output
    assert "доказательств с новым решением: 0" in dry.output, dry.output

    applied = runner.invoke(
        main, ["research-reattribute", "--evidence-level", "--since", SINCE_ISO], catch_exceptions=False
    )
    assert applied.exit_code == 0, applied.output
    assert "доказательств с новым решением: 0" in applied.output, applied.output
    assert "задач переоценки создано: 0" in applied.output, applied.output

    # побайтово те же запись, группировка, оценка, улики и строки источников
    assert asyncio.run(_scopes_of(scratch_url, STATEMENT_607)) == scopes_before
    assert asyncio.run(_knowledge_snapshot(scratch_url, STATEMENT_607)) == knowledge_before


# ─── 3. гварды: ловушка T7.87, вето третьего актора, производный пересказ ────


@pytest.mark.asyncio
async def test_t787_trap_cbr_page_with_rosstat_figure_writes_nothing(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Страница ЦБ с цифрой Росстата (CPD_2025-12, 5,59%): `self_primary` не объявляется — ни
    оркестратор, ни переатрибуция запись не напишут."""

    scratch_url, engine = migrated_db
    await _run(
        engine,
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_559,
        [
            two._search(QUERY_559),
            two._fetch(URL_CPD_2025_12),
            _complete(RATIONALE_559),
            two._curator(STATEMENT_559, 1),
        ],
        STATEMENT_559,
    )
    scopes = await _scopes_of(scratch_url, STATEMENT_559)
    assert scopes == {URL_CPD_2025_12: {"self": None, "value": None}}, scopes
    assert await _sources_snapshot(scratch_url) == [(URL_CPD_2025_12, None, False)]

    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    plan = await _reattribute(scratch_url, store, dry_run=True)
    rows = {row.canonical_uri: row for row in plan.rows}
    assert set(rows) == {URL_CPD_2025_12}, plan.rows
    assert rows[URL_CPD_2025_12].now_status == "value_not_attributed", rows[URL_CPD_2025_12]
    assert rows[URL_CPD_2025_12].action == "изменений нет: атрибуция значения не собрана", rows[
        URL_CPD_2025_12
    ]
    assert plan.changed_evidence == 0 and not plan.changed_source_ids


@pytest.mark.asyncio
async def test_third_actor_veto_writes_nothing_in_new_session(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """ADR-0035 §15: прогноз аналитиков на странице ЦБ (Infl_exp_25_12, 6,3%) — `self_primary`
    с вето третьего актора: оркестратор запись не пишет, переатрибуция называет отказ."""

    scratch_url, engine = migrated_db
    await _run(
        engine,
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_63,
        [
            two._search(QUERY_63),
            two._fetch(URL_INFL_25_12),
            _complete(RATIONALE_63),
            two._curator(STATEMENT_63, 1),
        ],
        STATEMENT_63,
    )
    scopes = await _scopes_of(scratch_url, STATEMENT_63)
    assert scopes == {URL_INFL_25_12: {"self": None, "value": None}}, scopes

    # та же честность на перечитывании окна: план пуст, отказ назван
    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    plan = await _reattribute(scratch_url, store, dry_run=True)
    rows = {row.canonical_uri: row for row in plan.rows}
    assert set(rows) == {URL_INFL_25_12}, plan.rows
    assert rows[URL_INFL_25_12].action == (
        "изменений нет: собственная публикация не записана — "
        "значение названо оценкой третьего актора (ADR-0035 §15)"
    ), rows[URL_INFL_25_12]
    assert plan.changed_evidence == 0 and not plan.changed_source_ids


@pytest.mark.asyncio
async def test_derivative_page_writes_only_value_attribution(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
) -> None:
    """Пересказ прогноза ЦБ на чужом сайте: этот путь закрывает `value_attribution` (T7.78) —
    записи о собственной публикации чужой страницы не бывает."""

    scratch_url, engine = migrated_db
    await _run(
        engine,
        scratch_url,
        fake_llm,
        tmp_path,
        QUESTION_DERIV,
        [
            two._search(QUERY_DERIV),
            two._fetch(URL_DERIVATIVE),
            _complete(RATIONALE_DERIV),
            two._curator(STATEMENT_607, 1),
        ],
        STATEMENT_607,
    )
    scopes = await _scopes_of(scratch_url, STATEMENT_607)
    assert set(scopes) == {URL_DERIVATIVE}, scopes
    assert scopes[URL_DERIVATIVE]["self"] is None, scopes
    value = scopes[URL_DERIVATIVE]["value"]
    assert value is not None, scopes
    assert value["primary_key"] == "cbr" and value["primary_name"] == "Банк России", value
    assert value["method"] == CURRENT_VALUE_METHOD, value
    # первоисточник, которого узел не читал, разрешён якорем (T7.75)
    anchor = await _scalar(
        scratch_url,
        "SELECT id::text AS id FROM sources WHERE canonical_uri = " + _quoted(URL_CBR_ANCHOR),
    )
    assert anchor is not None and value["parent_source_id"] == str(anchor[0]), (value, anchor)


# ─── 4. сравнение с прогоном без записи (прежнее поведение T7.101) ───────────


@pytest.mark.asyncio
async def test_run_without_record_then_reattribution_writes_the_same_record(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тот же сценарий без нового пути: витрина молчит, знание то же, что и в тесте 1.

    Дальнейшее перечитывание окна переатрибуцией дописывает РОВНО ту же запись, что новый путь
    пишет при создании улики (побайтово равна `_reattribution_style_record`, то есть записи
    теста 1), и больше ничего не меняет: группировка, действующая оценка, улики и строки
    источников равны снимкам до прогона, каскада переоценок нет.
    """

    scratch_url, engine = migrated_db
    original, orch = _disable_new_path(monkeypatch)
    claim_id = await _run(
        engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607
    )
    _restore_new_path(monkeypatch, orch, original)

    scopes = await _scopes_of(scratch_url, STATEMENT_607)
    assert scopes == {URL_CPD_2026_7: {"self": None, "value": None}}, scopes
    label_before = await _label(scratch_url, claim_id)
    assert label_before["label"] == WEAK_LABEL, label_before

    knowledge = await _knowledge_snapshot(scratch_url, STATEMENT_607)
    # форма знания без записи совпадает с формой знания с записью (тест 1): одна группа, E1,
    # строка источника не размечена, одна поддерживающая улика
    assert {g[0] for g in knowledge["groups"]} == {"single"}, knowledge["groups"]
    assert knowledge["assessment"][0] == "E1", knowledge["assessment"]
    assert "insufficient_evidence" in knowledge["assessment"][3], knowledge["assessment"]
    assert knowledge["sources"] == [(URL_CPD_2026_7, None, False)], knowledge["sources"]
    assert len(knowledge["evidence"]) == 1, knowledge["evidence"]

    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    plan = await _reattribute(scratch_url, store, dry_run=True)
    rows = {row.canonical_uri: row for row in plan.rows}
    assert set(rows) == {URL_CPD_2026_7}, plan.rows
    assert rows[URL_CPD_2026_7].action == (
        "будет записано: собственная публикация производителя Банк России"
    ), rows[URL_CPD_2026_7]
    assert await _scopes_of(scratch_url, STATEMENT_607) == scopes, "dry-run ничего не пишет"

    applied = await _reattribute(scratch_url, store, dry_run=False)
    assert applied.changed_evidence == 1, applied.rows
    assert not applied.changed_source_ids, applied.rows
    assert applied.invalidated_heads == 0 and applied.jobs_created == 0, applied.rows

    written = (await _scopes_of(scratch_url, STATEMENT_607))[URL_CPD_2026_7]["self"]
    assert written == _reattribution_style_record(URL_CPD_2026_7, STATEMENT_607), written
    assert await _knowledge_snapshot(scratch_url, STATEMENT_607) == knowledge
    label_after = await _label(scratch_url, claim_id)
    assert label_after["label"] == PRODUCER_LABEL, label_after


# ─── 5. новый ключ невидим группировке и оценкам ─────────────────────────────


@pytest.mark.asyncio
async def test_self_publication_key_is_invisible_to_grouping_and_assessment(
    migrated_db: tuple[str, Any], tmp_path: Path, fake_engine: None
) -> None:
    """Утверждение с тремя прочитанными страницами: две собственные публикации ЦБ и пересказ.

    Прогон `--evidence-level` записывает обе собственные публикации (после T7.104 такие записи
    появляются и при создании улики) и не меняет решительно ничего в знании: basis и состав
    групп независимости, роли улик, действующая оценка с причинами и строки источников равны
    снимкам до прогона, каскада и задач переоценки нет.
    """

    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    urls = (own.URL_CPD_2026_7, own.URL_INFL_26_09, own.URL_INFL_25_12, own.URL_CPD_2025_12)
    await own._seed_claim(scratch_url, store, urls, STATEMENT_607)

    scopes_before = await _scopes_of(scratch_url, STATEMENT_607)
    assert set(scopes_before) == set(urls), scopes_before
    assert all(v == {"self": None, "value": None} for v in scopes_before.values()), scopes_before
    knowledge_before = await _knowledge_snapshot(scratch_url, STATEMENT_607)
    groups_before = await _group_snapshot(scratch_url, STATEMENT_607)
    # все четыре страницы — на домене Банка России: группировка сворачивает их в одну группу
    assert {g[0] for g in groups_before} <= {"domain:cbr.ru", "single"}, groups_before
    assert len(groups_before) == 4, groups_before

    plan = await _reattribute(scratch_url, store, dry_run=False)
    assert plan.changed_evidence == 2, plan.rows
    assert not plan.changed_source_ids, plan.rows
    assert plan.invalidated_heads == 0 and plan.jobs_created == 0, plan.rows

    scopes_after = await _scopes_of(scratch_url, STATEMENT_607)
    for url in (own.URL_CPD_2026_7, own.URL_INFL_26_09):
        record = scopes_after[url]["self"]
        assert record == _reattribution_style_record(url, STATEMENT_607), (url, record)
    for url in (own.URL_INFL_25_12, own.URL_CPD_2025_12):
        assert scopes_after[url] == {"self": None, "value": None}, (url, scopes_after[url])

    assert await _knowledge_snapshot(scratch_url, STATEMENT_607) == knowledge_before


# ─── 6. перечитывание той же страницы: та же улика получает запись дописыванием ──


@pytest.mark.asyncio
async def test_reread_of_the_same_page_adds_the_record_to_the_existing_evidence(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    fake_engine: None,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тот же приём `packages/memory/service.py`, что T7.78 для `value_attribution`.

    Улика, перечитанная позже и получившая решение, дописывает запись в существующую строку:
    второй улики не появляется, группировка и оценка не меняются, а третий прогон того же
    перечитывания уже записанное не перезаписывает.
    """

    scratch_url, engine = migrated_db
    original, orch = _disable_new_path(monkeypatch)
    await _run(engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607)
    _restore_new_path(monkeypatch, orch, original)

    scopes_first = await _scopes_of(scratch_url, STATEMENT_607)
    assert scopes_first == {URL_CPD_2026_7: {"self": None, "value": None}}, scopes_first
    rows_first = await _evidence_rows(scratch_url, STATEMENT_607)
    groups_before = await _group_snapshot(scratch_url, STATEMENT_607)
    assessment_before = await _assessment_snapshot(scratch_url, STATEMENT_607)

    # тот же вопрос, та же страница, та же формулировка — но новый путь работает
    await _run(engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607)

    record = (await _scopes_of(scratch_url, STATEMENT_607))[URL_CPD_2026_7]["self"]
    assert record == _reattribution_style_record(URL_CPD_2026_7, STATEMENT_607), record
    rows_after = await _evidence_rows(scratch_url, STATEMENT_607)
    assert rows_after == rows_first, (rows_first, rows_after)  # дедуп: идентичность улики та же
    groups_after = await _group_snapshot(scratch_url, STATEMENT_607)
    assert {g[0] for g in groups_after} == {g[0] for g in groups_before}, groups_after
    assert len(groups_after) == len(groups_before), (groups_before, groups_after)
    assessment_after = await _assessment_snapshot(scratch_url, STATEMENT_607)
    assert assessment_after[:3] == assessment_before[:3], (assessment_before, assessment_after)

    # третье перечитывание: уже записанное не перезаписывается, второй улики по-прежнему нет
    await _run(engine, scratch_url, fake_llm, tmp_path, QUESTION_607, _script_607(), STATEMENT_607)
    assert (await _scopes_of(scratch_url, STATEMENT_607))[URL_CPD_2026_7]["self"] == record
    assert await _evidence_rows(scratch_url, STATEMENT_607) == rows_after

    store = FilesystemArtifactStore(tmp_path / "data" / "artifacts")
    plan = await _reattribute(scratch_url, store, dry_run=True)
    rows = {row.canonical_uri: row for row in plan.rows}
    assert set(rows) == {URL_CPD_2026_7}, plan.rows
    assert rows[URL_CPD_2026_7].action == "изменений нет: собственная публикация уже записана", rows[
        URL_CPD_2026_7
    ]
    assert plan.changed_evidence == 0 and not plan.changed_source_ids


def _since() -> Any:
    from datetime import UTC, datetime

    return datetime.fromisoformat(SINCE_ISO.replace("Z", "+00:00")).replace(tzinfo=UTC)
