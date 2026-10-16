"""T7.118a: Ф3 — имя названо только когда в формулировке найдены ВСЕ значимые основы имени.

Дефект найден менеджером на реальных данных стенда (прогон `honesty-report`): прежний
порядок «достаточно одной совпавшей основы имени» глушил Ф3 — для подписи «Банк России»
хватало слова «России», а оно есть почти в каждой формулировке стенда. Примеры взяты из
утверждений приёмки `.92` (STATUS T7.118 / T7.118a, docs/eval/CONTROL-SET.md): фикстуры
cc75e97f и 391c4388 обязаны теперь флаговаться, c6729d4c / 136a0ca0 / 822072db / 7cc2b578 —
сохранять прежнее поведение. Значимые основы — без чисел и без общей основы-якоря страны
(та же «росси», что у Ф2); якорь выведен тем же `_prefix_stem`, новых словарей нет.
"""

from __future__ import annotations

import pytest

from packages.memory import honesty_report
from packages.memory.honesty_report import off_topic_flag, producer_not_named_flag

pytestmark = [pytest.mark.unit]

# пары (имя, ключ) — как их записывает атрибущный словарь apps/ (STATUS T7.118)
CBR = ("Банк России", "cbr")
MINEC = ("Минэкономразвития России", "minec")
ROSSTAT = ("Росстат", "rosstat")
RUSSIA_ONLY = ("Россия", "cbr")

# cc75e97f: ожидания населения по опросу инФОМ, подпись «Опубликовано производителем (Банк России)»
STMT_CC75E97F = (
    "Инфляционные ожидания населения России в сентябре 2026 года составили 14,2% "
    "(по данным опроса ООО «инФОМ»)."
)
# 391c4388: наблюдаемая населением инфляция по тому же опросу инФОМ, та же подпись
STMT_391C4388 = (
    "Наблюдаемая населением годовая инфляция в России в декабре 2025 года составила 14,5% "
    "(по данным опроса инФОМ)."
)
# c6729d4c и 136a0ca0: Банк России назван в самой формулировке
STMT_C6729D4C = "Прогноз Банка России по годовой инфляции на 2026 год — от 6,0 до 7,0%."
STMT_136A0CA0 = "Банк России в июле 2026 года повысил прогноз годовой инфляции до 6,5%."
# 822072db: Минэкономразвития названо без слова «России»
STMT_822072DB = "Минэкономразвития повысило прогноз по инфляции до 6,8%."
# 7cc2b578: Росстат в формулировке нет (подпись — Росстат) — прежнее поведение сохранено
STMT_7CC2B578 = (
    "Фактическая годовая инфляция в России по итогам 2025 года (5,59%) оказалась ниже "
    "всех прогнозов аналитиков."
)
STMT_ROSSTAT_NAMED = "Годовая инфляция в сентябре замедлилась до 2,2% по данным Росстата."


# ─── Ф3 сработал там, где раньше молчал (красные на f6b8edc) ───────────────────────────


def test_f3_fires_for_infom_expectations_with_cbr_signature() -> None:
    flag = producer_not_named_flag(CBR, STMT_CC75E97F)
    assert flag is not None and flag.code == "f3", (
        "одна основа «росси» не делает имя «Банк России» названным"
    )


def test_f3_fires_for_infom_observed_inflation_with_cbr_signature() -> None:
    flag = producer_not_named_flag(CBR, STMT_391C4388)
    assert flag is not None and flag.code == "f3", (
        "одна основа «росси» не делает имя «Банк России» названным"
    )


# ─── прежнее поведение сохранено (зелёные и на старом коде) ─────────────────────────────


def test_f3_quiet_when_bank_of_russia_named_in_statement() -> None:
    assert producer_not_named_flag(CBR, STMT_C6729D4C) is None
    assert producer_not_named_flag(CBR, STMT_136A0CA0) is None


def test_f3_quiet_for_mineconomrazvitiya_named_without_country_word() -> None:
    # значимая основа одна («минэко» без якоря) — названа → флага нет
    assert producer_not_named_flag(MINEC, STMT_822072DB) is None


def test_f3_fires_for_rosstat_signature_absent_from_statement() -> None:
    # 7cc2b578: «России» в формулировке есть, Росстата нет — флаг обязан остаться
    flag = producer_not_named_flag(ROSSTAT, STMT_7CC2B578)
    assert flag is not None and flag.code == "f3"


def test_f3_quiet_when_rosstat_named() -> None:
    assert producer_not_named_flag(ROSSTAT, STMT_ROSSTAT_NAMED) is None


def test_anchor_only_name_compares_on_full_set_as_before() -> None:
    # имя только из якоря («Россия»): после исключения якоря основ не осталось —
    # сравниваем по полному набору, как прежде
    assert producer_not_named_flag(RUSSIA_ONLY, STMT_C6729D4C) is None
    flag = producer_not_named_flag(
        RUSSIA_ONLY, "Инфляция в Тармении ускорилась до 14% по оценке ведомства"
    )
    assert flag is not None and flag.code == "f3"


def test_country_anchor_is_the_same_stem_f2_shows_as_generic() -> None:
    anchor = honesty_report._COUNTRY_ANCHOR_STEM
    flag = off_topic_flag(
        "Уровень безработицы в России в 2026 году составил около трёх процентов",
        "Средние зарплаты в России в 2026 году",
    )
    assert flag is not None and f"«{anchor}»" in (flag.message or "")
