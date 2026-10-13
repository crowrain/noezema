"""T7.108 (часть а, ADR-0034 §10): сравнение значения утверждения с текстом улики.

Чистый помощник `apps/research_proxy/value_attribution.py::claim_values_found_in_texts` —
та же канонизация чисел (`_canonical_number`), что у детектора атрибуции и у relay-гейта
(ADR-0034 §9), но **без требования единицы измерения на стороне текста**: страница таблицы
пишет «2.2» без «%», и это всё равно значение. Числа ищутся целыми maximal-токенами
`VALUE_NUMBER_PATTERN` — цифры внутри другого числа (122,2 против 2,2) не подтверждение.
"""

from __future__ import annotations

from apps.research_proxy.value_attribution import (
    claim_value_numbers,
    claim_values_found_in_texts,
)

_STATEMENT = (
    "Официальный уровень безработицы в России в августе 2026 года составил 2,2% "
    "по данным Росстата."
)


def _values() -> frozenset[str]:
    return frozenset(claim_value_numbers(_STATEMENT))


def test_claim_value_is_found_in_fragment_with_unit() -> None:
    assert _values() == frozenset({"2.2"})
    found = claim_values_found_in_texts(
        values=_values(),
        texts=("Уровень безработицы снизился до 2,2% с 2,3% в июле.",),
    )
    assert found == frozenset({"2.2"})


def test_page_side_number_needs_no_unit() -> None:
    # страница таблицы пишет значение без единицы измерения — сравнение то же
    found = claim_values_found_in_texts(
        values=_values(),
        texts=("Приложение. Столбец A: 4,1. Столбец B: 2.2. Столбец C: 9.7.",),
    )
    assert found == frozenset({"2.2"})


def test_boundary_numbers_of_the_page_do_not_count() -> None:
    # размер файла и размер архива — не значение утверждения (стенд 2026-08-29:
    # «12,2 Мб» и «52.22 Кб» на странице-заглушке не подтверждают «2,2%»)
    found = claim_values_found_in_texts(
        values=_values(),
        texts=(
            "Файлы раздела. Rab_sila_2026.xlsx (52.22 Кб). "
            "Уровень безработицы по месяцам: таблица (12,2 Мб). Архив 2019–2026.",
        ),
    )
    assert found == frozenset()


def test_digits_inside_a_larger_number_are_not_a_whole_token() -> None:
    # maximal-токен: 122,2 — другое число; подстрочное вхождение не подтверждение
    found = claim_values_found_in_texts(
        values=_values(), texts=("Рост показателя составил 122,2% год к году.",)
    )
    assert found == frozenset()


def test_adjacent_numbers_merge_into_one_token_accepted_quirk() -> None:
    # тот же quirk maximal-токена, что у `_measured_numbers` детектора и relay-гейта
    # (ADR-0034 §7/§10): «12 2,2» читается одним токеном «122.2» — значение не найдено.
    found = claim_values_found_in_texts(
        values=_values(), texts=("Показатели 12 2,2 за год.",)
    )
    assert found == frozenset()


def test_line_break_is_a_token_boundary_and_survives_normalization() -> None:
    found = claim_values_found_in_texts(
        values=_values(), texts=("Обзор рынка.\nБезработица — 2,2% по итогам месяца.",)
    )
    assert found == frozenset({"2.2"})


def test_any_value_of_the_claim_satisfies_the_check() -> None:
    # утверждение с несколькими значениями: подтверждением считается любое из них
    # (то же сравнение, что правило (ii) bibliography-режима ADR-0029)
    statement = "Уровень безработицы снизился с 2,3% до 2,2% за месяц."
    values = frozenset(claim_value_numbers(statement))
    assert values == frozenset({"2.2", "2.3"})
    found = claim_values_found_in_texts(values=values, texts=("В июле — 2,3%.",))
    assert found == frozenset({"2.3"})


def test_claim_without_measured_value_yields_empty_and_text_is_not_scanned() -> None:
    # утверждение без измеренного значения — гейт не применяется (пустые values)
    found = claim_values_found_in_texts(values=frozenset(), texts=("что угодно 2,2%",))
    assert found == frozenset()


def test_scan_budget_is_shared_with_the_attribution_detector() -> None:
    from apps.research_proxy.source_attribution import MAX_SCAN_CHARS

    tail = "Безработица — 2,2% по итогам месяца."
    page = ("обзор рынка труда " * (MAX_SCAN_CHARS // 19)) + tail
    # значение внутри бюджета видно…
    assert claim_values_found_in_texts(values=_values(), texts=(page,)) == frozenset({"2.2"})
    # …а за пределами бюджета — нет: сохранённый текстом сессии весь артефакт не бывает
    beyond = ("обзор рынка труда " * (MAX_SCAN_CHARS // 9)) + tail
    assert claim_values_found_in_texts(values=_values(), texts=(beyond,)) == frozenset()


def test_none_texts_are_skipped_not_matched() -> None:
    assert claim_values_found_in_texts(values=_values(), texts=(None, None)) == frozenset()
