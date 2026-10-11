"""T7.96: взаимоисключающие уточнители показателя (замечание приёмки, ADR-0033 §7.6).

Стендовый дефект (.92, вопрос 5a68172e, сессия bdd9ef8d): карточки
`7c8c9afe` («Наблюдаемая населением годовая инфляция … 15,1%») и `cc75e97f`
(«Инфляционные ожидания населения … 14,2%») — это ДВА разных показателя одного опроса
инФОМ (насколько цены уже выросли за год / какими они видятся на следующие 12 месяцев),
а витрина показывала их как «Другая запись того же показателя … расходится на 0,9 п.п.»
и зеркальную строку на второй карточке.

Причина: формулировки различаются ровно одним словом, поэтому пересечение словаря
показателя (3/4) выше порога `INDICATOR_OVERLAP_MIN` — одного порога для этой пары не
хватает ни при каком разумном его значении. Различие решает закрытый словарь уточнителей
`_INDICATOR_QUALIFIER_GROUPS` по образцу словаря территорий: отказ только когда ОБЕ
формулировки называют уточнитель и множества групп не пересекаются (замер всех 15
формулировок дампа claims_live.tsv — в STATUS.md T7.96 и тестом ниже).

Направление ошибки то же, что у территорий: правило может только ОТМЕНЯТЬ склейку.
"""

from __future__ import annotations

import itertools
from fractions import Fraction

import pytest

from apps.orchestrator.value_duplicate import (
    INDICATOR_OVERLAP_MIN,
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_value_duplicates,
    indicator_check,
    statement_indicator_words,
)

pytestmark = pytest.mark.unit

# ─── подлинные формулировки дампа claims_live.tsv (fixtures-t796, стенд .92) ──────────

LIVE_STATEMENTS: dict[str, str] = {
    "2d010d53": (
        "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского макроэкономического "
        "опроса Банка России составил 6,3%."
    ),
    "2f456e85": (
        "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 6,6% (по данным "
        "макроэкономического опроса Банка России за сентябрь 2026 года)."
    ),
    "391c4388": (
        "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% (по данным опроса инФОМ "
        "для Банка России)."
    ),
    "58e2d5d4": (
        "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского макроэкономического "
        "опроса Банка России составил 6,3%."
    ),
    "6d9a12ff": (
        "Медианная оценка годовой наблюдаемой инфляции населением России в декабре 2025 года составила 13,1% для "
        "граждан с накоплениями и 15,6% для граждан без накоплений (по данным опроса «инФОМ»)."
    ),
    "73d52835": "В пресс-релизе Банка России (event id 28251) годовая инфляция в 2025 году указана как 5,6%.",
    "7c8c9afe": (
        "Наблюдаемая населением годовая инфляция в России в сентябре 2026 года увеличилась до 15,1% (по данным опроса "
        "ООО «инФОМ»)."
    ),
    "7cc2b578": (
        "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже всех прогнозов аналитиков: "
        "прогноз Банка России — 6,5–7%, Минэкономразвития — 6,8%, консенсус-прогноз ЦБ — 6,6%"
    ),
    "9266248e": (
        "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59% (Банк России и "
        "Росстат, опубликовано 16–21 января 2026 г.)"
    ),
    "c6729d4c": "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%.",
    "c970bc08": (
        "Независимые оценки Сбера/СберCIB совпали с официальной цифрой Росстата: инфляция в России по итогам 2025 года "
        "составила 5,6% — расхождений с официальными данными нет"
    ),
    "cc75e97f": (
        "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% (по данным опроса ООО «инФОМ»)."
    ),
    "d8155167": "ООН состоит из 193 государств-членов.",
    "e189c80d": (
        "Официальная годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59% (по "
        "данным Росстата)."
    ),
    "e2c52955": (
        "В информационно-аналитическом комментарии Банка России «Инфляция в России» № 12 (120) за декабрь 2025 г. "
        "годовая инфляция за 2025 год приведена как 5,6%."
    ),
}

#: Склейки, которые ДОЛЖНЫ остаться: тот же показатель и тот же период либо тот же
#: показатель при другом периоде (период отсекает отдельное правило гейта и подборщика).
#: Пары даются в алфавитном порядке коротких id, как их перебирает тест таблицы.
EXPECTED_MATCHES: frozenset[tuple[str, str]] = frozenset(
    {
        # консенсус-прогноз аналитиков (опрос Банка России): конец 2025 и 2026 год
        ("2d010d53", "2f456e85"),
        # байт-в-байт одна формулировка (дубль T7.85: 2d010d53/58e2d5d4)
        ("2d010d53", "58e2d5d4"),
        # тот же консенсус-прогноз, другой период
        ("2f456e85", "58e2d5d4"),
        # наблюдаемая населением инфляция инФОМ: декабрь 2025 и сентябрь 2026
        ("391c4388", "7c8c9afe"),
        # официальный ИПЦ за 2025 год: 9266248e и e189c80d (дубль гейта T7.83)
        ("9266248e", "e189c80d"),
    }
)

