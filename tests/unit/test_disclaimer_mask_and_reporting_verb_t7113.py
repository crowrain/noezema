"""T7.113 (часть в, ADR-0029 дополнение): дополнения закрытого словаря — плашка посетителей и
форма отчёта «отчиталось».

(1) Наблюдаемая плашка finmarket «Мнение посетителей сайта, оставляющих свои комментарии на
    новости и статьи, может не совпадать с мнением редакции ИА «Финмаркет»…» и её зеркало:
    ограниченная вставка между частями (не более 80 знаков без точки и перевода строки). Плашка —
    свойство площадки: её маскировка (T7.107) снимает ложное вето; настоящая оценка рядом с
    числом от присутствия плашки не исчезает.

(2) Шаблон отчётности первоисточника дополнен средней формой: «Минэкономразвития отчиталось:
    … 1,2%» (тот же закрытый список глаголов T7.112; требование измеренного числа и алиаса
    словаря в окне одного фрагмента не изменено). Голое «отчиталось» без алиаса пары не даёт.

Тесты не обращаются к LLM, сети и стенду.
"""

from __future__ import annotations

from apps.research_proxy.source_attribution import (
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
)
from apps.research_proxy.value_attribution import attribute_value_in_fragment

_CLAIM_22 = "Уровень безработицы в России по данным Росстата составляет 2,2%."
_CLAIM_MINEC = "Инфляция в России в сентябре 2026 года составила 1,2% по данным Минэкономразвития."
_ARTICLE = (
    "По последним данным Росстата, уровень безработицы сохраняется на рекордно низких "
    "отметках - 2,2% по методологии международных организаций.\n"
)

_PLATE_FINMARKET = (
    "Мнение посетителей сайта, оставляющих свои комментарии на новости и статьи, может не "
    "совпадать с мнением редакции ИА «Финмаркет», и за содержание комментариев ИА «Финмаркет» "
    "ответственности не несет.\n\n"
)
_PLATE_MIRROR = (
    "Мнение редакции ИА «Финмаркет» после модерации может не совпадать с мнением посетителей "
    "сайта и читателей ленты комментариев.\n\n"
)


def test_finmarket_visitor_plate_no_longer_vetoes_the_retelling() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.finmarket.ru/news/6727171",
        text=_PLATE_FINMARKET + _ARTICLE,
        claim_statement=_CLAIM_22,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision


def test_mirror_plate_form_is_masked_too() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.finmarket.ru/news/6727171",
        text=_PLATE_MIRROR + _ARTICLE,
        claim_statement=_CLAIM_22,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision


def test_true_own_assessment_survives_next_to_the_plate() -> None:
    # маскировка плашки не отменяет настоящую оценку рядом с числом: вето остаётся
    text = (
        _PLATE_FINMARKET
        + _ARTICLE
        + "\nПо нашим расчётам безработица 2,2% занижена: методология МОТ не учитывает занятых "
        "в неформальном секторе.\n"
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.finmarket.ru/news/6727171", text=text, claim_statement=_CLAIM_22
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


def test_reporting_verb_middle_form_pairs_minec() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.gazeta.ru/economy",
        text="Минэкономразвития отчиталось: инфляция в сентябре составила 1,2%.\n",
        claim_statement=_CLAIM_MINEC,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "minec", decision
    assert decision.pairing == "value_and_primary_in_fragment", decision


def test_bare_reporting_without_dictionary_alias_stays_refusal() -> None:
    # тот же закрытый список без алиаса словаря — прежний честный отказ (pairing не ослаблен)
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.gazeta.ru/economy",
        text="Экономический блок отчитался: инфляция в сентябре составила 1,2%.\n",
        claim_statement=_CLAIM_MINEC,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision
