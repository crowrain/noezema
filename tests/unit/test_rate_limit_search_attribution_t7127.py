"""T7.127 (часть 4): привязка отказа лимита поиска узла к поиску сессии — чистые функции отчёта.

Строка журнала `research_fetch_rejected` пишется прокси БЕЗ session_id (его пишет сам прокси,
сессии не зная; закрытый `AuditEventType` задача не расширяла), поэтому отчёт восстанавливает
запрос из upstream-адреса отказа и сопоставляет его с отказавшими `web.search` сессии. Обе
функции — чистые (AGENTS: логику выносить в тестируемую функцию), сценарная часть — в
`tests/scenario/test_honesty_report_rate_limit_t7127.py`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.search import upstream_request_url
from packages.memory.honesty_report import (
    RATE_LIMIT_REJECTION_REASON,
    attribute_rate_limited_searches,
    search_query_from_upstream_url,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_query_survives_the_upstream_url_round_trip() -> None:
    """Декодер отчёта читает адрес ровно так, как его пишет прокси (`upstream_request_url`)."""
    for query in (
        "ВТБ прогноз инфляции 2026 5-6%",
        "Сбербанк ВТБ Альфа-Банк Газпромбанк прогноз инфляции 2026",
        "Росстат годовая инфляция сентябрь 2026 официальная цифра",
    ):
        url = upstream_request_url("http://127.0.0.1:8888", query)
        assert search_query_from_upstream_url(url) == query


def test_fixture_shaped_url_decodes() -> None:
    """Адрес из журнала стенового прогона (процентная кодировка кириллицы) читается."""
    url = "http://127.0.0.1:8888/search?format=json&q=%D0%92%D0%A2%D0%91%20%D0%BE%D0%B1%D0%BD%D0%BE%D0%B2%D0%BB"
    assert search_query_from_upstream_url(url) == "ВТБ обновл"


def test_address_without_a_query_is_not_attributed() -> None:
    """Пустой адрес и адрес без параметра `q` — None: такой отказ ни к чему не привязывается."""
    assert search_query_from_upstream_url("") is None
    assert search_query_from_upstream_url("http://127.0.0.1:8888/search?format=json") is None


def test_single_refusal_is_attributed_to_the_refused_search() -> None:
    matched = attribute_rate_limited_searches(
        [("a1", "ВТБ прогноз инфляции 2026"), ("a2", "Альфа-Банк прогноз инфляции 2026")],
        ["ВТБ прогноз инфляции 2026"],
    )
    assert matched == {"a1"}


def test_repeated_query_consumes_journal_rows_one_by_one() -> None:
    """Один и тот же запрос дважды в окне — два отказа, но не больше: строка журнала расходуется
    один раз на один вызов."""
    matched = attribute_rate_limited_searches(
        [("a1", "ВТБ прогноз инфляции 2026 5-6%"), ("a2", "ВТБ прогноз инфляции 2026 5-6%")],
        ["ВТБ прогноз инфляции 2026 5-6%"],
    )
    assert matched == {"a1"}
    both = attribute_rate_limited_searches(
        [("a1", "ВТБ прогноз инфляции 2026 5-6%"), ("a2", "ВТБ прогноз инфляции 2026 5-6%")],
        ["ВТБ прогноз инфляции 2026 5-6%", "ВТБ прогноз инфляции 2026 5-6%"],
    )
    assert both == {"a1", "a2"}


def test_journal_row_without_a_refused_search_stays_unaccounted() -> None:
    """Отказ чужого запроса (в списке отказов сессии его нет) не приписывается никому."""
    assert attribute_rate_limited_searches([("a1", "свои слова")], ["чужой запрос"]) == set()


def test_action_without_a_query_is_never_attributed() -> None:
    """Пустой запрос не сопоставляется ни с чем: пустая строка не «совпадает» с пустой строкой."""
    assert attribute_rate_limited_searches([("a1", "")], [""]) == set()


def test_empty_inputs() -> None:
    assert attribute_rate_limited_searches([], []) == set()
    assert attribute_rate_limited_searches([("a1", "запрос")], []) == set()


def test_reason_constant_matches_the_proxy_writer() -> None:
    """Отчёт и прокси говорят об одном отказе одной строкой — иначе коррляция молчит молча."""
    service_source = (REPO_ROOT / "apps/research_proxy/service.py").read_text(encoding="utf-8")
    assert RATE_LIMIT_REJECTION_REASON == "upstream_rate_limit_exceeded"
    assert f'"reason": "{RATE_LIMIT_REJECTION_REASON}"' in service_source
