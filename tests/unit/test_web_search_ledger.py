"""Unit: признак «поиск независимых оценок» и строка учёта поиска (T7.89, ADR-0035 §14).

Проверяются ЧИСТЫЕ функции: `apps.web.search_ledger` (консервативный текстовый признак
записанного `web.search`-запроса и разбор аргументов из журнала) и сборка строки в
`apps.web.estimates.estimate_rows_for_source` для трёх разных записанных случаев:
связанных сессий нет (общая честная строка T7.88), сессии есть но поиска независимых
оценок не записано, поиск выполнялся (со счётчиком и цитатами запросов).

Положительные и отрицательные запросы — дословно из дампа журнала сессий стенда .92
(`fixtures-t789/session_searches.tsv`) и из постановки T7.89: признак намеренно уже,
чем «вообще про инфляцию»: официальные и соседние формулировки (Росстат, Банк России,
cbr.ru, инФОМ/наблюдаемая инфляция) признаком НЕ считаются — это официальные и
соседние показатели, а не поиск независимой оценки показателя.

Тексты составленных строк закреплены дословно: их собирает сервер из словаря подписей,
витрина печатает как есть (T7.64).
"""

from __future__ import annotations

import pytest

from apps.web import estimates, search_ledger

pytestmark = pytest.mark.unit

# ─── признак по тексту записанного запроса: только явные маркеры независимости ──

POSITIVE_QUERIES: tuple[str, ...] = (
    # дословно из журнала стенда (`fixtures-t789/session_searches.tsv`)
    "независимые оценки инфляции по итогам 2025 года Росстат альтернативные данные",
    "альтернативная оценка инфляции в России 2025 экономисты не Росстат онлайн-чеки индекс",
    "чек-индекс годовая инфляция 2025 Россия",
    "независимая оценка инфляции России 2025 5,59% альтернативные оценки наблюдаемая инфляция",
    "Чек-индекс инфляция 2025 год годовая оценка",
    # англоязычные формулировки explorerv9-сессий (маркеры те же, по началу слова)
    "independent estimates of russian inflation 2025 alternative sources",
    "alternative inflation measure Russia 2025 unofficial estimate",
)

NEGATIVE_QUERIES: tuple[str, ...] = (
    # официальные источники и их публикации — не поиск независимой оценки
    "Росстат инфляция 2025 год 5,59%",
    "Банк России 28251 инфляция 2025 5,6% cbr.ru",
    "инФОМ наблюдаемая инфляция декабрь 2025 14,5%",
    "наблюдаемая инфляция 13,1% декабрь 2025 ФОМ",
    # сбор фактов и прогнозов — маркеров независимости в формулировке нет
    "Росстат об оценке индекса потребительских цен декабрь 2025 пресс-выпуск",
    "оценка инфляции конец 2025 прогнозы банков Сбербанк ВТБ Тинькофф",
    "итоги инфляции 2025 Россия оценка аналитиков ВТБ Т-Банк 5,5 5,6",
    "",
    "   ",
)


@pytest.mark.parametrize("query", POSITIVE_QUERIES)
def test_signed_queries_are_independent_evaluation_search(query: str) -> None:
    assert search_ledger.search_query_targets_independent_evaluation(query) is True


@pytest.mark.parametrize("query", NEGATIVE_QUERIES)
def test_unsigned_queries_are_not_independent_evaluation_search(query: str) -> None:
    assert search_ledger.search_query_targets_independent_evaluation(query) is False


def test_sign_ignores_case_and_thin_spaces() -> None:
    assert search_ledger.search_query_targets_independent_evaluation(
        "НЕЗАВИСИМАЯ\u00a0оценка инфляции России 2025"
    ) is True


def test_sign_looks_at_word_starts_not_any_substring() -> None:
    """Маркер — начало слова: «полунезависимая» маркером не считается (признак консервативен)."""
    assert search_ledger.search_query_targets_independent_evaluation("полунезависимые оценки инфляции") is False


# ─── разбор аргументов из журнала (`action_started.payload.arguments`) ────────

def test_recorded_query_parses_canonical_json_string() -> None:
    assert search_ledger.recorded_search_query('{"query": "чек-индекс годовая инфляция 2025 Россия"}') == (
        "чек-индекс годовая инфляция 2025 Россия"
    )


def test_recorded_query_accepts_plain_dict_too() -> None:
    assert search_ledger.recorded_search_query({"query": "independent inflation estimates 2025"}) == (
        "independent inflation estimates 2025"
    )


@pytest.mark.parametrize(
    "arguments",
    [
        "это не json вообще",
        '{"url": "https://example.ru"}',  # запроса нет — утверждать про него нечего
        '{"query": ""}',
        '{"query": null}',
        '{"query": 5}',
        None,
        ["query"],
    ],
)
def test_recorded_query_refuses_to_quote_unparseable_arguments(arguments: object) -> None:
    assert search_ledger.recorded_search_query(arguments) is None


# ─── три разных записанных случая вместо одной честной строки (T7.89) ────────

NO_INDEPENDENT = "В знаниях системы нет независимых измерений этого показателя за этот период."
SEARCH_NOT_DONE = (
    "В сессиях этого утверждения поиск независимых оценок не выполнялся; "
    "в знаниях системы нет независимых измерений этого показателя за этот период."
)