#: Дефектная пара стенда: разные показатели одного опроса. Словарь даёт 3/4 — выше порога.
DEFECT_PAIR: tuple[str, str] = ("7c8c9afe", "cc75e97f")

# формулировка гейта (fixtures-t796/gate_claim_created_a09977e1.json): обе склейки обязаны
# выжить после правки — тексты причин взяты из payload'а `claim_created` той сессии
GATE_CLAIM_OFFICIAL = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила 5,59% "
    "(Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)
GATE_CLAIM_OBSERVED = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ для Банка России)."
)
GATE_REASON_OFFICIAL = (
    "indicator vocabulary overlap 1/1 (shared stems: ['банк', 'инфля', 'росси', 'росст'])"
)
GATE_REASON_OBSERVED = (
    "indicator vocabulary overlap 1/1 (shared stems: ['банка', 'инфля', 'инфом', 'наблю', "
    "'насел', 'опрос', 'росси'])"
)
ANCHOR_OFFICIAL_ID = "9266248e-2433-4a12-a3f7-b51ff10ad803"
ANCHOR_OBSERVED_ID = "391c4388-c0dc-461b-a843-23d4d6bfcd17"
ANCHOR_FORECAST_ID = "2d010d53-8818-4362-9475-387b71b63bb5"

# синтетика: ОДНО И ТО ЖЕ значение у разных показателей — ровно форма дефекта гейта T7.83a
SYNTH_OBSERVED_14_5 = "Наблюдаемая населением инфляция в России в декабре 2025 года составила 14,5%."
SYNTH_EXPECTATIONS_14_5 = "Инфляционные ожидания населения России в декабре 2025 года составили 14,5%."
# уточнителя нет ни с одной стороны (или он вне словаря) — одностороннее вето не срабатывает
SYNTH_BASE_INFLATION_5_4 = "Базовая инфляция в России по итогам 2025 года составила 5,4%."
SYNTH_PLAIN_INFLATION_5_4 = "Годовая инфляция в России по итогам 2025 года составила 5,4%."
SYNTH_OBSERVED_ONLY_5_4 = "Наблюдаемая инфляция в России по итогам 2025 года составила 5,4%."
# период и агрегация — НЕ уточнители из словаря: та же группа, склейка остаётся
SYNTH_ANNUAL_OBSERVED_14_5 = "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5%."
SYNTH_MONTHLY_OBSERVED_1_4 = "Наблюдаемая населением месячная инфляция в России в декабре 2025 года составила 1,4%."
SYNTH_MEDIAN_OBSERVED_13_1 = "Медианная оценка наблюдаемой инфляции в России в декабре 2025 года составила 13,1%."
SYNTH_PLAIN_OBSERVED_14_5 = "Наблюдаемая инфляция в России в декабре 2025 года составила 14,5%."
# обе группы названы в одной формулировке — конфликта нет, связка остаётся
SYNTH_BOTH_QUALIFIERS_14_5 = (
    "Наблюдаемая и ожидаемая населением инфляция в России в декабре 2025 года составила 14,5%."
)


def _overlap(claim_text: str, candidate_text: str) -> Fraction:
    """Пересечение словарей показателя — ровно то рациональное число, которое считает гейт."""
    claim_words = statement_indicator_words(claim_text)
    candidate_words = statement_indicator_words(candidate_text)
    return Fraction(len(claim_words & candidate_words), len(claim_words | candidate_words))


def _live_pairs() -> list[tuple[str, str]]:
    return list(itertools.combinations(sorted(LIVE_STATEMENTS), 2))


# ─── дефектная пара стенда ────────────────────────────────────────────────────────────


def test_stand_observed_inflation_and_expectations_are_different_indicators() -> None:
    """7c8c9afe (наблюдаемая инфляция) и cc75e97f (инфляционные ожидания) — разные показатели:
    вето называет, что именно разошлось (по образцу строк о территориях)."""
    left, right = LIVE_STATEMENTS[DEFECT_PAIR[0]], LIVE_STATEMENTS[DEFECT_PAIR[1]]
    check = indicator_check(left, right)
    assert check.matched is False
    assert "different indicator qualifiers" in check.detail
    assert "наблюдаемая" in check.detail
    assert "ожидания" in check.detail


