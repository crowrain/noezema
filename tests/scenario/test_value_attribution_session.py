"""Scenario (fake LLM + fake pages): поуровневое решение пишет сам сеанс, а не переатрибуция (T7.78).

В `test_value_attribution_levels.py` решение о происхождении значения дописывает команда
`research-reattribute` уже существительным уликам. Здесь же проверяется путь нового знания: настоящая
сессия (curated, fake LLM, fake страницы) читает официальную страницу Росстата и страницу-пересказ, на
которой рядом стоят «По\xa0данным Росстата … 5,59%» и «По оценкам Банка России … 10,7%».

Что из этого следует и что проверяется:

1. **страничный уровень не изменился**: неоднозначную страницу детектор по-прежнему не размечает —
   `ambiguous_primaries`, у строки источника нет родителя и нет `metadata.derivative_of`;
2. **уровень улики решён хостом при коммите**: `_curator` кладёт решение в staging-конверт `evidence`,
   commit boundary переносит его в `evidence.scope` — ни одна доменная таблица вручную не правится;
3. **родитель — настоящая прочитанная страница первоисточника**, а не вновь заведённый якорь: сессия уже
   читала rosstat.gov.ru, и T7.75-разрешение родителя обязано взять этот же факт;
4. **оценка считает честно**: две новости об одном числе Росстата = одна группа независимости →
   `insufficient_independence`, E1, «Проверено» не выставлено (прежде было бы E3).

Сети нет: HTTP-слой research proxy подменён целиком (`FakeEngineClient` из репродюсера T7.76), фикстура
только меняет текст страницы-пересказа на неоднозначный. Домены — заведомо разные registrable domains
(AGENTS §7, T7.75).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import tests.scenario.test_two_sided_search_session as two
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online
from tests.scenario.test_scope_coverage import _all, _scalar, _seed_question

pytestmark = [pytest.mark.scenario]


def _quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"

# Живой текст: две атрибуции разных первоисточников на одной странице + неразрывные пробелы (T7.77)
PAGE_AMBIGUOUS_RETAIL = (
    "Лента новостей · Экономика · Инфляция\n"
    "По\xa0данным Росстата, потребительские цены в России по итогам 2025 года выросли на 5,59% "
    "(в 2024 году — 9,52%).\n"
    "По оценкам Банка России, годовая инфляция замедлилась до 10,7%.\n"
    "Материал подготовлен редакцией по публикациям ведомств."
)


@pytest.fixture()
def ambiguous_retail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Тот же репродюсер T7.76, но страница-пересказ — с двумя первоисточниками сразу."""

    import apps.research_proxy.service as svc

    monkeypatch.setattr(svc, "FetchClient", two.FakeEngineClient)
    monkeypatch.setitem(two.PAGES, two.URL_RETAIL, PAGE_AMBIGUOUS_RETAIL)


@pytest.mark.asyncio
async def test_session_writes_the_value_origin_when_the_page_refuses(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    ambiguous_retail: None,
    tmp_path: Path,
) -> None:
    scratch_url, engine = migrated_db
    assert (await _run_online(engine, two._payload())).state == "active"

    statement = two.STATEMENT_OFFICIAL_ONLY
    question_id = await _seed_question(scratch_url, two.QUESTION_TEXT)
    fake_llm.script(
        [
            two._search(two.QUERY_OFFICIAL),
            two._fetch(two.URL_OFFICIAL),
            two._search(two.QUERY_OFFICIAL),
            two._fetch(two.URL_RETAIL),
            two._COMPLETE,
            two._curator(statement, 2),
        ]
    )
    session = await two._run_session(
        scratch_url, fake_llm, tmp_path / "ws", tmp_path / "artifacts", question_id
    )
    assert session.final_state.value == "succeeded"

    # 1. страничный уровень: неоднозначная страница остаётся нерешённой и не помеченной
    journal = await _all(
        scratch_url,
        "SELECT payload->>'final_url' AS url, payload->>'attribution_status' AS status, "
        "payload->>'source_id' AS source_id FROM audit_events WHERE type = 'research_fetch_completed' "
        "ORDER BY occurred_at",
    )
    by_url = {str(row["url"]): row for row in journal}
    assert by_url[two.URL_RETAIL]["status"] == "ambiguous_primaries", journal
    assert by_url[two.URL_OFFICIAL]["status"] != "derivative", journal

    rows = await _all(
        scratch_url,
        "SELECT canonical_uri, parent_source_id, metadata ? 'derivative_of' AS marked FROM sources "
        "WHERE canonical_uri IN (" + _quoted(two.URL_RETAIL) + ", " + _quoted(two.URL_OFFICIAL) + ")",
    )
    sources = {str(r["canonical_uri"]): r for r in rows}
    assert sources[two.URL_RETAIL]["parent_source_id"] is None, sources
    assert sources[two.URL_RETAIL]["marked"] is False, sources

    # 2. уровень улики: решение записано на пересказе, и его родитель — прочитанная страница Росстата
    origins = await _all(
        scratch_url,
        "SELECT s.canonical_uri, e.scope -> 'value_attribution' AS origin FROM evidence e "
        "JOIN sources s ON s.id = e.source_id JOIN claims c ON c.id = e.claim_id WHERE c.statement = "
        + _quoted(statement),
    )
    assert len(origins) == 2, origins
    by_origin_uri = {str(o["canonical_uri"]): o["origin"] for o in origins}
    retail = dict(by_origin_uri[two.URL_RETAIL] or {})
    assert retail.get("primary_name") == "Росстат", retail
    # T7.85: запись несёт текущий метод значений v2 и pairing; схема остаётся v1
    assert retail.get("method") == "host-value-attribution-v2", retail
    assert "По данным Росстата" in retail.get("basis_fragment", ""), retail
    official_id = await _scalar(
        scratch_url, "SELECT id::text AS id FROM sources WHERE canonical_uri = :u", {"u": two.URL_OFFICIAL}
    )
    assert str(retail.get("parent_source_id")) == official_id[0], (
        "родителем обязана стать уже прочитанная страница первоисточника, а не новый якорь"
    )
    # собственная страница ведомства — не пересказ самой себя: записи о происхождении нет
    assert by_origin_uri[two.URL_OFFICIAL] is None, origins

    # 3. независимость и оценка: одна группа и честный отказ вместо E3
    members = await two._groups(scratch_url, statement)
    assert len({row["group_id"] for row in members}) == 1, members
    retail_members = [m for m in members if m["canonical_uri"] == two.URL_RETAIL]
    assert all(str(m["basis"]).startswith("parent:") for m in retail_members), members

    grade = await two._grade(scratch_url, statement)
    assert grade is not None and grade[0] == "E1", grade
    reasons = await two._reasons(scratch_url)
    assert reasons is not None and "insufficient_independence" in reasons[0], reasons
