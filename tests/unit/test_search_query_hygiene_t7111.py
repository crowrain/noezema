"""Unit (T7.111 часть 1; поправка T7.111a): нормализация запроса, основы содержательных слов,
решение гейта почти-повтора и заметка отказа.

Замер менеджера по 8 сессиям стенда (`fixtures-t7111/step-budget-2026-10-14.txt`, скрипт
`step_budget.py`): 54 поиска, 11 помечены почти-повторами (порог Жаккара 0,6). На приёмке найдено,
что порог отклоняет законные смены угла одним содержательным словом (сравнение с прошлым годом,
смена месяца, подмена актора) — против правила 14 промпта explorer-v11. Правило T7.111a:
почти-повтор = новый запрос без единого нового содержательного слова относительно выполненного
запроса сессии (основы слов, служебные слова вне сравнения). Пересчёт по фикстуре: 11 -> 6
(docs/adr/0036 §1а, STATUS T7.111a). Здесь проверяются чистые функции; поведение цикла сессии —
tests/scenario/test_explorer_query_hygiene_t7111.py.
"""

from __future__ import annotations

import pytest

import apps.orchestrator.tool_context as tool_context
from apps.orchestrator.tool_context import (
    SEARCH_QUERY_SERVICE_WORDS,
    content_word_stems,
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


# ─── содержательные основы (T7.111a) ────────────────────────────────────────────


def test_content_stems_strip_service_words_collapse_forms_and_keep_numbers_whole() -> None:
    # служебные слова вынуты, словоформы сведены к основе приёмом фиксированного префикса,
    # который уже применяется в assertion_window (гейт дублей по значению)
    assert content_word_stems("Росстат уровень безработицы в России последние данные") == frozenset(
        {"росста", "уровен", "безраб", "росси", "послед", "данны"}
    )
    # «Россия» и «России» — одна основа: падежная смена не приносит нового слова
    assert content_word_stems("независимая оценка зарплат Россия 2026") == content_word_stems(
        "независимой оценки зарплатах в России 2026"
    )
    # числа (годы, проценты) — содержательные слова как есть: не усечены, не слиты
    assert "14" in content_word_stems("cbr.ru инфляционные ожидания сентябрь 2026 14,2")
    # T7.112 (б): слово «год» и его падежные формы — служебные: период в поиске несёт число.
    # Было (T7.111a): {"2026", "год"}; стало: {"2026"} (STATUS T7.112). Прилагательное
    # «годовая» — другой показатель («годовые выпуски»), содержательное слово осталось
    assert content_word_stems("2026 год") == frozenset({"2026"})
    assert "годова" in content_word_stems("Росстат годовая инфляция 2026")
    # пустой запрос не содержит содержательных слов
    assert content_word_stems("") == frozenset()


def test_service_words_are_a_closed_short_list_and_gate_constants_are_gone() -> None:
    # закрытый короткий список служебных слов (предлоги, союзы, частицы, местоимения)
    assert {"в", "по", "для", "или", "чтобы", "этот", "ее"} <= SEARCH_QUERY_SERVICE_WORDS
    assert all(len(word) <= 6 for word in SEARCH_QUERY_SERVICE_WORDS)
    # бренды, показатели и акторы служебными словами не являются
    assert not {"росстат", "hh", "втб", "инфляция"} & SEARCH_QUERY_SERVICE_WORDS
    # T7.111a: Жаккар и минимальная длина больше не условия отказа гейта (порог 0,6 отклонял
    # законные смены периода/актора, MIN_WORDS выключал сравнение коротких повтором)
    assert not hasattr(tool_context, "SEARCH_QUERY_NEAR_DUP_JACCARD")
    assert not hasattr(tool_context, "SEARCH_QUERY_NEAR_DUP_MIN_WORDS")


# Четыре запроса «про зарплату» из сессии b357a12b (фигурант замера). Тексты — из фикстуры.
SALARY_FIRST = "независимые оценки реальных зарплат в России 2026 hh.ru SuperJob аналитики банков"
SALARY_COPY = SALARY_FIRST  # буквальный повтор (фикстура: шаги 23–26 одной сессии)
SALARY_SHUFFLED = "независимые оценки реальных зарплат Россия 2026 SuperJob hh.ru аналитики банков"
SALARY_EXTENDED = "независимые оценки реальных зарплат в России 2026 SuperJob hh.ru аналитики банков прогноз"


def test_salary_copies_and_reorder_are_caught_extension_runs() -> None:
    """Буквальный повтор и перестановка слов со сменой падежа («Россия»/«России») — ни одного нового
    содержательного слова: отказ. Расширение формулировки новым словом «прогноз» по правилу T7.111a
    выполняется (было: отказ с Жаккаром 0,923). Доля в кортеже — вспомогательная, прежние значения
    замера закреплены."""
    prior = [(SALARY_FIRST, 21)]
    for query, expected in ((SALARY_COPY, 1.0), (SALARY_SHUFFLED, 0.769)):
        near = search_near_duplicate(query, prior)
        assert near is not None, f"почти-повтор не отклонён: {query}"
        similar_query, similar_step, ratio = near
        assert similar_query == SALARY_FIRST and similar_step == 21
        assert round(ratio, 3) == pytest.approx(expected, abs=0.001)
    assert search_near_duplicate(SALARY_EXTENDED, prior) is None


def test_near_duplicate_compares_against_every_executed_query_of_the_session() -> None:
    # прежний запрос может быть не первым: сравнивается со всеми выполненными
    prior = [(SALARY_FIRST, 1), ("Росстат реальные заработные платы 2026 год", 3)]
    near = search_near_duplicate("Росстат реальные заработные платы 2026", prior)
    assert near is not None
    assert near[1] == 3 and round(near[2], 3) == pytest.approx(0.833, abs=0.001)


def test_legal_fixture_pairs_do_not_trigger_the_guard() -> None:
    """Законные смены угла из фикстуры (смена источника, показателя или актора — новое
    содержательное слово) не отклоняются и после поправки."""
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


def test_defect_pairs_period_and_actor_shifts_run() -> None:
    """Воспроизведение дефекта приёмки T7.111 (на e00f73f..3e3dd9b эти запросы отклонялись с
    Жаккаром 0,714–0,75): сравнение с прошлым годом, смена месяца, подмена актора — каждая смена
    приносит новое содержательное слово и законна (правило 14)."""
    defect_pairs = [
        ("инфляция в России сентябрь 2026 Росстат", "инфляция в России сентябрь 2025 Росстат"),
        ("Росстат уровень безработицы в России июль 2026", "Росстат уровень безработицы в России август 2026"),
        ("реальные зарплаты в России 2026 Росстат", "реальные зарплаты в России 2025 Росстат"),
        ("hh.ru индекс конкуренции август 2026", "hh.ru индекс конкуренции август 2025"),
        # перебор банков по одному — ровно та независимая сторона, ради которой делался T7.111
        ("ВТБ прогноз инфляции 2026 Россия аналитики", "Газпромбанк прогноз инфляции 2026 Россия аналитики"),
        # раньше этот проход был случайностью (два новых токена), теперь — следствием правила
        ("прогноз инфляции 2026 SberCIB аналитики", "прогноз инфляции 2026 Альфа-Банк аналитики"),
    ]
    for prior_query, new_query in defect_pairs:
        assert search_near_duplicate(new_query, [(prior_query, 1)]) is None, f"законный запрос отклонён: {new_query}"


def test_month_shift_after_value_found_is_allowed() -> None:
    """Фикстура e6c8959b: «…май 2026 2,1%…» -> «…май июль 2026 2,2%» -> «…август 2026 2,2%».
    Было (T7.111): отказ с долями 0,7 и 0,667. Стало (T7.111a): новый месяц — новое содержательное
    слово, оба запроса выполняются."""
    first = "rosstat.gov.ru безработица май 2026 2,1% Росстат"
    assert search_near_duplicate("rosstat.gov.ru безработица май июль 2026 2,2%", [(first, 4)]) is None
    assert search_near_duplicate("rosstat.gov.ru безработица август 2026 2,2%", [(first, 4)]) is None


def test_removed_word_and_service_words_do_not_make_a_query_new() -> None:
    # удаление слова: тот же запрос без «год» — новых слов нет, отказ (0,833)
    near = search_near_duplicate(
        "Росстат реальные заработные платы 2026", [("Росстат реальные заработные платы 2026 год", 3)]
    )
    assert near is not None and round(near[2], 3) == pytest.approx(0.833, abs=0.001)
    # добавление служебных слов («и», «для») не меняет существо запроса — отказ
    prior = "Росстат уровень безработицы в России май 2026"
    assert search_near_duplicate("Росстат уровень безработицы в России и для май 2026", [(prior, 1)]) is not None
    # добавление нового года, месяца, актора — проход
    assert search_near_duplicate("Росстат уровень безработицы в России май 2027", [(prior, 1)]) is None
    assert search_near_duplicate("Росстат уровень безработицы в России июнь 2026", [(prior, 1)]) is None
    assert search_near_duplicate("hh.ru индекс конкуренции май 2026 Россия", [(prior, 1)]) is None


def test_short_query_without_new_words_is_a_duplicate_too() -> None:
    """T7.111a: минимальной длины сравнения больше нет — короткий запрос теми же основами тоже
    повтор («инфляция в России» повторно — отказ; было: запросы короче 4 слов не сравнивались).
    Законное уточнение новым словом («прогноз») проходит; пустой запрос не приносит ничего нового
    и отклоняется против любого выполненного запроса.
    T7.112 (б): повтор теперь равенство основ, поэтому УБРАННОЕ содержательное слово — законное
    сужение/расширение: «росстат 2026 инфляция 2,2» -> «росстат 2026» было отказ (подмножество),
    стало выполняется (STATUS T7.112)."""
    assert search_near_duplicate("инфляция в России", [("инфляция в России", 1)]) is not None
    assert search_near_duplicate("инфляция в России прогноз", [("инфляция в России", 1)]) is None
    assert search_near_duplicate("росстат 2026", [("росстат 2026 инфляция 2,2", 1)]) is None
    assert search_near_duplicate("", [(SALARY_FIRST, 1)]) is not None
    # против пустого множества прежних запросов сравнивать не с чем — первый поиск законен всегда
    assert search_near_duplicate("инфляция в России", []) is None


def test_fixture_recount_after_t7112b() -> None:
    """Пересчёт фикстуры (54 поиска) — третьи сутки правила: Жаккарр >= 0,6 отклонял 11 запросов,
    правило «без новых слов» (T7.111a) — 6, правило равенства основ T7.112 (б) — 5 (STATUS T7.112:
    пересчёт фикстуры 6 -> 5, пары закреплены здесь и в test_salary_copies_and_reorder…;
    [43f799fa] поиск 6 <- 2 отпущен: новый запрос уже прежнего без доменного токена «rosstat.gov.ru»
    — сужение/расширение по принятому правилу законно). Закреплены пары, которые изменение коснулось
    прямо: отловленная перестановка 43f799fa и отпущенные законные смены угла."""
    caught_now = [
        (
            "SberCIB прогноз инфляции 2026 6,5 Альфа-Банк 7,5",
            "SberCIB 6,5% Альфа-Банк 7,5% прогноз инфляции 2026",
        ),
    ]
    for prior_query, new_query in caught_now:
        assert search_near_duplicate(new_query, [(prior_query, 1)]) is not None, f"перефраз не отловлен: {new_query}"
    released = [
        ("инФОМ инфляционные ожидания населения 2026", "инФОМ инфляционные ожидания населения сентябрь 2026"),
        (
            "Росстат уровень безработицы в России свежие данные 2026",
            "Росстат уровень безработицы в России август 2026 2,2",
        ),
        # было (T7.111a): отказ — перефраз без новых слов; стало (T7.112б): прежний запрос шире
        # (доменный токен), сужение законно (STATUS T7.112)
        (
            "Росстат годовая инфляция сентябрь 2026 6,35% rosstat.gov.ru",
            "Росстат инфляция в сентябре 2026 6,35% годовая",
        ),
    ]
    for prior_query, new_query in released:
        outcome = search_near_duplicate(new_query, [(prior_query, 1)])
        assert outcome is None, f"законная смена угла отклонена: {new_query}"


def test_near_duplicate_note_names_prior_query_step_alternative() -> None:
    note = render_search_near_duplicate_note(7, SALARY_FIRST, 3, 0.643)
    assert note.startswith("[7] web.search ОТКЛОНЕНО")
    assert "почти повтор запроса шага 3" in note
    assert "совпадение слов 0.64" in note
    assert SALARY_FIRST[:40] in note
    # формулировка наблюдения поправлена (T7.111a): названо отсутствие новых содержательных слов и
    # законная замена из правила 14 — добавить актора, показатель или период
    assert "Новых содержательных слов в нём нет" in note
    assert "прочитай найденное (research.fetch)" in note
    assert "добавь другого актора, другой показатель или другой период" in note
    assert "Отклонённое действие тоже стоит один шаг" in note
    long_note = render_search_near_duplicate_note(7, "с" * 300, 3, 0.611)
    assert "с" * 120 in long_note and "с" * 121 not in long_note
