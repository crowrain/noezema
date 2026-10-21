"""T7.128 (часть 3, ADR-0034 §10 дополнение): индексная форма значения в гейте «значение на связи».

Стендовый случай c4 (сессия 2d682ab0, config-v29): исследователь прочитал релиз Росстата
«Об индексе потребительских цен в сентябре 2026 года» (страница rosstat.gov.ru, документ 157),
где годовое значение дано ТОЛЬКО индексной формой — строка таблицы «Индекс потребительских цен»
и ячейка «106,35» в столбце «к сентябрю 2025 г.». Куратор предложил «годовая инфляция …
составила 6,35%» — гейт значения снял связь (отдельного «6,35» на странице нет, а граница
текстовой формы не считает «6,35» внутри «106,35»), после раунда исправления куратор вернул
пустое предложение, ответ потерялся.

Закреплено тестом (правило и его границы — apps/research_proxy/index_value_form.py):
- процент утверждения подтверждается связью, если на странице отдельным числом стоит 100 + X
  («106,35» для «6,35%») И рядом с этим числом есть маркер индексной формы;
- без маркера — связь снята (число 100 + X само по себе не подтверждение);
- другое процентное значение («5,35%») индексной формой не подтверждается: сверка точная;
- отрицательное значение («-0,5%») вне правила: форма 100 − X («99,5») не введена;
- обратное направление (утверждение «106,35%», страница «6,35%») вне правила;
- целое процентное значение («6%») вне правила: индексная форма требует дробной части;
- окно маркера ограничено (`INDEX_MARKER_WINDOW_CHARS` = 400): маркер за окном не подтверждает;
- число внутри другого числа («1106,35») подтверждением не считается (та же граница формы).

Фикстура — побайтовый нормализованный текст реальной стендовой страницы (tests/fixtures/t7128/).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.orchestrator.curator_rejections import (
    VALUE_GATE_ABSENT,
    VALUE_GATE_PRESENT,
    link_value_gate,
    statement_value_profile,
)
from apps.research_proxy.index_value_form import (
    INDEX_FORM_MARKERS,
    INDEX_MARKER_WINDOW_CHARS,
    index_form_confirms_value,
    index_form_value,
    index_form_values_found_in_texts,
    percent_statement_values,
)
from apps.research_proxy.source_attribution import MAX_SCAN_CHARS, normalize_scan_text

pytestmark = pytest.mark.unit

FIXTURES = Path(__file__).parent.parent / "fixtures" / "t7128"

CPI_PAGE = normalize_scan_text((FIXTURES / "rosstat-cpi-2026-09.normalized.txt").read_text(encoding="utf-8"))[
    :MAX_SCAN_CHARS
]

# реальная формулировка предложения куратора из аудит-фикстуры c4
C4_CLAIM = (
    "Официальная годовая инфляция в России в сентябре 2026 года составила 6,35% "
    "(по данным Росстата)."
)

# синтетические страницы на вымышленных числах: та же форма таблицы, что у фикстуры
CPI_LIKE = (
    "Индекс потребительских цен в Тармении в сентябре 2029 года\n"
    "к сентябрю 2028 г.\n"
    "Индекс потребительских цен 106,35\n"
    "Продовольственные товары 107,10\n"
)
NO_MARKER_PAGE = (
    "Хроника Тарменской службы статистики. Средний размер выплаты по региону: "
    "106,35 рубля на одного получателя. Таблицы выпуска готовятся отдельно."
)
DECLINE_PAGE = (
    "Индекс потребительских цен в Тармении в сентябре 2029 года\n"
    "к сентябрю 2028 г.\n"
    "Индекс потребительских цен 99,50\n"
)
GROWTH_PLAIN_PAGE = (
    "Годовая инфляция в Тармении ускорилась до 6,35% к сентябрю 2028 года, сообщает "
    "Тарменская служба статистики. Месяцем ранее показатель был ниже."
)


def test_percent_values_of_the_statement_are_measured_with_percent_unit() -> None:
    assert percent_statement_values(C4_CLAIM) == frozenset({"6.35"})
    # единица «процент» обязательна: то же число без неё индексной формой не проверяется
    assert percent_statement_values("Индекс потребительских цен вырос на 6,35 пункта.") == frozenset()
    # знак минуса перед числом — убыль, вне правила (форма 100 − X не введена)
    assert percent_statement_values("Цены снизились на -0,5% за месяц.") == frozenset()
    assert percent_statement_values("Цены снизились на −0,5% за месяц.") == frozenset()
    # целое процентное значение собирается, но индексной формой не подтверждается
    assert percent_statement_values("Годовая инфляция составила 6%.") == frozenset({"6"})


def test_index_form_value_is_a_closed_rule() -> None:
    assert index_form_value("6.35") == "106.35"
    assert index_form_value("0.35") == "100.35"
    assert index_form_value("9.9") == "109.9"
    # целое значение (индекс «106») — вне правила
    assert index_form_value("6") is None
    # X >= 100 и X <= 0 — вне правила
    assert index_form_value("105.5") is None
    assert index_form_value("-6.35") is None
    # не читается как одно десятичное число — значения нет
    assert index_form_value("3.2.5") is None


def test_cpi_fixture_confirms_the_claim_through_the_index_form() -> None:
    """Стенд c4: единственное годовое значение на странице — «106,35» в индексной таблице."""
    assert link_value_gate(statement=C4_CLAIM, texts=(None, CPI_PAGE)) == VALUE_GATE_PRESENT
    assert index_form_confirms_value(statement=C4_CLAIM, values={"6.35"}, texts=(None, CPI_PAGE)) == frozenset(
        {"6.35"}
    )
    # профиль токенов отчёта T7.124 НЕ меняется: индексная форма — путь решения, а не токен
    assert statement_value_profile(C4_CLAIM) == ("6.35",)


def test_marker_is_required_near_the_index_number() -> None:
    """106,35 без подписи «индекс»/базы сравнения — не подтверждение (иначе правило размыто)."""
    assert link_value_gate(statement=C4_CLAIM, texts=(None, NO_MARKER_PAGE)) == VALUE_GATE_ABSENT
    assert index_form_values_found_in_texts(values={"6.35"}, texts=(NO_MARKER_PAGE,)) == frozenset()
    assert index_form_values_found_in_texts(values={"6.35"}, texts=(CPI_LIKE,)) == frozenset({"6.35"})


def test_other_percent_value_is_not_confirmed_by_this_index_number() -> None:
    """Сверка точная: «5,35%» не подтверждается числом 106,35 (только 100 + X)."""
    statement = "Официальная годовая инфляция в России в сентябре 2026 года составила 5,35%."
    assert link_value_gate(statement=statement, texts=(None, CPI_PAGE)) == VALUE_GATE_ABSENT
    assert index_form_values_found_in_texts(values={"5.35"}, texts=(CPI_PAGE, CPI_LIKE)) == frozenset()


def test_decline_is_not_confirmed_by_index_below_one_hundred() -> None:
    """Остаток правила: форма 100 − X (убыль как «99,5») не введена, знак минуса — вне правила."""
    statement = "Индекс потребительских цен в Тармении в сентябре 2029 года снизился на -0,5%."
    assert link_value_gate(statement=statement, texts=(None, DECLINE_PAGE)) == VALUE_GATE_ABSENT
    # и без знака минуса убыль индексом не подтверждается: «0,5%» требует «100,5», а не «99,5»
    plain = "Индекс потребительских цен в Тармении в сентябре 2029 года снизился на 0,5%."
    assert link_value_gate(statement=plain, texts=(None, DECLINE_PAGE)) == VALUE_GATE_ABSENT


def test_reverse_direction_is_not_introduced() -> None:
    """Утверждение об индексе («106,35%») не подтверждается самостоятельным замером «6,35%»."""
    statement = "Индекс потребительских цен в Тармении в сентябре 2029 года составил 106,35%."
    assert link_value_gate(statement=statement, texts=(None, GROWTH_PLAIN_PAGE)) == VALUE_GATE_ABSENT
    assert index_form_values_found_in_texts(values={"106.35"}, texts=(GROWTH_PLAIN_PAGE,)) == frozenset()


def test_integer_percent_is_not_confirmed_by_index_without_fraction() -> None:
    """Целое процентное значение вне правила: «6%» не подтверждается числом 106."""
    page = "Индекс потребительских цен в Тармении\nк сентябрю 2028 г.\n106"
    assert index_form_values_found_in_texts(values={"6"}, texts=(page,)) == frozenset()


def test_marker_window_is_bounded() -> None:
    """Маркер подтверждает число только в окне `INDEX_MARKER_WINDOW_CHARS` (= 400 знака)."""
    filler = "Заполнение блока о методике расчёта занимает этот абзац целиком, цифры в нём нет. "
    assert INDEX_MARKER_WINDOW_CHARS == 400
    near = "Справка Тарменской службы статистики: 106,35. " + filler * 4 + "Индекс потребительских цен"
    far = "Справка Тарменской службы статистики: 106,35. " + filler * 8 + "Индекс потребительских цен"
    distance = len(near) - near.index("106,35") - len("106,35")
    assert distance <= INDEX_MARKER_WINDOW_CHARS
    assert index_form_values_found_in_texts(values={"6.35"}, texts=(near,)) == frozenset({"6.35"})
    far_distance = len(far) - far.index("106,35") - len("106,35")
    assert far_distance > INDEX_MARKER_WINDOW_CHARS
    assert index_form_values_found_in_texts(values={"6.35"}, texts=(far,)) == frozenset()


def test_index_number_inside_another_number_is_not_a_confirmation() -> None:
    """Граница текстовой формы прежняя (T7.108a): «1106,35» — не «106,35»."""
    page = "Индекс потребительских цен Тармении за сентябрь 2029 года и поправочный коэффициент: 1106,35"
    assert index_form_values_found_in_texts(values={"6.35"}, texts=(page,)) == frozenset()


def test_marker_lists_are_closed() -> None:
    """Каждый маркер закрытого списка подтверждает; голые «рост/инфляция/месяц» — нет."""
    base = "Тарменская служба статистики, таблица квартала: 106,35"
    for marker_page in (
        f"{base} (в % к сентябрю 2028 г.)",
        f"{base} — к соответствующему периоду предыдущего года",
        f"Индексы потребительских цен в процентах. {base}",
        f"Показатели месяца; к сентябрю 2028 г.: {base}",
    ):
        assert index_form_values_found_in_texts(values={"6.35"}, texts=(marker_page,)) == frozenset(
            {"6.35"}
        ), marker_page
    for noise_page in (
        "Годовая инфляция в Тармении в сентябре 2029 года: 106,35.",
        "Рост цен составил 106,35 за сентябрь 2029 года.",
    ):
        assert index_form_values_found_in_texts(values={"6.35"}, texts=(noise_page,)) == frozenset()
    assert all(isinstance(marker, str) for marker in INDEX_FORM_MARKERS)
