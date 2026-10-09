"""Unit: поуровневая атрибуция значения (T7.78, ADR-0029) — чистая функция без БД и сети.

Проверяется `attribute_value_in_fragment`: тот вход, который хост имеет в момент записи улики
(canonical URI прочитанной страницы, текст основания улики и формулировка утверждения).

Фикстуры набраны живой русской типографикой: неразрывные пробелы (U+00A0) стоят прямо в литералах
(ловушка T7.77 — если редактор или копипаст заменит NBSP обычным пробелом, фикстура перестанет
ловить дефект). Основной текст — живой фрагмент sbercib.ru, где рядом соседствуют атрибуция
Росстата и атрибуция Банка России: ровно та ситуация, из-за которой страничный детектор честно
отказывается решать (`ambiguous_primaries`), а утверждение о числе Росстата получает E3 за
«независимость двух источников».

Смысл тестов — те же границы консервативности (ADR-0029 «Риски»), что у страничного детектора:
ложная склейка дороже ложного пропуска, отказ обязан быть явным статусом, а не молчанием.
"""

from __future__ import annotations

import pytest

from apps.research_proxy.source_attribution import (
    BASIS_FRAGMENT_CHARS,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    STATUS_SELF_PRIMARY,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    NEGATIVE_STATUSES,
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    ValueAttributionDecision,
    attribute_value_in_fragment,
    claim_value_numbers,
)

pytestmark = pytest.mark.unit

SBERCIB_URI = "https://sbercib.ru/press/inflyaciya-2025"

# Живая страница: навигационный мусор + атрибуция Росстата (число года) + атрибуция Банка России
# (январское число). Фраза про продовольствие — измерение без первоисточника: пары не даёт.
PAGE_TWO_PRIMARY = (
    "SberCIB\n"
    "Главная\nАналитика\nМакроэкономика\nОтчёты\nКонтакты\n"
    "\n"
    "Инфляция в России: итоги года и январские оценки\n"
    "\n"
    "По\xa0данным Росстата, инфляция в\xa0России за\xa0весь 2025 год составила 5,59% (после 9,52% в 2024 году).\n"
    "В продовольственном сегменте цены выросли на 6,1% за месяц.\n"
    "По оценкам Банка России, инфляция в\xa0январе замедлилась до 10,7% после 11,2% месяцем ранее.\n"
    "\n"
    "Материал подготовлен аналитическим центром.\n"
)

CLAIM_ROSSTAT = "Инфляция в России за весь 2025 год составила 5,59%"
CLAIM_CBR = "Инфляция в России в январе замедлилась до 10,7%"


def _decide(
    text: str | None, *, uri: str | None = SBERCIB_URI, claim: str | None = CLAIM_ROSSTAT
) -> ValueAttributionDecision:
    return attribute_value_in_fragment(canonical_uri=uri, text=text, claim_statement=claim)


# ── сам факт стенда (.92): страница неоднозначна, а значение утверждения — нет ──────────────


def test_page_level_detector_refuses_this_page() -> None:
    """Прежний уровень честно отказывается: на странице два первоисточника (факт T7.78)."""
    decision = detect_source_attribution(canonical_uri=SBERCIB_URI, text=PAGE_TWO_PRIMARY)
    assert decision.status == STATUS_AMBIGUOUS, decision


def test_rostat_value_on_that_page_is_a_retelling_of_rostat() -> None:
    """Для утверждения о числе Росстата страница — пересказ Росстата (не независимый источник)."""
    decision = _decide(PAGE_TWO_PRIMARY)
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.is_derivative
    assert decision.primary_key == "rosstat"
    assert decision.primary_name == "Росстат"
    assert decision.parent_uri == "https://rosstat.gov.ru/"
    assert "По данным Росстата" in decision.basis_fragment
    assert "5,59%" in decision.basis_fragment


