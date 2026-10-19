"""Unit: новый последний режим детектора значений «известный производитель упомянут на
странице» — pairing `known_producer_mentioned` (T7.116 часть а, ADR-0029 дополнение; метод
значений v13).

Стендовый случай (сессия 53be7fd6, утверждение c6729d4c «Прогноз Банка России по годовой
инфляции в России на 2026 год составляет 6,0–7,0%»): улика interfax.ru/1121468 записана
строгим режимом (derivative → Банк России), а пересказ той же формулировки на lenta.ru не
содержит ни шаблона атрибуции, ни глагола сообщения — прежний детектор отказывался, страница
оставалась независимой группой, и утверждение в третий раз получало E3 «Проверено».

Новый режим приписывает значение производителю X только когда всё одновременно:
(i) X — известный производитель ЭТОГО утверждения (другая улика записана строгим режимом или
режимом публикации — не новым и не bibliography-режимом: без цепочек);
(ii) значение утверждения есть на странице (диапазон — целым диапазоном или обоими концами,
одиночное — одиночным числом и не концом чужого составного диапазона (T7.109a внутри
области); эквивалентность форм T7.109/T7.109a, как гейт T7.113);
(iii) словарный алиас X встречается на странице в ОДНОЙ области со значением — между концами
предложения (co-location; ужесточение после свипа old-vs-new: «6%» прибыли Morgan Stanley в
списке новостей при заголовке «выше ожиданий ЦБ» и «14,5%» базовой инфляции в предложении
«такой же была динамика» после абзаца «по оценкам Банка России» — чужие числа в чужих
предложениях; формы значения проверяются внутри области, а не по всей странице);
(iv) вето собственной оценки в окрестности значения (T7.113, ±400) не сработало;
(v) страница не домашняя для X;
(vi) на странице не назван ДРУГОЙ известный производитель утверждения (иначе ambiguous_primaries).
Режим вызывается после всех прежних (strict → публикация → ближайший → bibliography → новый)
и записывается формой строгого режима с новым pairing.

Синтетические страницы набраны абзацами и на заведомо разных доменах (ловушка T7.75:
interfax.example / lenta.example / portal.example).
"""

from __future__ import annotations

from pathlib import Path
from typing import Final

import pytest

from apps.orchestrator.attribution_keys import known_primary_key_from_decision
from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
)
from apps.research_proxy.value_attribution import (
    PAIRING_PUBLICATION_WINDOW,
    PAIRING_VALUE_AND_PRIMARY,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    ValueAttributionDecision,
    attribute_value_in_fragment,
)
from packages.memory.scope import (
    VALUE_ATTRIBUTION_PAIRINGS,
    build_value_attribution,
    parse_value_attribution,
)

pytestmark = pytest.mark.unit

#: новое pairing-значение (литерал закрепляет контракт и на прежнем коде — красный тест)
NEW_PAIRING: Final = "known_producer_mentioned"

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t7116"
LENTA_PAGE = (FIXTURES / "lenta-kogan-20261008.txt").read_text(encoding="utf-8")
URL_LENTA: Final = "https://www.lenta.ru/news/2026-10-08/sprognozirovano-reshenie-cb-po-inflyacii"

STATEMENT_CBR: Final = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STATEMENT_KOGAN: Final = (
    "Инвестбанкир Евгений Коган предполагает, что Банк России повысит прогноз инфляции "
    "на 2026 год до 7,0-7,5%."
)
STATEMENT_DEC2025: Final = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5%."
)
STATEMENT_ROSSTAT_TWIN: Final = (
    "Инфляция в России в 2026 году окажется в коридоре 6-7 процентов по данным Росстата."
)

# ─── синтетические страницы той же синтаксической формы, что стенд ────────────────

IFX_LIKE: Final = "\n".join(
    [
        "Годовая инфляция на 5 октября превысила прогноз ЦБ на 2026 год (6-7%).",
        "ЦБ 11 сентября подтвердил свой прогноз по инфляции в 2026 году на уровне 6-7%.",
        "Нейтральный абзац про ставку и проинфляционные риски без чужих оценок.",
    ]
)
URL_IFX: Final = "https://interfax.example/news/1121468"

LENTA_LIKE: Final = "\n".join(
    [
        "Спрогнозировано решение Банка России по инфляции",
        "Инвестбанкир Коган: ЦБ, вероятно, повысит прогноз по инфляции до 7-7,5 %",
        (
            "На фоне роста инфляции, годовые показатели которой превысили 7 процентов, "
            "выйдя за пределы запланированного Банком России коридора в 6-7 процентов, "
            "регулятор, вероятно, повысит прогноз темпов роста цен, считает инвестбанкир "
            "Евгений Коган."
        ),
        "Он предположил, что прогноз будет поднят до 7,0-7,5 процента.",
    ]
)
URL_LENTA_LIKE: Final = "https://lenta.example/news/1"