def test_overlap_threshold_alone_cannot_decide_the_stand_pair() -> None:
    """Порог тут не при чём: пересечение словарей 3/4 заметно выше `INDICATOR_OVERLAP_MIN`.
    Именно поэтому различие решает словарь уточнителей, а не число в пороге."""
    left, right = LIVE_STATEMENTS[DEFECT_PAIR[0]], LIVE_STATEMENTS[DEFECT_PAIR[1]]
    overlap = _overlap(left, right)
    assert overlap == Fraction(3, 4)
    assert overlap > INDICATOR_OVERLAP_MIN
    # та же форма дефекта при одном и том же значении: 3/5 — ровно порог
    assert _overlap(SYNTH_OBSERVED_14_5, SYNTH_EXPECTATIONS_14_5) == INDICATOR_OVERLAP_MIN


def test_qualifier_veto_names_the_words_and_only_then_refuses() -> None:
    check = indicator_check(SYNTH_OBSERVED_14_5, SYNTH_EXPECTATIONS_14_5)
    assert check.matched is False
    assert check.detail == (
        "the statements name different indicator qualifiers "
        "['наблюдаемая'] vs ['ожидания']"
    )


# ─── гейт T7.83a: одно и то же число у разных показателей не склеивается ─────────────


def test_same_value_of_observed_inflation_and_expectations_is_not_glued() -> None:
    """Синтетика ровно формы дефекта: один тип, ОДНО значение 14,5%, тот же период, кандидат
    объявлен опорой — склейки всё равно нет, и причина называет уточнители."""
    decisions = find_value_duplicates(
        [_gate_claim(SYNTH_OBSERVED_14_5)],
        [_gate_candidate("c-expect", SYNTH_EXPECTATIONS_14_5)],
    )
    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept"
    assert entry["target"] is None
    assert "indicator does not match" in entry["reason"]
    assert "different indicator qualifiers" in entry["reason"]


def _gate_claim(statement: str, index: int = 0) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=2025,
        has_support_link=True,
        has_counter_link=False,
    )


def _gate_candidate(claim_id: str, statement: str) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=2025,
        relied=True,
    )


# ─── вето одностороннее: вне словаря и при отсутствии уточнителя с одной стороны ─────


def test_qualifier_free_wordings_are_still_merged() -> None:
    """«Базовая инфляция» и просто «годовая инфляция»: ни одного уточнителя из словаря —
    склейка остаётся (анализ (б): базовая/общая в закрытый словарь не внесена)."""
    assert _overlap(SYNTH_BASE_INFLATION_5_4, SYNTH_PLAIN_INFLATION_5_4) == Fraction(2, 3)
    assert indicator_check(SYNTH_BASE_INFLATION_5_4, SYNTH_PLAIN_INFLATION_5_4).matched is True


def test_one_sided_qualifier_is_not_a_conflict() -> None:
    """Уточнитель назван только одной формулировкой — конфликта нет (та же conservative-логика,
    что у территорий): неполнота словаря не создаёт отказа и не создаёт склейки. Период
    («годовая»/«месячная») и агрегация («медианная») — не уточнители: это та же группа."""
    # слева есть «наблюдаемая», справа уточнителя нет вовсе
    assert _overlap(SYNTH_OBSERVED_ONLY_5_4, SYNTH_PLAIN_INFLATION_5_4) == Fraction(2, 3)
    assert indicator_check(SYNTH_OBSERVED_ONLY_5_4, SYNTH_PLAIN_INFLATION_5_4).matched is True
    # та же группа с обеих сторон: разная периодность и другая агрегация — не конфликт
    assert indicator_check(SYNTH_MEDIAN_OBSERVED_13_1, SYNTH_PLAIN_OBSERVED_14_5).matched is True
    assert indicator_check(SYNTH_ANNUAL_OBSERVED_14_5, SYNTH_MONTHLY_OBSERVED_1_4).matched is True


def test_statement_naming_both_qualifiers_is_not_split_from_either() -> None:
    """«Наблюдаемая И ожидаемая населением инфляция» (одна формулировка называет оба показателя)
    не расходится ни с ожиданиями, ни с наблюдаемой инфляцией."""
    assert indicator_check(SYNTH_BOTH_QUALIFIERS_14_5, SYNTH_EXPECTATIONS_14_5).matched is True
    assert indicator_check(SYNTH_BOTH_QUALIFIERS_14_5, SYNTH_OBSERVED_14_5).matched is True


def test_territory_veto_is_named_before_the_qualifier_veto() -> None:
    """Детерминированный порядок отказов: территория называется раньше уточнителя (тот же
    порядок, что был до T7.96 — прежние строки причин не поменялись)."""
    check = indicator_check(
        "Наблюдаемая инфляция в России в сентябре 2026 года составила 15,1%.",
        "Ожидаемая инфляция в Казахстане в сентябре 2026 года составила 15,1%.",
    )
    assert check.matched is False
    assert "different territories" in check.detail
    assert "qualifiers" not in check.detail


