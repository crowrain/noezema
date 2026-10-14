"""T7.110: исправленный омоглиф в глаголе ожидания — закреплённые решения.

Прежние шаблоны `RELAY_FORECAST_VERBS` (`source_attribution.py`) и
`OTHER_SOURCE_ESTIMATION_MARKERS` (`value_attribution.py`) начинались с латинской «o»
(U+006F) после `\b`, поэтому кириллические «ожидает/ожидают/ожидали» не матчились: реле по
такому глаголу молчало, а вето окна публикации по нему не срабатывало. Замер до/после на
синтетике (старый код 29cafeb → новый):

  «ЦБ ожидает инфляцию в 2026 году на уровне 6–7%.» + утверждение о прогнозе ЦБ 6,0–7,0%:
    страница  `no_value_attribution` → `derivative cbr` (честный relay: алиас + глагол +
            число значения на позиции прогноза);
    значение  `no_value_attribution` → `derivative cbr` (pairing value_and_primary_in_fragment).
  та же фраза рядом с шаблоном Росстата (два первоисточника на странице):
    страница  `derivative rosstat` → `ambiguous_primaries` (прежний указатель «производная
            Росстата» противоречится названным прогнозом ЦБ — честный отказ вместо ложной
            одиночной производности);
    значение  `value_not_attributed` → `derivative cbr` (число 6–7 — значение прогноза ЦБ).
  «…опубликованный отчёт Росстата… Отдельно регулятор ожидает ускорение инфляции до 6,3%»:
    значение  `derivative rosstat` (окно публикации) → `value_not_attributed` — вето глагола
            ожидания того же класса, что прежние маркеры оценки другого источника («аналитики
            Сбербанка ожидают» вето не требовало, действующее лицо уже было маркером).
  «инфляционные ожидания населения … 14,2%» — имя показателя: решение не изменилось
    (`derivative cbr`, pairing publication_window): «ожидания» перечисленных конечных форм
    глагола не содержит.

Ни одной ложной склейки правка не порождает: новые приписки — собственные числа собственных
прогнозов названного первоисточника; новые отказы сняли прежние приписки чужих оценок.
"""

from __future__ import annotations

import re

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    RELAY_FORECAST_VERBS,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    OTHER_SOURCE_ESTIMATION_MARKERS,
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)

pytestmark = pytest.mark.unit

NEWS_URI = "https://some-news.example/article"
STAND_NEWS = "https://news.example.ru/a"

CLAIM_CBR_RANGE = "Прогноз Банка России по инфляции на 2026 год составляет 6,0–7,0%."
PAGE_ONE_PRIMARY = "ЦБ ожидает инфляцию в 2026 году на уровне 6–7%."
PAGE_TWO_PRIMARY = (
    "ЦБ ожидает инфляцию в 2026 году на уровне 6–7%. "
    "По данным Росстата, инфляция в 2025 году составила 5,59%."
)


# ─── реле: «ЦБ ожидает …» называет прогноз первоисточника без шаблона ──────────────────


def test_expectation_verb_creates_relay_derivative() -> None:
    """Страница с одним алиасом и глаголом ожидания — пересказ прогноза ЦБ (было: молчание)."""
    page = detect_source_attribution(canonical_uri=NEWS_URI, text=PAGE_ONE_PRIMARY)
    assert (page.status, page.primary_key) == ("derivative", "cbr"), page
    value = attribute_value_in_fragment(
        canonical_uri=NEWS_URI, text=PAGE_ONE_PRIMARY, claim_statement=CLAIM_CBR_RANGE
    )
    assert (value.status, value.primary_key) == ("derivative", "cbr"), value
    assert value.pairing == PAIRING_VALUE_AND_PRIMARY, value


def test_expectation_verb_second_primary_makes_page_ambiguous() -> None:
    """Два первоисточника на странице: названный прогноз ЦБ противоречит шаблонной паре Росстата.

    Страница была «производной Росстата» (один указатель), теперь — честный `ambiguous_primaries`:
    значение 6–7% реле приписывает ЦБ, и прежнее ложное одиночное решение снимается. Для самого
    значения 6–7% приписка честная: число стоит на позиции значения прогноза ЦБ.
    """
    page = detect_source_attribution(canonical_uri=NEWS_URI, text=PAGE_TWO_PRIMARY)
    assert page.status == "ambiguous_primaries", page
    value = attribute_value_in_fragment(
        canonical_uri=NEWS_URI, text=PAGE_TWO_PRIMARY, claim_statement=CLAIM_CBR_RANGE
    )
    assert (value.status, value.primary_key) == ("derivative", "cbr"), value


# ─── вето окна публикации: чужой глагол ожидания ──────────────────────────────────────


