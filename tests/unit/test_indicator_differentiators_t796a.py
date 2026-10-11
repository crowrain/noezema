"""T7.96a: разные показатели одного опроса — словарь респондента и словарь горизонта
(замечания приёмки после T7.96, ADR-0033 §11).

Замечания менеджера (стенд .92, HEAD c221369, вопрос 5a68172e, сессия bdd9ef8d; тексты
артефактов research.fetch собраны в `~/dsh1/fixtures-t796a/`, замер `indicator_check` на
c221369 дал во всех четырёх пробах `matched=True`):

(4) «Инфляционные ожидания населения России на годовом горизонте … 14,2%» против
    «… на 5 лет вперёд … 10,7%» — перекрытие словаря 2/3 (имя показателя идентично);
(5) «Инфляционные ожидания НАСЕЛЕНИЯ России … 14,2%» против «… ПРЕДПРИЯТИЙ России … 7,0%»
    — ровно порог 3/5; тот же дефект в форме гейта T7.83a при ОДНОМ значении (7,0 и 7,0);
(2) «Годовая инфляция ПО ОЦЕНКЕ населения России … 15,1%» против ожиданий 14,2% — 6/7;
(3) «Население России НАБЛЮДАЕТ годовую инфляцию … 15,1%» против ожиданий 14,2% — 2/3.

T7.96 закрыл только класс «наблюдаемая инфляция / инфляционные ожидания». Здесь тем же
образом добавлены ещё два замкнутых словаря различий (`_INDICATOR_RESPONDENT_GROUPS`,
`_INDICATOR_HORIZON_MARKERS` + `_INDICATOR_HORIZON_SCALES`) и расширена прежняя группа
«состоявшегося» глагольными формами («наблюдает», «ощущает») и словом «оценка*».

Все четыре правила двусторонние (отказ только когда ОБЕ стороны назвали различитель и
группы не пересеклись) и только ОТМЕНЯЮТ склейку: ни один словарь-вето не участвует в
подсчёте перекрытия, поэтому ни одна пара не может стать «тем же показателем» из-за правки.
Замер всех пар корпуса тестов и дампа (194 376 пар, 1 235 склеек на c221369) — в STATUS.md
T7.96a: прежние законные склейки не сломаны, новых склеек нет.
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

# ─── пробы менеджера (fixtures-t796a/README.txt, дословно) ──────────────────────────────

PROBE_ANNUAL_HORIZON_14_2 = (
    "Инфляционные ожидания населения России на годовом горизонте в сентябре 2026 года "
    "составили 14,2% (опрос инФОМ)."
)
PROBE_FIVE_YEAR_FORWARD_10_7 = (
    "Инфляционные ожидания населения России на 5 лет вперёд в сентябре 2026 года снизились "
    "до 10,7% (опрос инФОМ)."
)
PROBE_POPULATION_SHORT_14_2 = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2%."
)
PROBE_ENTERPRISES_SHORT_7_0 = (
    "Инфляционные ожидания предприятий России в сентябре 2026 года составили 7,0%."
)
PROBE_ASSESSMENT_15_1 = (
    "Годовая инфляция по оценке населения России в сентябре 2026 года — 15,1% "
    "(по данным опроса ООО «инФОМ»)."
)
PROBE_VERB_OBSERVE_15_1 = (
    "Население России наблюдает годовую инфляцию на уровне 15,1% "
    "(по данным опроса ООО «инФОМ»)."
)
#: ожидания населения из дампа claims_live.tsv (карта cc75e97f) — вторая сторона проб (2) и (3)
LIVE_EXPECTATIONS_14_2 = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% "
    "(по данным опроса ООО «инФОМ»)."
)

# ─── синтетика: пределы и границы словарей ──────────────────────────────────────────────

#: тот же годовой горизонт, названный другим словом — склейка обязана остаться
SYNTH_ANNUAL_FORWARD_14_2 = (
    "Инфляционные ожидания населения России на год вперёд в сентябре 2026 года составили 14,2% "
    "(опрос инФОМ)."
)
#: горизонт назван только одной формулировкой — вето молчит
SYNTH_NO_HORIZON_14_2 = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% (опрос инФОМ)."
)
#: ПРЕДЕЛ: шкала названа без маркера направления — различить горизонт нечем
SYNTH_FIVE_YEAR_ADJECTIVE_10_7 = (
    "Пятилетние инфляционные ожидания населения России в сентябре 2026 года составили 10,7% "
    "(опрос инФОМ)."
)
#: ПРЕДЕЛ: «следующие 12 месяцев» — это числа, а правило читает слова
SYNTH_NEXT_MONTHS_14_2 = (
    "Инфляционные ожидания населения России за следующие 12 месяцев в сентябре 2026 года "
    "составили 14,2% (опрос инФОМ)."
)
#: оба респондента в одной формулировке — конфликта нет
SYNTH_BOTH_RESPONDENTS_14_2 = (
    "Инфляционные ожидания населения и предприятий России в сентябре 2026 года составили "
    "14,2% (опрос инФОМ)."
)
#: одна и та же группа респондента разными словами
SYNTH_CITIZENS_14_2 = (
    "Инфляционные ожидания граждан России в сентябре 2026 года составили 14,2% (опрос инФОМ)."
)
#: та же группа респондента и то же значение, что у синтетики гейта
SYNTH_CITIZENS_7_0 = (
    "Инфляционные ожидания граждан России в сентябре 2026 года составили 7,0% "
    "(по данным опроса ООО «инФОМ»)."
)
SYNTH_BUSINESS_7_0 = (
    "Ценовые инфляционные ожидания бизнеса России в сентябре 2026 года составили 7,0% "
    "(опрос инФОМ)."
)
SYNTH_COMPANIES_7_0 = (
    "Ценовые инфляционные ожидания компаний России в сентябре 2026 года составили 7,0% "
    "(опрос инФОМ)."
)
#: синтетика гейта T7.83a: ОДНО И ТО ЖЕ значение у ожиданий населения и ожиданий предприятий
SYNTH_POPULATION_7_0 = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 7,0% "
    "(по данным опроса ООО «инФОМ»)."
)
SYNTH_ENTERPRISES_7_0 = (
    "Ценовые инфляционные ожидания предприятий России в сентябре 2026 года составили 7,0% "
    "(по данным опроса ООО «инФОМ»)."
)
#: «медианная оценка ожиданий» — оценка того же показателя, расщеплять нельзя
SYNTH_MEDIAN_OF_EXPECTATIONS_14_2 = (
    "Медианная оценка инфляционных ожиданий населения России в сентябре 2026 года составила "
    "14,2% (опрос инФОМ)."
)
#: обе стороны назвали группу «состоявшегося» разными словами — одной группы достаточно
SYNTH_ASSESSMENT_AND_OBSERVED_15_1 = (
    "Годовая инфляция по оценке населения России в сентябре 2026 года составила 15,1%."
)
SYNTH_OBSERVED_15_1 = (
    "Наблюдаемая населением годовая инфляция в России в сентябре 2026 года увеличилась "
    "до 15,1%."
)
#: порядок отказов: конфликты сразу по респонденту и по горизонту
SYNTH_POP_ANNUAL_7_0 = (
    "Инфляционные ожидания населения России на годовом горизонте в сентябре 2026 года "
    "составили 7,0% (опрос инФОМ)."
)
SYNTH_ENT_FIVE_YEAR_7_0 = (
    "Ценовые инфляционные ожидания предприятий России на 5 лет вперёд составили 7,0% "
    "(опрос инФОМ)."
)

ALL_FORMULATIONS: tuple[str, ...] = (
    PROBE_ANNUAL_HORIZON_14_2,
    PROBE_FIVE_YEAR_FORWARD_10_7,
    PROBE_POPULATION_SHORT_14_2,
    PROBE_ENTERPRISES_SHORT_7_0,
    PROBE_ASSESSMENT_15_1,
    PROBE_VERB_OBSERVE_15_1,
    LIVE_EXPECTATIONS_14_2,
    SYNTH_ANNUAL_FORWARD_14_2,
    SYNTH_NO_HORIZON_14_2,
    SYNTH_FIVE_YEAR_ADJECTIVE_10_7,
    SYNTH_NEXT_MONTHS_14_2,
    SYNTH_BOTH_RESPONDENTS_14_2,
    SYNTH_CITIZENS_14_2,
    SYNTH_CITIZENS_7_0,
    SYNTH_BUSINESS_7_0,
    SYNTH_COMPANIES_7_0,
    SYNTH_POPULATION_7_0,
    SYNTH_ENTERPRISES_7_0,
    SYNTH_MEDIAN_OF_EXPECTATIONS_14_2,
    SYNTH_ASSESSMENT_AND_OBSERVED_15_1,
    SYNTH_OBSERVED_15_1,
    SYNTH_POP_ANNUAL_7_0,
    SYNTH_ENT_FIVE_YEAR_7_0,
)


def _overlap(claim_text: str, candidate_text: str) -> Fraction:
    """Пересечение словарей показателя — ровно то рациональное число, которое считает гейт."""
    claim_words = statement_indicator_words(claim_text)
    candidate_words = statement_indicator_words(candidate_text)
    return Fraction(len(claim_words & candidate_words), len(claim_words | candidate_words))


# ─── (4) горизонт ожидания: годовой против «на 5 лет вперёд» ────────────────────────────


def test_annual_horizon_and_five_year_forward_are_different_indicators() -> None:
    """Проба (4): имена показателя идентичны, словарь даёт 2/3 — выше порога. Решает
    словарь горизонта, и отказ называет слова, которыми расходится горизонт."""
    assert _overlap(PROBE_ANNUAL_HORIZON_14_2, PROBE_FIVE_YEAR_FORWARD_10_7) == Fraction(2, 3)
    check = indicator_check(PROBE_ANNUAL_HORIZON_14_2, PROBE_FIVE_YEAR_FORWARD_10_7)
    assert check.matched is False
    assert "different indicator horizons" in check.detail
    assert "'горизонте'" in check.detail and "'лет'" in check.detail


def test_one_horizon_named_in_two_words_stays_the_same_indicator() -> None:
    """«на годовом горизонте» и «на год вперёд» — одна группа шкалы: склейка остаётся."""
    assert _overlap(PROBE_ANNUAL_HORIZON_14_2, SYNTH_ANNUAL_FORWARD_14_2) == Fraction(3, 4)
    assert indicator_check(PROBE_ANNUAL_HORIZON_14_2, SYNTH_ANNUAL_FORWARD_14_2).matched is True


def test_horizon_named_by_only_one_statement_is_not_a_conflict() -> None:
    """Вето двустороннее: правая формулировка горизонт не называет — отказа нет."""
    assert _overlap(PROBE_ANNUAL_HORIZON_14_2, SYNTH_NO_HORIZON_14_2) == Fraction(6, 7)
    assert indicator_check(PROBE_ANNUAL_HORIZON_14_2, SYNTH_NO_HORIZON_14_2).matched is True


def test_calendar_words_alone_never_become_a_horizon() -> None:
    """Слова периода («в сентябре 2026 года», «по итогам 2025 года») горизонтом не считаются:
    их по-прежнему решает правило периода гейта (ADR-0033 §10.5), а словарь-вето молчит."""
    assert indicator_check(LIVE_EXPECTATIONS_14_2, SYNTH_NO_HORIZON_14_2).matched is True


def test_horizon_without_direction_marker_stays_an_accepted_limit() -> None:
    """ЗАФИКСИРОВАННЫЙ ПРЕДЕЛ: «пятилетние ожидания» и «за следующие 12 месяцев» не названы
    маркером направления («вперёд»/«горизонт»), поэтому правило их не различает — склейка
    остаётся на решении словаря показателя (3/4). Расширять словарь без живой формы с
    маркером нельзя: окно шире нуля начинает читать шкалой слово из соседней конструкции."""
    assert _overlap(SYNTH_FIVE_YEAR_ADJECTIVE_10_7, PROBE_ANNUAL_HORIZON_14_2) == Fraction(3, 4)
    assert indicator_check(SYNTH_FIVE_YEAR_ADJECTIVE_10_7, PROBE_ANNUAL_HORIZON_14_2).matched is True
    assert _overlap(SYNTH_NEXT_MONTHS_14_2, PROBE_ANNUAL_HORIZON_14_2) == Fraction(3, 4)
    assert indicator_check(SYNTH_NEXT_MONTHS_14_2, PROBE_ANNUAL_HORIZON_14_2).matched is True


# ─── (5) чьи показания опроса названы: население против предприятий ──────────────────────


def test_population_and_enterprise_expectations_are_different_indicators() -> None:
    """Проба (5): перекрытие ровно на пороге (3/5) — склейку отменяет словарь респондента,
    и отказ называет оба слова."""
    assert _overlap(PROBE_POPULATION_SHORT_14_2, PROBE_ENTERPRISES_SHORT_7_0) == Fraction(3, 5)
    check = indicator_check(PROBE_POPULATION_SHORT_14_2, PROBE_ENTERPRISES_SHORT_7_0)
    assert check.matched is False
    assert "different survey respondents" in check.detail
    assert "'населения'" in check.detail and "'предприятий'" in check.detail


def test_one_value_two_respondents_is_not_a_value_duplicate() -> None:
    """Форма дефекта гейта T7.83a: то же десятичное значение (7,0), тот же период, кандидат
    объявлен опорой — склейки всё равно нет, а причина называет респондента. Обе стороны
    проверяются отдельно, чтобы в решении не было двусмысленности из-за двух кандидатов."""
    for statement, other in (
        (SYNTH_POPULATION_7_0, SYNTH_ENTERPRISES_7_0),
        (SYNTH_ENTERPRISES_7_0, SYNTH_POPULATION_7_0),
    ):
        decisions = find_value_duplicates(
            [_claim(statement)],
            [_candidate("a1", other)],
        )
        assert [entry["action"] for entry in decisions] == ["kept"]
        assert decisions[0]["target"] is None
        assert "the indicator does not match" in decisions[0]["reason"]
        assert "different survey respondents" in decisions[0]["reason"]
    # контроль того же гейта: та же формулировка против себя склеивается по-прежнему
    control = find_value_duplicates(
        [_claim(SYNTH_POPULATION_7_0)],
        [_candidate("a1", SYNTH_CITIZENS_7_0)],
    )
    assert [entry["action"] for entry in control] == ["reverified"]


def test_one_respondent_named_in_two_words_stays_the_same_indicator() -> None:
    """«населения» и «граждан», «бизнеса» и «компаний» — по одной группе с каждой стороны."""
    assert indicator_check(LIVE_EXPECTATIONS_14_2, SYNTH_CITIZENS_14_2).matched is True
    assert indicator_check(SYNTH_BUSINESS_7_0, SYNTH_COMPANIES_7_0).matched is True


def test_statement_naming_both_respondents_is_not_split_from_either() -> None:
    """Формулировка называет оба респондента — она не расходится ни с одним из них."""
    assert (
        indicator_check(SYNTH_BOTH_RESPONDENTS_14_2, SYNTH_NO_HORIZON_14_2).matched is True
    )


# ─── (2) и (3): оценка/наблюдает против ожиданий — та же группа состоявшегося ────────────


def test_realized_assessment_wording_and_expectations_are_different_indicators() -> None:
    """Пробы (2) и (3): «по оценке населения» (6/7) и «население наблюдает» (2/3) — оба выше
    порога; теперь это отказ, называющий расходившееся слово."""
    assert _overlap(PROBE_ASSESSMENT_15_1, LIVE_EXPECTATIONS_14_2) == Fraction(6, 7)
    assessment = indicator_check(PROBE_ASSESSMENT_15_1, LIVE_EXPECTATIONS_14_2)
    assert assessment.matched is False
    assert "different indicator qualifiers" in assessment.detail
    assert "'оценке'" in assessment.detail

    assert _overlap(PROBE_VERB_OBSERVE_15_1, LIVE_EXPECTATIONS_14_2) == Fraction(2, 3)
    verb = indicator_check(PROBE_VERB_OBSERVE_15_1, LIVE_EXPECTATIONS_14_2)
    assert verb.matched is False
    assert "different indicator qualifiers" in verb.detail
    assert "'наблюдает'" in verb.detail


def test_assessment_word_stays_out_of_the_indicator_vocabulary() -> None:
    """«оценка*» читает только словарь-вето: словарь показателя (`statement_indicator_words`)
    её по-прежнему не содержит — перекрытие от правки не изменилось ни для одной пары."""
    assert statement_indicator_words(PROBE_ASSESSMENT_15_1) == frozenset(
        {"инфля", "инфом", "насел", "ооо", "опрос", "росси"}
    )


def test_median_of_expectations_is_not_split_by_the_assessment_word() -> None:
    """«Медианная оценка инфляционных ожиданий» называет обе группы сразу — с ожиданиями она
    остаётся тем же показателем (иначе правка добавила бы отказ там, где его не было)."""
    assert indicator_check(SYNTH_MEDIAN_OF_EXPECTATIONS_14_2, SYNTH_NO_HORIZON_14_2).matched is True


def test_two_words_of_the_same_group_do_not_split_the_indicator() -> None:
    """«по оценке населения» и «наблюдаемая населением инфляция» — одна группа: склейка 3/4
    остаётся (правило различает группы, а не любые слова состояния)."""
    assert _overlap(SYNTH_ASSESSMENT_AND_OBSERVED_15_1, SYNTH_OBSERVED_15_1) == Fraction(3, 4)
    assert (
        indicator_check(SYNTH_ASSESSMENT_AND_OBSERVED_15_1, SYNTH_OBSERVED_15_1).matched is True
    )


# ─── порядок отказов и направление ошибки ────────────────────────────────────────────────


def test_respondent_veto_is_named_before_the_horizon_veto() -> None:
    """Детерминированный порядок: сначала уточнители, затем респондент, затем горизонт —
    прежние строки причин (территория, T7.96) не поменялись."""
    check = indicator_check(SYNTH_POP_ANNUAL_7_0, SYNTH_ENT_FIVE_YEAR_7_0)
    assert check.matched is False
    assert "different survey respondents" in check.detail
    assert "horizons" not in check.detail


def test_new_vetoes_can_only_cancel_a_merge() -> None:
    """Направление ошибки (г): `matched` подразумевает, что порог пересечения этой парой уже
    выполнен — ни один новый словарь не может сделать пару «тем же показателем»."""
    for claim_text, candidate_text in itertools.combinations(ALL_FORMULATIONS, 2):
        if indicator_check(claim_text, candidate_text).matched:
            assert _overlap(claim_text, candidate_text) >= INDICATOR_OVERLAP_MIN, (
                claim_text,
                candidate_text,
            )


def _claim(statement: str, index: int = 0) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=2026,
        has_support_link=True,
        has_counter_link=False,
    )


def _candidate(claim_id: str, statement: str) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type="temporal_fact",
        as_of_year=2026,
        relied=True,
    )
