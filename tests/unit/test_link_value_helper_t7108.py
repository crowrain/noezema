"""T7.108 (часть а, ADR-0034 §10) + T7.108a: сравнение значения утверждения с текстом улики.

Чистый помощник `apps/research_proxy/value_attribution.py::claim_values_found_in_texts` —
та же канонизация чисел (`_canonical_number`, через `claim_value_numbers`), что у детектора
атрибуции и у relay-гейта (ADR-0034 §9), но **без требования единицы измерения на стороне
текста**: страница таблицы пишет «2.2» без «%», и это всё равно значение. T7.108a: на стороне
текста значение ищется по ТЕКСТОВЫМ ФОРМАМ с границами (десятичный разделитель «.» или «,»,
хвостовые нули дробной части — 2,2 ≡ 2,20; группировка разрядов по три цифры — «1 649» ≡
«1649»), а не жадными токенами `VALUE_NUMBER_PATTERN`: табличная строка первоисточника —
набор ячеек, а не одно число. Цифры внутри другого числа (122,2 против 2,2) и граничные
числа страницы — не подтверждение: их блокируют границы формы. Соседние ячейки не склеиваются:
«12 2,2» находит 2,2 во второй ячейке (группировка идёт тройками, «12 2,2» не может быть
числом 122,2).
"""

from __future__ import annotations

import pytest

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
    # «12,2 Мб» и «52.22 Кб» на странице-заглушке не подтверждают «2,2%»):
    # вхождения «2,2»/«2.2» склеены с цифрой слева — граница формы их блокирует
    found = claim_values_found_in_texts(
        values=_values(),
        texts=(
            "Файлы раздела. Rab_sila_2026.xlsx (52.22 Кб). "
            "Уровень безработицы по месяцам: таблица (12,2 Мб). Архив 2019–2026.",
        ),
    )
    assert found == frozenset()


def test_digits_inside_a_larger_number_are_not_confirmation() -> None:
    # «122,2» — другое число: вхождение «2,2» склеено цифрой слева, граница формы блокирует
    found = claim_values_found_in_texts(
        values=_values(), texts=("Рост показателя составил 122,2% год к году.",)
    )
    assert found == frozenset()


def test_adjacent_table_cells_do_not_merge() -> None:
    # T7.108a (ADR-0034 §10): «12 2,2» — две ячейки таблицы, значение найдено во второй.
    # Прежде жадный maximal-токен читал «12 2,2» одним числом «122.2» и значение не
    # находилось (переписанный тест: было `frozenset()` → стало `{2.2}`). Группировка
    # разрядов идёт тройками, «12 2,2» не может быть числом 122,2 — ложного склеивания нет.
    found = claim_values_found_in_texts(
        values=_values(), texts=("Показатели 12 2,2 за год.",)
    )
    assert found == frozenset({"2.2"})


@pytest.mark.parametrize(
    "text",
    (
        "Год Июль Август\n2026 2,3 2,2\n",  # табличная строка с переводом строки
        "2026 2,2",  # год и значение в соседних ячейках
        "2,3 2,2",  # июльское и августовское значения рядом
        "2,3\t2,2",  # то же через табуляцию — разделитель таблицы
        "Таблица 3. 2,2%",  # номер таблицы «3.» не склеивается со значением
        "2,20%",  # хвостовые нули дробной части: 2,20 ≡ 2,2 (докстринг обещал — стало на деле)
    ),
)
def test_rosstat_table_rows_confirm_the_value(text: str) -> None:
    # T7.108a: дефект, найденный менеджером на приёмке (beda40a): максимально жадный
    # токен склеивал соседние числа через пробел/табуляцию/перевод строки/точку и гейт
    # снимал законные связи именно с первичных страниц-таблиц (Росстат).
    assert claim_values_found_in_texts(values=_values(), texts=(text,)) == frozenset({"2.2"})


@pytest.mark.parametrize(
    "text",
    (
        "июль 2,3 август 2,2",
        "2,3; 2,2",
        "август 2,2 %",
        "Безработица: 2,2",
        "2.2",
    ),
)
def test_plain_wording_still_confirms(text: str) -> None:
    # прежние засчитываемые строки — засчитываются и после правки границ (прежний результат)
    assert claim_values_found_in_texts(values=_values(), texts=(text,)) == frozenset({"2.2"})


def test_trailing_fraction_zeros_are_equivalent() -> None:
    # докстринг T7.108 утверждал «2,2» ≡ «2.20» — на деле не совпадало; теперь совпадает
    assert claim_values_found_in_texts(values=_values(), texts=("2,20%",)) == frozenset({"2.2"})
    assert claim_values_found_in_texts(values=_values(), texts=("2.20",)) == frozenset({"2.2"})
    assert claim_values_found_in_texts(values=_values(), texts=("2,200 %",)) == frozenset({"2.2"})


def test_different_last_digit_is_not_equivalent() -> None:
    # хвостовые нули — не любые хвостовые цифры: 2,21 — другое значение
    assert claim_values_found_in_texts(values=_values(), texts=("2,21%",)) == frozenset()


def test_digit_grouping_of_integer_value_is_a_form() -> None:
    values = frozenset({"1649"})
    assert claim_values_found_in_texts(values=values, texts=("Добыча 1 649 тыс. тонн.",)) == frozenset(
        {"1649"}
    )
    assert claim_values_found_in_texts(
        values=values, texts=("Добыча 1\u00a0649 тыс. тонн.",)
    ) == frozenset({"1649"})  # неразрывный пробел становится обычным при нормализации
    assert claim_values_found_in_texts(values=values, texts=("Добыча 1649 тыс. тонн.",)) == frozenset(
        {"1649"}
    )


def test_wrong_digit_grouping_is_not_a_form() -> None:
    # «16 49» — не группировка по три цифры (и не голые «1649»): не подтверждение.
    # Прежде maximal-токен склеивал «16 49» в «1649» — переписанный смысл прежней склейки
    # (этим тестом закреплено новое поведение, ADR-0034 §10).
    values = frozenset({"1649"})
    assert claim_values_found_in_texts(values=values, texts=("Добыча 16 49 тыс. тонн.",)) == frozenset()


def test_grouped_form_does_not_match_mid_group() -> None:
    # стык групп через пробел — часть числа: «1 12 345» не подтверждает 12345, а «12 345» подтверждает
    values = frozenset({"12345"})
    assert claim_values_found_in_texts(values=values, texts=("Показатель 1 12 345 пункта.",)) == frozenset()
    assert claim_values_found_in_texts(values=values, texts=("Показатель 12 345 пункта.",)) == frozenset(
        {"12345"}
    )


def test_sign_is_stripped_on_claim_side_and_ignored_on_text_side() -> None:
    # фактическое поведение закреплено: `claim_value_numbers` знак НЕ хранит
    # (`VALUE_NUMBER_PATTERN` знака не видит), поэтому значение «−2,2%» — это «2.2»;
    # знак в тексте значения не мешает: форма «2,2» стоит после «−» или «-»
    assert claim_value_numbers("Динамика производства составил −2,2%.") == ("2.2",)
    assert claim_value_numbers("Динамика производства составил -2,2%.") == ("2.2",)
    values = frozenset(claim_value_numbers("Динамика производства составил −2,2%."))
    assert claim_values_found_in_texts(values=values, texts=("−2,2%", "-2,2%")) == frozenset({"2.2"})


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