def test_expectation_verb_vetoes_publication_window() -> None:
    """«…опубликованный отчёт Росстата… регулятор ожидает ускорение инфляции до 6,3%».

    До правки окно приписывало 6,3% Росстату (`derivative`, pairing publication_window) —
    чужой прогноз попадал в пересказ. После — вето того же класса, что другие маркеры чужой
    оценки: честный отказ с прежним строгим pairing. Действующего лица-маркера здесь нет —
    признак несёт один глагол (для «аналитики ожидают» вето действовало и раньше по действующему
    лицу; ниже оно закреплено как неизменное).
    """
    text = (
        "По данным опубликованного 17 декабря 2025 года отчёта Росстата, инфляция в 2025 году "
        "составила 5,59%. Отдельно регулятор ожидает ускорение инфляции до 6,3% в 2026 году."
    )
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_NEWS,
        text=text,
        claim_statement="Прогноз инфляции в России на 2026 год составляет 6,3%.",
    )
    assert not decision.is_derivative, decision
    assert decision.primary_key is None, decision
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
    # страница целиком остаётся пересказом Росстата: атрибутивная фраза со своим числом 5,59%
    page = detect_source_attribution(canonical_uri=STAND_NEWS, text=text)
    assert (page.status, page.primary_key) == ("derivative", "rosstat"), page


def test_actor_expectation_veto_unchanged() -> None:
    """«Аналитики ожидают …» в окне публикации — прежнее вето (решение не изменилось)."""
    text = (
        "По данным опубликованного Росстатом отчёта, инфляция в 2025 году составила 5,59%. "
        "Аналитики ожидают инфляцию 6,3% в 2026 году."
    )
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_NEWS,
        text=text,
        claim_statement="Аналитики ожидают инфляцию 6,3% в 2026 году.",
    )
    assert not decision.is_derivative, decision
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


def test_expectation_indicator_noun_still_attributed() -> None:
    """«Инфляционные ожидания населения … выросли до 14,2%» — имя показателя, не чужая оценка:
    приписывание ЦБ (окно публикации) остаётся прежним решением."""
    text = (
        "Об этом следует из опубликованных данных опроса, проводимого «инФОМ» по заказу Банка "
        "России. Инфляционные ожидания населения в декабре 2025 года выросли до 14,2%."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://news.example.ru/b",
        text=text,
        claim_statement="Наблюдаемые инфляционные ожидания населения в декабре 2025 года "
        "составляют 14,2%.",
    )
    assert (decision.status, decision.primary_key) == ("derivative", "cbr"), decision
    assert decision.pairing == PAIRING_PUBLICATION_WINDOW, decision


# ─── словарь: конечные формы глагола матчатся, имена показателя — нет ──────────────────


RELAY_VERB = RELAY_FORECAST_VERBS[6]
ESTIMATION_VERB = re.compile(OTHER_SOURCE_ESTIMATION_MARKERS[7])


@pytest.mark.parametrize(
    ("word", "relay", "veto"),
    [
        # перечисленные конечные формы глагола ожидания — действие
        ("ЦБ ожидает инфляцию", True, True),
        ("аналитики ожидают инфляцию", True, True),
        ("регулятор ожидал рост цен", True, True),
        ("мы ожидаем ускорения", True, True),
        # имя показателя опроса — не матчится ни в одном словаре (перечисленных форм нет)
        ("инфляционные ожидания населения", False, False),
        # «ожидаемой/ожидаемых» содержат подслово «ожидаем»: у релевого шаблона нет концевой
        # границы (прежняя форма словаря, T7.110 её не меняет), вето требует границы и молчит.
        # На решениях это не сказывается: релевые гварды (б)–(е) и home-host проверяет код-
        # вызывающий; свип всех фикстурных текстов репозитория — ноль изменившихся решений.
        ("показатель ожидаемой инфляции", True, False),
        ("ожидаемых значениях", True, False),
        # страдательная «ожидается» — релеевый глагол (границы нет), но не маркер вето
        ("ожидается рост цен", True, False),
    ],
)
def test_expectation_word_forms(word: str, relay: bool, veto: bool) -> None:
    assert bool(RELAY_VERB.search(word)) is relay, word
    assert bool(ESTIMATION_VERB.search(word)) is veto, word


# ─── версии методов ────────────────────────────────────────────────────────────────────


def test_method_versions_bumped() -> None:
    """Поведение обоих детекторов изменилось: значения v9→v10, страницы v6→v7 (ADR-0029).
    T7.112 поднял их ещё раз (закрытый словарь глаголов отчётности): значения v10→v11,
    страницы v7→v8 (STATUS T7.112)."""
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v11"
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v8"
