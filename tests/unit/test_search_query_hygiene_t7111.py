"""Unit (T7.111 часть 1): нормализация поискового запроса, порог почти-повтора и заметка отказа.

Замер менеджера по 8 сессиям стенда (`fixtures-t7111/step-budget-2026-10-14.txt`, скрипт
`step_budget.py`): 54 поиска, из них 11 помечены почти-повторами при пороге 0,6. Здесь проверяются
чистые функции (нормализация, сравнение по Жаккару, текст заметки); поведение цикла сессии —
tests/scenario/test_explorer_query_hygiene_t7111.py. Обоснование порога и расхождения с замером:
docs/adr/0036-explorer-step-hygiene.md и STATUS T7.111.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.tool_context import (
    SEARCH_QUERY_NEAR_DUP_JACCARD,
    SEARCH_QUERY_NEAR_DUP_MIN_WORDS,
    normalize_search_query,
    render_search_near_duplicate_note,
    search_near_duplicate,
)

pytestmark = [pytest.mark.unit]

# ─── нормализация запроса ───────────────────────────────────────────────────────


def test_normalize_lowercases_and_splits_words_on_punctuation() -> None:
    assert normalize_search_query("Росстат Уровень Безработицы В России") == frozenset(
        {"росстат", "уровень", "безработицы", "в", "россии"}
    )
    # пунктуация — разделители, а не часть слова: домены делятся по точкам, десятичные числа — по запятой
    assert normalize_search_query("hh.ru оценка безработицы 2,1% Росстат") == frozenset(
        {"hh", "ru", "оценка", "безработицы", "2", "1", "росстат"}
    )
    # ё -> е: в фикстуре «напряженность рынка» и «напряжённость рынка» должны совпадать
    assert normalize_search_query("напряжённость рынка труда") == normalize_search_query("напряженность рынка труда")
    assert normalize_search_query("") == frozenset()
    assert "\x00" not in "".join(sorted(normalize_search_query("за\x00прос")))


# ─── пороги ─────────────────────────────────────────────────────────────────────


def test_named_constants_match_the_decision() -> None:
    # порог назван решением пользователя; минимальная длина — защита от коротких запросов (ADR-0036)
    assert SEARCH_QUERY_NEAR_DUP_JACCARD == 0.6
    assert SEARCH_QUERY_NEAR_DUP_MIN_WORDS == 4


# Четыре запроса «про зарплату» из сессии b357a12b (фигурант замера). Тексты — из фикстуры.
SALARY_FIRST = "независимые оценки реальных зарплат в России 2026 hh.ru SuperJob аналитики банков"
SALARY_COPY = SALARY_FIRST  # буквальный повтор (фикстура: шаги 23–26 одной сессии)
SALARY_SHUFFLED = "независимые оценки реальных зарплат Россия 2026 SuperJob hh.ru аналитики банков"
SALARY_EXTENDED = "независимые оценки реальных зарплат в России 2026 SuperJob hh.ru аналитики банков прогноз"


def test_salary_family_is_caught_with_the_measured_ratios() -> None:
    prior = [(SALARY_FIRST, 21)]
    for query, expected in ((SALARY_COPY, 1.0), (SALARY_SHUFFLED, 0.769), (SALARY_EXTENDED, 0.923)):
        near = search_near_duplicate(query, prior)
        assert near is not None, f"почти-повтор не отклонён: {query}"
        similar_query, similar_step, ratio = near
        assert similar_query == SALARY_FIRST and similar_step == 21
        assert round(ratio, 3) == pytest.approx(expected, abs=0.001)
        assert ratio >= SEARCH_QUERY_NEAR_DUP_JACCARD


def test_near_duplicate_compares_against_every_executed_query_of_the_session() -> None:
    # прежний запрос может быть не первым: сравнивается со всеми выполненными
    prior = [(SALARY_FIRST, 1), ("Росстат реальные заработные платы 2026 год", 3)]
    near = search_near_duplicate("Росстат реальные заработные платы 2026", prior)
    assert near is not None
    assert near[1] == 3 and round(near[2], 3) == pytest.approx(0.833, abs=0.001)


def test_legal_fixture_pairs_do_not_trigger_the_guard() -> None:
    """Законные смены угла из фикстуры (доля слов < 0,6 при обеих токенизациях замера) не должны
    отклоняться: смена источника, показателя или актора — не перефраз."""
    legal_pairs = [
        ("Росстат уровень безработицы в России последние данные", "Росстат уровень безработицы в России май 2026"),
        (
            "hh.ru оценка безработицы напряженность рынка труда России 2026 аналитики банков",
            "SberCIB прогноз безработицы 2026 независимая оценка рынка труда России",
        ),
        (
            "SberCIB прогноз безработицы 2026 независимая оценка рынка труда России",
            "SberCIB прогноз безработицы 2026 hh.ru напряжённость рынка труда",
        ),
        ("cbr.ru инфляционные ожидания сентябрь 2026 14,2", "SberCIB ВТБ Альфа БКС прогноз инфляции 2026"),
        ("Росстат годовая инфляция сентябрь 2026", "инФОМ наблюдаемая инфляция сентябрь 2026 15,1%"),
        (
            "аналитики банков прогноз реальных зарплат Россия 2026 SberCIB Альфа ВТБ",
            "SberCIB прогноз инфляции 2026 6,5 Альфа-Банк 7,5",
        ),
    ]
    for prior_query, new_query in legal_pairs:
        outcome = search_near_duplicate(new_query, [(prior_query, 1)])
        assert outcome is None, f"законная смена угла отклонена: {new_query}"


def test_month_shift_with_value_already_found_is_caught() -> None:
    """Смена месяца при уже найденном значении (фикстура e6c8959b: «…май 2026 2,1%…» → «…май июль
    2026 2,2%» → «…август 2026 2,2%») — перефраз теми же словами, guard обязан ловить: 0,7 и 0,667."""
    first = "rosstat.gov.ru безработица май 2026 2,1% Росстат"
    second = "rosstat.gov.ru безработица май июль 2026 2,2%"
    third = "rosstat.gov.ru безработица август 2026 2,2%"
    near_second = search_near_duplicate(second, [(first, 4)])
    assert near_second is not None and round(near_second[2], 3) == pytest.approx(0.7, abs=0.001)
    near_third = search_near_duplicate(third, [(first, 4), (second, 5)])
    assert near_third is not None and round(near_third[2], 3) == pytest.approx(0.667, abs=0.001)


def test_short_queries_are_not_compared_and_cannot_collide() -> None:
    """Риск коротких запросов (ADR-0036): на 3 словах доля легко превышает 0,6 («инфляция в России» /
    «инфляция в России прогноз» дают 0,75). Запросы короче MIN_WORDS не сравниваются. Живые запросы
    фикстуры содержат не меньше 5 слов — guard их не выключает."""
    assert search_near_duplicate("инфляция в России прогноз", [("инфляция в России", 1)]) is None
    assert search_near_duplicate("росстат 2026", [("росстат 2026 инфляция 2,2", 1)]) is None
    assert search_near_duplicate("", [(SALARY_FIRST, 1)]) is None


def test_near_duplicate_note_names_prior_query_step_and_alternative() -> None:
    note = render_search_near_duplicate_note(7, SALARY_FIRST, 3, 0.643)
    assert note.startswith("[7] web.search ОТКЛОНЕНО")
    assert "почти повтор запроса шага 3" in note
    assert "совпадение слов 0.64" in note
    assert SALARY_FIRST[:40] in note
    assert "прочитай найденное (research.fetch) или смени формулировку по существу" in note
    assert "Отклонённое действие тоже стоит один шаг" in note
    long_note = render_search_near_duplicate_note(7, "с" * 300, 3, 0.611)
    assert "с" * 120 in long_note and "с" * 121 not in long_note
