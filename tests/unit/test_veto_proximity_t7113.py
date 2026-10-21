"""T7.113 (часть а, ADR-0029 дополнение): вето собственной оценки — окрестность значения.

Стендовый случай (finmarket.ru/news/6727171): утверждение 7cfbe942 «безработица 2,2% по данным
Росстата» на странице-пересказе отвергалось как `own_assessment` из-за маркеров ВНЕ окрестности
числа: плашки комментариев «Мнение посетителей сайта… может не совпадать с мнением редакции…»
(1361 знак от ближайшего вхождения 2,2%) и заголовка чужой новости «Фондовая Америка демонстрирует
противоречивый настрой» (2506 знаков). Новое правило (решение пользователя 2026-10-14): маркеры
собственной оценки ветоют только в радиусе `OWN_ASSESSMENT_PROXIMITY_CHARS` (400) от вхождения
значения утверждения на странице; маркер вне всех окрестностей вето не даёт. Настоящая оценка
рядом с числом ветоет по-прежнему; утверждение без измеренного значения — прежнее вето по всему
основанию.

Диапазонное утверждение 4e7ba8c2 («2,1–2,2% по данным Росстата») на той же странице даёт
`value_not_attributed`: страница несёт одно 2,2, а не диапазон (T7.109a не тронут).

Детектор страниц при этом НЕ изменён (часть а его не касается): на finmarket он остаётся
`own_assessment` — вето по всему документу живёт, и «противоречивый настрой» замазан не был.

Тесты не обращаются к LLM, сети и стенду; finmarket — нормализованная локальная фикстура
T7.112 (windows-1251 → текст по meta-объявлению).
"""

from __future__ import annotations

from pathlib import Path

from apps.research_proxy.normalization import normalize_content
from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    STATUS_DERIVATIVE,
    STATUS_OWN_ASSESSMENT,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    OWN_ASSESSMENT_PROXIMITY_CHARS,
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)

FINMARKET_URI = "https://www.finmarket.ru/news/6727171"
_CLAIM_22 = "Уровень безработицы в России по данным Росстата составляет 2,2%."
_CLAIM_RANGE = "Уровень безработицы в России в сентябре 2026 года составил 2,1–2,2% по данным Росстата."


def _finmarket_page() -> str:
    raw = (
        Path(__file__).resolve().parents[2] / "tests/fixtures/t7112/finmarket-6727171.raw.html"
    ).read_bytes()
    normalized = normalize_content("text/html", raw)
    assert normalized.text.count("\ufffd") == 0
    return normalized.text


# ─── т1: плашка и сайдбар вне окрестности 2,2% — пересказ Росстата подтверждён ──────────


def test_finmarket_value_claim_is_now_derivative_rosstat() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=FINMARKET_URI, text=_finmarket_page(), claim_statement=_CLAIM_22
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision
    assert decision.pairing == "value_and_primary_in_fragment", decision
    assert "2,2%" in decision.basis_fragment, decision.basis_fragment


# ─── т2: диапазонное утверждение остаётся отказом (на странице одно 2,2, не диапазон) ──


def test_finmarket_range_claim_is_value_not_attributed() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=FINMARKET_URI, text=_finmarket_page(), claim_statement=_CLAIM_RANGE
    )
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision
    assert decision.primary_key is None, decision


# ─── т3: настоящая оценка рядом с числом ветоет; шум боковой колонки — нет ─────────────


_ARTICLE_HEAD = (
    "По данным Росстата, уровень безработицы в сентябре составил 2,2% — рекордный минимум "
    "по методологии МОТ.\n\n"
)
_SIDEBAR_NOISE = (
    "События рынка. Сводка корпоративных новостей дня. " * 12
    + "\n\nФондовая Америка демонстрирует противоречивый настрой: индексы закрылись по-разному.\n"
)


def test_own_assessment_sentence_next_to_the_value_still_vetoes() -> None:
    text = _ARTICLE_HEAD + "По нашим расчётам безработица 2,2%, а не ниже, подтверждает тот же ряд.\n"
    decision = attribute_value_in_fragment(
        canonical_uri="https://rg.ru/economics", text=text, claim_statement=_CLAIM_22
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


def test_evaluative_word_in_the_sidebar_outside_the_neighbourhood_is_not_a_veto() -> None:
    text = _ARTICLE_HEAD + _SIDEBAR_NOISE
    decision = attribute_value_in_fragment(
        canonical_uri="https://rg.ru/economics", text=text, claim_statement=_CLAIM_22
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision


def test_claim_without_measured_value_keeps_the_whole_basis_veto() -> None:
    # окрестностей нет — прежнее поведение T7.78…T7.107: маркер где угодно ветоет
    text = _ARTICLE_HEAD + _SIDEBAR_NOISE
    decision = attribute_value_in_fragment(
        canonical_uri="https://rg.ru/economics",
        text=text,
        claim_statement="Безработица в России остаётся низкой по методологии МОТ.",
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


# ─── т6: детектор страниц не тронут — на finmarket по-прежнему own_assessment ──────────


def test_page_detector_on_finmarket_is_unchanged() -> None:
    decision = detect_source_attribution(canonical_uri=FINMARKET_URI, text=_finmarket_page())
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


# ─── версии: значения v12 (поведение изменено); страницы v8 (часть в решений не меняет) ─


def test_method_versions_pinned_after_t7113() -> None:
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v17"
    # часть (в) изменила общий словарь плашек и шаблон «отчитал(ось)», но ни одно закреплённое
    # и фикстурное решение детектора страниц не изменилось (замер свипа — STATUS T7.113):
    # номер метода страниц остаётся v8
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v10"
    assert OWN_ASSESSMENT_PROXIMITY_CHARS == 400