def test_other_value_of_the_same_page_is_a_retelling_of_the_bank_of_russia() -> None:
    """Другое утверждение той же страницы — пересказ Банка России: решение зависит от значения."""
    decision = _decide(PAGE_TWO_PRIMARY, claim=CLAIM_CBR)
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr"
    assert decision.primary_name == "Банк России"
    assert decision.parent_uri == "https://cbr.ru/"


def test_decision_is_not_the_page_status() -> None:
    """Поуровневое решение не подменяет страничное: у него своя версия метода.

    T7.85: v2 = строгий pairing (значение и первоисточник в одном фрагменте) плюс узкий второй
    режим «окно публикации». T7.85a: v3 = то же окно, но с вето «другого источника числа» внутри
    окна (строгий режим не тронут). Маркер формы записи (`VALUE_ATTRIBUTION_SCHEMA`) при этом НЕ
    менялся: записи v1 и v2 читаются по-прежнему, отличается только `method` — на этом различии
    переатрибуция пересматривает прежние решения (STATUS T7.85, STATUS T7.85a)."""
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v3"


# ── границы консервативности ────────────────────────────────────────────────────────────────


def test_two_primaries_for_the_same_claimed_value_are_refused() -> None:
    page = (
        "По\xa0данным Росстата, инфляция за 2025 год составила 5,59%.\n"
        "По оценкам Банка России, инфляция за 2025 год также составила 5,59%.\n"
    )
    decision = _decide(page)
    assert decision.status == STATUS_AMBIGUOUS, decision
    assert not decision.is_derivative
    assert decision.primary_key is None
    assert decision.basis_fragment


def test_own_assessment_fragment_is_not_a_retelling() -> None:
    """Своя оценка редакции в основании улики отвергает производность (вето всего текста)."""
    page = PAGE_TWO_PRIMARY + "По нашей оценке, при ключевой ставке 15% инфляция в 2026 году составит 4,8%.\n"
    decision = _decide(page)
    assert decision.status == STATUS_OWN_ASSESSMENT, decision
    assert not decision.is_derivative


def test_contrast_with_the_primary_is_not_a_retelling() -> None:
    """«В отличие от …» — спорящий расчёт: склеивать нельзя даже при верной атрибуции числа."""
    page = (
        "По\xa0данным Росстата, инфляция за 2025 год составила 5,59%.\n"
        "В отличие от Росстата, наша методика даёт 6,3%.\n"
    )
    decision = _decide(page)
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


def test_different_number_is_not_attributed_to_this_claim() -> None:
    """Первоисточник назван, но про другое число: для этого утверждения атрибуции нет."""
    decision = _decide(PAGE_TWO_PRIMARY, claim="Инфляция в России в апреле ускорилась до 6,2%")
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision
    assert not decision.is_derivative
    assert decision.primary_key is None


def test_bare_mention_of_the_primary_is_not_attribution() -> None:
    page = (
        "Росстат опубликовал годовую статистику цен.\n"
        "Отчёт охватывает 2025 год и 85 регионов.\n"
    )
    decision = _decide(page)
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


def test_primary_page_is_never_a_retelling_of_itself() -> None:
    page = "По данным Росстата, инфляция в России за 2025 год составила 5,59%.\n"
    decision = _decide(page, uri="https://rosstat.gov.ru/press/inflation-2025")
    assert decision.status == STATUS_SELF_PRIMARY, decision
    assert not decision.is_derivative


def test_value_of_another_primary_on_the_own_page_of_a_first_one_is_not_merged() -> None:
    """На странице Минфина названо число Банка России — это чужое значение, а не пересказ Минфина."""
    page = (
        "Минфин ожидает дефицит бюджета в 2026 году на уровне 1,2% ВВП.\n"
        "По оценкам Банка России, инфляция за 2025 год составила 5,59%.\n"
    )
    decision = _decide(page, uri="https://minfin.gov.ru/press/budget-2026")
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr"


# ── значения и их сравнение ─────────────────────────────────────────────────────────────────