AMBIGUOUS_PAGE: Final = "\n".join(
    [
        "Инфляция в России в 2026 году окажется в коридоре 6-7 процентов.",
        "Ранее об этом говорил Банк России в своём обзорном материале.",
        "Свои комментарии публиковал и Росстат на странице выпуска.",
    ]
)
URL_PORTAL: Final = "https://portal.example/a"

SINGLE_END_PAGE: Final = "\n".join(
    [
        "Инфляция в России ускорилась до 7 процентов по итогам месяца.",
        "Ранее об этом говорил Банк России в своём обзорном материале.",
    ]
)
URL_PORTAL_B: Final = "https://portal.example/b"

BOTH_ENDS_PAGE: Final = "\n".join(
    [
        (
            "По прогнозу Банка России инфляция в России в 2026 году: "
            "нижняя граница 6 процентов, верхняя граница 7 процентов."
        ),
        "Отдельный абзац: тот же прогноз обсуждался на пресс-конференции.",
    ]
)
URL_PORTAL_D: Final = "https://portal.example/d"

#: свип T7116 (страница finmarket): чужие числа в чужих предложениях при алиасе в заголовке
SCATTERED_PAGE: Final = "\n".join(
    [
        "Morgan Stanley увеличил чистую прибыль в 3-м квартале на 6 процентов.",
        "Индекс сырьевых товаров вырос на 7 процентов за неделю.",
        "Отчёты аналитиков оказались выше ожиданий ЦБ по ключевой ставке.",
    ]
)
URL_PORTAL_E: Final = "https://portal.example/e"

#: свип T7116 (страница sbercib): алиас в одном предложении про January, значение — в следующем
OTHER_SENTENCE_PAGE: Final = "\n".join(
    [
        "По оценкам Банка России, инфляция в январе замедлилась до 10,7 процента.",
        "Такой же была динамика другой величины — с 14,5% до 11%.",
    ]
)
URL_PORTAL_F: Final = "https://portal.example/f"

#: алиас в начале длинной (но не запотолковой) области, значение — в её конце: дальше окна
# publication/nearest режимов, внутри co-location области; проверка гварда (iii-а) и потолка
# длины области (_COLLOCATION_REGION_MAX_CHARS) одновременно
FAR_NAMED_PAGE: Final = (
    "Банк России опубликовал развёрнутый обзор денежно-кредитной политики за прошлый год "
    "и планы на следующий период "
    + "подробные абзацы обзора регулятора с перечислением факторов и комментариев " * 8
    + "коридор на 2026 год: нижняя граница 6 процентов, верхняя граница 7 процентов."
)
URL_PORTAL_G: Final = "https://portal.example/g"

#: свип T7116 (страница 34ec): одиночное значение утверждения 14,5 на странице — только конец
# чужого составного диапазона «14,5–14,6%» прогноза средней ставки в одной области с алиасом
FOREIGN_RANGE_PAGE: Final = (
    "Средняя ключевая ставка в 2026 году, по обновленному прогнозу Банка России, "
    "составит 14,5–14,6%. Об этом сообщил регулятор на пресс-конференции."
)
URL_PORTAL_H: Final = "https://portal.example/h"

#: контроль к предыдущему фикстуру: то же значение — одиночной записью в одной области с алиасом
SINGLE_VALUE_PAGE: Final = (
    "Уровень инфляции 14,5 процента, зафиксированный Банком России по итогам года."
)
URL_PORTAL_I: Final = "https://portal.example/i"

OWN_ASSESSMENT_PAGE: Final = "\n".join(
    [
        "По нашим расчётам, инфляция в России в 2026 году уложится в коридор 6-7 процентов.",
        "Мы учитывали сезонность и индексацию тарифов.",
    ]
)
URL_BLOG: Final = "https://blog.example/c"


# ─── т1: стендовый случай закрыт новым режимом ──────────────────────────────────


def test_lenta_retelling_is_attributed_when_producer_is_known_from_other_evidence() -> None:
    """Было (8ffb4c9): `value_not_attributed` — независимая группа, E3 в третий раз.
    Стало: derivative → Банк России с pairing нового режима (значение 6-7 на странице,
    алиас «Банк России» назван, известный производитель другой улики = cbr)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_LENTA,
        text=LENTA_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == NEW_PAIRING, decision


def test_ifx_anchor_pair_stays_strict_mode_not_the_new_one() -> None:
    """Строгий режим решает первым: на страницы с собранной парой новый режим не
    распространяется — pairing остаётся строгим (прежние режимы не ослаблены)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_IFX,
        text=IFX_LIKE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision


# ─── т2/т3: прежний честный отказ без известного производителя ──────────────────


def test_without_known_primaries_the_refusal_is_unchanged() -> None:
    """Тот же текст и то же утверждение без `known_claim_primaries` — прежний отказ
    (режим не изобретает признак «первоисточник на любой странице»)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_LENTA, text=LENTA_PAGE, claim_statement=STATEMENT_CBR
    )
    assert decision.status in {STATUS_NO_VALUE_ATTRIBUTION, "value_not_attributed"}, decision
    assert decision.primary_key is None, decision


def test_private_opinion_claim_without_known_producer_stays_unattributed() -> None:
    """Частное мнение инвестбанкира (стенд 85220305): значения-токены на странице есть,
    но известного производителя у этого утверждения нет — приписки нет."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_LENTA, text=LENTA_PAGE, claim_statement=STATEMENT_KOGAN
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


# ─── т4: вето собственной оценки рядом со значением ─────────────────────────────


def test_own_assessment_near_the_value_beats_the_new_mode() -> None:
    """Настоящая собственная оценка («по нашим расчётам 6-7%») в окрестности значения
    (T7.113, ±400) — прежний отказ `own_assessment`, даже когда производитель известен."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_BLOG,
        text=OWN_ASSESSMENT_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


# ─── т5: два известных производителя названы на странице ────────────────────────


def test_two_known_producers_named_on_the_page_are_ambiguous() -> None:
    """Условие (vi): на странице названы ДВА известных производителя утверждения —
    `ambiguous_primaries` с pairing нового режима (записи нет)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL,
        text=AMBIGUOUS_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr", "rosstat"}),
    )
    assert decision.status == STATUS_AMBIGUOUS, decision
    assert decision.primary_key is None, decision
    assert decision.pairing == NEW_PAIRING, decision


# ─── т6: диапазон утверждения против одиночного конца на странице ───────────────


def test_single_page_end_does_not_confirm_a_claim_range() -> None:
    """Условие (ii): одиночный конец («7 процентов») не подтверждает диапазон утверждения
    «6,0–7,0%» — прежний честный отказ (тот же смысл, что T7.109a и гейт T7.113)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_B,
        text=SINGLE_END_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


def test_both_ends_as_standalone_numbers_confirm_the_range() -> None:
    """Оба конца отдельными самостоятельными числами подтверждают диапазон целиком —
    та же форма подтверждения, что у гейта T7.113 (таблица «мин./макс.»), но теперь и алиас
    стоит в том же предложении (co-location после ужесточения)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_D,
        text=BOTH_ENDS_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == NEW_PAIRING, decision


# ─── т6а: свип old-vs-new — чужие числа в чужих предложениях (ужесточение) ──────


def test_numbers_in_other_sentences_do_not_confirm_despite_alias_on_page() -> None:
    """Страничный случай finmarket: «6%» прибыли Morgan Stanley и «7%» сырьевого индекса —
    числа чужих сюжетов; алиас ЦБ в заголовке другой темы. Прежняя «co-location по всей
    странице» склеивала бы их с диапазоном утверждения; теперь — честный отказ."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_E,
        text=SCATTERED_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


def test_alias_in_the_previous_sentence_does_not_confirm_the_next_one() -> None:
    """Страничный случай sbercib: «По оценкам Банка России … 10,7%» и следующее предложение
    «такой же была динамика … с 14,5% до 11%» — алиас относится к другой величине.
    Честный отказ, приписки нет."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_F,
        text=OTHER_SENTENCE_PAGE,
        claim_statement=STATEMENT_DEC2025,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


def test_end_of_foreign_composite_range_is_not_a_single_value() -> None:
    """Страничный случай 34ec: алиас и число в одном предложении, но 14,5 на странице —
    только конец чужого составного диапазона «14,5–14,6%» прогноза средней ставки.
    Одиночное значение утверждения этим не подтверждается (T7.109a внутри области)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_H,
        text=FOREIGN_RANGE_PAGE,
        claim_statement=STATEMENT_DEC2025,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


def test_single_number_in_same_region_is_attributed() -> None:
    """Контроль к предыдущему тесту: то же значение тем же текстом, но записанным одиночным
    числом в одной области с алиасом — новый режим приписывает значение."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_I,
        text=SINGLE_VALUE_PAGE,
        claim_statement=STATEMENT_DEC2025,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == NEW_PAIRING, decision