# ─── слова атрибуции из словаря показателя НЕ исключены (замер варианта В) ────────────


def test_attribution_words_stay_in_the_indicator_vocabulary() -> None:
    """«опрос», «инФОМ», «ООО» остаются значимыми словами показателя. Их исключение не чинит
    этот дефект (пара поднимается до 3/5 — всё ещё склейка) и добавляет склейки другим парам
    дампа: замер V1/V2/V3 в STATUS.md T7.96."""
    assert statement_indicator_words(LIVE_STATEMENTS["7c8c9afe"]) == frozenset(
        {"инфля", "инфом", "наблю", "насел", "ооо", "опрос", "росси"}
    )
    assert statement_indicator_words(LIVE_STATEMENTS["cc75e97f"]) == frozenset(
        {"инфля", "инфом", "ожида", "насел", "ооо", "опрос", "росси"}
    )


# ─── обе склейки гейта из фикстуры a09977e1 сохранены байт-в-байт ────────────────────


def test_gate_fixture_glues_keep_their_indicator_details() -> None:
    """T7.83a не сломан: склейка 9266248e↔e189c80d и склейка наблюдаемой инфляции 391c4388
    дают ровно те же детали сверки, что записаны в payload'е `claim_created` стенда."""
    left = indicator_check(GATE_CLAIM_OFFICIAL, LIVE_STATEMENTS["9266248e"])
    right = indicator_check(GATE_CLAIM_OBSERVED, LIVE_STATEMENTS["391c4388"])
    assert left.matched is True and left.detail == GATE_REASON_OFFICIAL
    assert right.matched is True and right.detail == GATE_REASON_OBSERVED


def test_gate_fixture_decisions_are_unchanged() -> None:
    """Весь прогон гейта по фикстуре a09977e1 (три операции, три опоры) даёт ровно две склейки
    с прежними причинами; третья операция молчит (тип якоря другой, T7.85)."""
    decisions = find_value_duplicates(
        [
            _gate_claim(GATE_CLAIM_OFFICIAL, index=0),
            _gate_claim(GATE_CLAIM_OBSERVED, index=1),
            _gate_claim(LIVE_STATEMENTS["2d010d53"], index=2),
        ],
        [
            _gate_candidate(ANCHOR_OFFICIAL_ID, LIVE_STATEMENTS["9266248e"]),
            _gate_candidate(ANCHOR_OBSERVED_ID, LIVE_STATEMENTS["391c4388"]),
            _gate_candidate(ANCHOR_FORECAST_ID, LIVE_STATEMENTS["2d010d53"]),
        ],
    )
    decisions = [entry for entry in decisions if entry["claim_index"] != 2]
    assert [entry["action"] for entry in decisions] == ["reverified", "reverified"]
    assert [entry["target"] for entry in decisions] == [ANCHOR_OFFICIAL_ID, ANCHOR_OBSERVED_ID]
    assert GATE_REASON_OFFICIAL in decisions[0]["reason"]
    assert GATE_REASON_OBSERVED in decisions[1]["reason"]


# ─── таблица 15 формулировок дампа ───────────────────────────────────────────────────


def test_live_formulation_table_expected_indicator_matches() -> None:
    """Все пары 15 живых формулировок claims_live.tsv: совпадения ожидаются ровно там, где
    показатель один и тот же (или тот же в другом периоде). Дефектная пара отказывает."""
    matched = {pair for pair in _live_pairs() if indicator_check(*_texts(pair)).matched}
    assert matched == set(EXPECTED_MATCHES)


def test_live_formulation_table_refuses_the_defect_pair_with_qualifier_reason() -> None:
    check = indicator_check(*_texts(DEFECT_PAIR))
    assert check.matched is False
    assert "different indicator qualifiers" in check.detail


def test_indicator_match_is_never_wider_than_the_vocabulary_threshold() -> None:
    """Направление ошибки (г): `matched` подразумевает, что порог пересечения этой парой уже
    выполнен — вето только снимает склейку и не может дать совпадения, которого словарь не дал."""
    for pair in _live_pairs():
        claim_text, candidate_text = _texts(pair)
        if indicator_check(claim_text, candidate_text).matched:
            assert _overlap(claim_text, candidate_text) >= INDICATOR_OVERLAP_MIN, pair


def _texts(pair: tuple[str, str]) -> tuple[str, str]:
    return LIVE_STATEMENTS[pair[0]], LIVE_STATEMENTS[pair[1]]