def test_years_are_not_values_but_measured_numbers_are() -> None:
    assert claim_value_numbers("Инфляция в России по итогам 2025 года превысила целевой уровень") == ()
    assert claim_value_numbers(CLAIM_ROSSTAT) == ("5.59",)
    assert claim_value_numbers("инфляция 5,59% (после 9,52%) и рост на 1,2 п.\u00a0п.") == (
        "1.2",
        "5.59",
        "9.52",
    )


def test_comma_dot_and_nbsp_inside_a_number_are_the_same_value() -> None:
    assert claim_value_numbers("5,59%") == claim_value_numbers("5.59%")
    assert claim_value_numbers("5,\u00a059%") == ("5.59",)


def test_valueless_claim_is_decided_by_the_whole_basis_and_is_stricter() -> None:
    """Без измеренного значения выбор идёт по всему основанию: два первоисточника → отказ."""
    assert _decide(PAGE_TWO_PRIMARY, claim="Инфляция в России замедляется").status == STATUS_AMBIGUOUS
    single = "По\xa0данным Росстата, инфляция в России за 2025 год составила 5,59%.\n"
    decision = _decide(single, claim="Инфляция в России замедляется")
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat"


def test_values_of_the_claim_are_reported() -> None:
    decision = _decide(PAGE_TWO_PRIMARY, claim="Инфляция 5,59% против 9,52% годом ранее")
    assert decision.claim_values == ("5.59", "9.52")


# ── типографика живой страницы (ловушка T7.77) ──────────────────────────────────────────────


def test_living_typography_does_not_change_the_decision() -> None:
    variants = [
        PAGE_TWO_PRIMARY,
        PAGE_TWO_PRIMARY.replace("\xa0", " "),  # ASCII-пробелы
        PAGE_TWO_PRIMARY.replace("По\xa0данным", "По\u202fданным").replace(
            "Росстата,", "Рос\u00adстата,"
        ),  # узкий NBSP U+202F + мягкий перенос слова U+00AD (невидимая типографика)
    ]
    decisions = [_decide(variant) for variant in variants]
    for decision in decisions:
        assert decision.status == STATUS_DERIVATIVE, decision
        assert decision.primary_key == "rosstat", decision.basis_fragment


def test_basis_fragment_is_capped() -> None:
    long_tail = "и " + "дополнительные пояснения аналитиков " * 30
    page = f"По данным Росстата, инфляция за 2025 год составила 5,59%, {long_tail}.\n"
    decision = _decide(page)
    assert decision.status == STATUS_DERIVATIVE, decision
    assert len(decision.basis_fragment) <= BASIS_FRAGMENT_CHARS


# ── отказы и пустые входы ───────────────────────────────────────────────────────────────────


def test_empty_text_refuses_without_inventing_a_primary() -> None:
    for text in (None, ""):
        decision = _decide(text)
        assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision
        assert decision.primary_key is None


def test_missing_claim_statement_is_allowed() -> None:
    decision = attribute_value_in_fragment(canonical_uri=SBERCIB_URI, text=PAGE_TWO_PRIMARY)
    assert decision.claim_values == ()
    # без значения решение строится по всему основанию улики: здесь два первоисточника → отказ
    assert decision.status == STATUS_AMBIGUOUS, decision


def test_every_refusal_is_an_explicit_status() -> None:
    """Ни один отказ не молчит: статус есть, производность ложная (NEGATIVE_STATUSES полная)."""
    refusals = [
        _decide(PAGE_TWO_PRIMARY, claim="Инфляция в апреле ускорилась до 6,2%"),
        _decide("По нашей оценке, инфляция составит 4,8%."),
        _decide("Росстат опубликовал годовую статистику.", claim=None),
        _decide(None),
        _decide(PAGE_TWO_PRIMARY, claim=None),  # без значения: два первоисточника
    ]
    for decision in refusals:
        assert decision.status in NEGATIVE_STATUSES, decision
        assert not decision.is_derivative
    assert STATUS_VALUE_NOT_ATTRIBUTED in NEGATIVE_STATUSES
    assert STATUS_DERIVATIVE not in NEGATIVE_STATUSES