# ─── т6б: утверждение не должно противоречить приписыванию ((iii-а) в ко-локации) ─


def test_claim_naming_another_producer_refuses_the_mentioned_one() -> None:
    """Утверждение называет Росстат, страница в одной области со значением называет ЦБ,
    известный производитель утверждения — cbr: приписки нет (страница не перебивает
    формулировку самого утверждения; тот же дух, что у гварда T7.107)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_G,
        text=FAR_NAMED_PAGE,
        claim_statement=STATEMENT_ROSSTAT_TWIN,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.primary_key is None, decision
    assert decision.status != STATUS_DERIVATIVE, decision


def test_same_page_with_matching_claim_is_attributed() -> None:
    """Контроль к предыдущему тесту: тот же текст, то же известное множество и та же
    страница, но утверждение называет ЦБ — co-location работает (алиас далеко от значения
    за окна прежних режимов, но внутри одной области страницы)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_PORTAL_G,
        text=FAR_NAMED_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.pairing == NEW_PAIRING, decision


# ─── т7: цепочек нет — сборка ключей не читает новый режим ──────────────────────


def test_new_mode_record_gives_no_key_to_the_collectors() -> None:
    """Запись нового режима не кормит знание сама себя: чистый сборщик цикла
    (`known_primary_key_from_decision`) молчит на pairing нового режима."""
    decision = ValueAttributionDecision(
        status=STATUS_DERIVATIVE,
        primary_key="cbr",
        primary_name="Банк России",
        parent_uri="https://cbr.ru/",
        basis_fragment="Спрогнозировано решение Банка России по инфляции",
        pairing=NEW_PAIRING,
    )
    assert known_primary_key_from_decision(decision) is None


def test_real_new_mode_decision_gives_no_key() -> None:
    """То же на настоящем решении т1 (собранный режим): ключа от него нет."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_LENTA,
        text=LENTA_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset({"cbr"}),
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert known_primary_key_from_decision(decision) is None


# ─── регистрация pairing и версии ───────────────────────────────────────────────


def test_new_pairing_is_registered_in_scope() -> None:
    """Новое значение pairing зарегистрировано в `VALUE_ATTRIBUTION_PAIRINGS` (новых
    ключей scope нет), запись строгой формы с ним строится и читается обратно."""
    assert NEW_PAIRING in VALUE_ATTRIBUTION_PAIRINGS
    record = build_value_attribution(
        primary_key="cbr",
        primary_name="Банк России",
        primary_uri="https://cbr.ru/",
        parent_source_id="6e70379e-e1f5-2284-0000-000000000000",
        method="host-value-attribution-v15",
        basis_fragment="Спрогнозировано решение Банка России по инфляции",
        pairing=NEW_PAIRING,
    )
    assert record is not None
    parsed = parse_value_attribution({"value_attribution": record.as_scope()})
    assert parsed is not None and parsed.pairing == NEW_PAIRING


def test_bibliography_pairing_still_gives_no_key_to_collectors() -> None:
    """T7.108b не тронут: и третий, и новый режимы ключей не дают."""
    from apps.research_proxy.value_attribution import PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER

    for pairing in (PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER, NEW_PAIRING):
        decision = ValueAttributionDecision(
            status=STATUS_DERIVATIVE,
            primary_key="rosstat",
            primary_name="Росстат",
            parent_uri="https://rosstat.gov.ru/",
            basis_fragment="Источники: Росстат",
            pairing=pairing,
        )
        assert known_primary_key_from_decision(decision) is None, pairing


def test_value_method_version_bumped_page_method_untouched() -> None:
    """Смена поведения видна переатрибуции только через подъём версии метода значений:
    прежняя → текущая (сейчас v15 → v16, T7.126 часть а); страничный детектор не тронут
    (его версия не поднимается)."""
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v16"
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v10"


def test_strict_and_publication_modes_keep_their_own_pairings() -> None:
    """Прежние режимы не ослаблены и не переименованы: их pairing-значения те же."""
    assert PAIRING_VALUE_AND_PRIMARY == "value_and_primary_in_fragment"
    assert PAIRING_PUBLICATION_WINDOW == "publication_window"
    decision = attribute_value_in_fragment(
        canonical_uri=URL_LENTA,
        text=LENTA_PAGE,
        claim_statement=STATEMENT_CBR,
        known_claim_primaries=frozenset(),
    )
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY, decision