_SOURCE_FIELDS: dict[str, object] = {
    "claim_id": "c-1",
    "statement": (
        "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59%."
    ),
    "metric": None,
    "as_of_year": 2026,
    "producers": frozenset[str](),
    "snapshot_id": None,
    "groups": frozenset[str](),
}


def _rows(searches: tuple[str, ...] | None) -> list[tuple[str, str]]:
    source = estimates.EstimateSource(**_SOURCE_FIELDS, independent_searches=searches)  # type: ignore[arg-type]
    return [(row.kind, row.text) for row in estimates.estimate_rows_for_source(source, ())]


def test_no_linked_sessions_keeps_the_old_generic_honest_row() -> None:
    """Сессий утверждения не записано — про поиск ничего не утверждаем (регресс T7.88)."""
    assert _rows(None) == [("no_independent", NO_INDEPENDENT)]


def test_sessions_without_search_say_so_explicitly() -> None:
    """Сессии есть, записанного поиска независимых оценок в них нет — другой случай (б)."""
    assert _rows(()) == [("independent_search_not_done", SEARCH_NOT_DONE)]


def test_search_was_done_cites_queries_and_counts_them() -> None:
    """Поиск выполнялся — строка называет счётчик и цитирует запросы; случай (а) не путается с (б)."""
    assert _rows(("чек-индекс годовая инфляция 2025 Россия",)) == [
        (
            "independent_search_done",
            "Поиск независимых оценок в сессиях этого утверждения: выполнялся "
            "(1 запрос: «чек-индекс годовая инфляция 2025 Россия»); "
            "независимых измерений этого показателя не записано.",
        )
    ]
    assert _rows(
        ("независимые оценки инфляции по итогам 2025 года", "чек-индекс годовая инфляция 2025 Россия")
    ) == [
        (
            "independent_search_done",
            "Поиск независимых оценок в сессиях этого утверждения: выполнялся "
            "(2 запроса: «независимые оценки инфляции по итогам 2025 года», "
            "«чек-индекс годовая инфляция 2025 Россия»); "
            "независимых измерений этого показателя не записано.",
        )
    ]


@pytest.mark.parametrize(
    ("count", "expected_tail"),
    [
        (5, "(5 запросов: «независимая оценка инфляции 0», «независимая оценка инфляции 1», "
           "«независимая оценка инфляции 2»; ещё 2)"),
        (11, "(11 запросов: «независимая оценка инфляции 0», «независимая оценка инфляции 1», "
             "«независимая оценка инфляции 2»; ещё 8)"),
        (4, "(4 запроса: «независимая оценка инфляции 0», «независимая оценка инфляции 1», "
            "«независимая оценка инфляции 2»; ещё 1)"),
    ],
)
def test_query_count_word_and_citation_cap(count: int, expected_tail: str) -> None:
    """Цитируются не больше трёх запросов (потолок карточки), остаток — числом; форма счётного
    слова русская, а «ещё» никогда не бывает нулевой."""
    searches = tuple(f"независимая оценка инфляции {i}" for i in range(count))
    (_kind, text) = _rows(searches)[0]
    assert expected_tail in text
    assert "; ещё 0)" not in text


def test_long_query_is_cut_by_words_with_ellipsis() -> None:
    (_kind, text) = _rows(("независимые оценки инфляции в 2025 году по итогам года аналитики",))[0]
    assert "«независимые оценки инфляции в 2025 году по итогам года…»" in text


def test_unsafe_query_text_is_counted_but_not_quoted() -> None:
    """Небезопасный текст запроса (запрещённый сканером карточек) не попадает в цитату,
    но в счётчик входит честно."""
    searches = ("независимая оценка инфляции: проверено экспертами", "чек-индекс годовая инфляция 2025 Россия")
    (_kind, text) = _rows(searches)[0]
    assert "проверено экспертами" not in text
    assert "(2 запроса: «чек-индекс годовая инфляция 2025 Россия»; ещё 1)" in text


def test_repeated_query_is_cited_once_but_counted_every_time() -> None:
    """Журнал записывает каждое выполненное действие: повтор цитируется один раз, но счёт честный."""
    (_kind, text) = _rows(("чек-индекс годовая инфляция 2025 Россия",) * 3)[0]
    assert "(3 запроса: «чек-индекс годовая инфляция 2025 Россия»; ещё 2)" in text


def test_search_row_disappears_when_independent_estimate_was_found() -> None:
    """Когда независимая оценка найдена, строка о поиске не нужна: подраздел уже называет
    измерение; добавлять к нему журнал поиска — раздувать карточку без нужды (ADR-0035 §14)."""
    source = estimates.EstimateSource(
        **{**_SOURCE_FIELDS, "metric": "годовая инфляция россии", "snapshot_id": "snap-1",
           "groups": frozenset({"g0"})},
        independent_searches=("независимые оценки инфляции по итогам 2025 года",),
    )
    candidate = estimates.EstimateCandidate(
        claim_id="cand-1",
        statement="Независимая оценка: годовая инфляция в России по итогам 2025 года — 7,9%.",
        metric="годовая инфляция россии",
        as_of_year=2026,
        producers=frozenset[str](),
        snapshot_id="snap-1",
        groups=frozenset({"g1"}),
    )
    kinds = [row.kind for row in estimates.estimate_rows_for_source(source, (candidate,))]
    assert kinds == ["independent"]
