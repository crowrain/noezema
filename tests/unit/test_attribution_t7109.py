"""Unit: T7.109 (часть а + часть б2) — равнозначность чисел, оба конца диапазона, ближайший
первоисточник (решение пользователя 2026-10-13).

Репродюсер стендовых страниц: interfax.ru/business/1121468 и kommersant.ru/doc/8924678
(`tests/fixtures/t7109/`, тексты зафиксированы дословно). Три замера, данные менеджером:

1. утверждение «…составляет 6,0–7,0%» с interfax-улики НЕ атрибуцировалось вообще: диапазон
   «6-7%» на странице давал одно измеренное число «7», а значение утверждения канонизировалось
   в «7.0» — и на стыке этих двух потерь реле (alias + глагол прогноза + число) молчал.
2. утверждение «…до 6–7%» атрибуцировалось Банку России (relay работал ровно через правый
   конец диапазона).
3. утверждение про 6,8% Минэкономразвития приписывалось Банку России: Минэкономразвития вообще
   не было в словаре первоисточников, и единственное видимое значение рядом (6-7% ЦБ) стало
   «его» числом.

Правило близости (часть б2): значение приписывается тому алиасу, который стоит ближе всего
перед значением; собственное число алиаса, ближайшее после его вхождения, обязано нести
значение утверждения. Ни одного однозначного кандидата — прежнее `ambiguous_primaries`.
Шаблоны, relay-гварды, публикационное окно и правило пары не ослаблены: близость включается
только там, где прежние режимы честно отказали (и только во фрагментах с ДВУМЯ алиасами).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_SELF_PRIMARY,
    detect_source_attribution,
    is_home_host,
    named_primary_keys,
    primary_source,
)
from apps.research_proxy.value_attribution import (
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_PAIRING_STRICT,
    attribute_value_in_fragment,
    claim_value_numbers,
    claim_values_found_in_texts,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "t7109"

INTERFAX_URL = "https://www.interfax.ru/business/1121468"
KOMMERSANT_URL = "https://www.kommersant.ru/doc/8924678"

# ─── замеры менеджера (утверждения стенда 2026-10-13) ────────────────────────

STATEMENT_RANGE_FULL = "Прогноз Банка России по инфляции на 2026 год составляет 6,0–7,0%."
STATEMENT_RANGE_SHORT = "Банк России повысил прогноз инфляции на 2026 год до 6–7%."
STATEMENT_MINEC_68 = "Минэкономразвития повысило прогноз инфляции на 2026 год до 6,8%."
STATEMENT_ROSSTAT_34 = (
    "Реальные зарплаты в России в июне 2026 года увеличились на 3,4% в годовом выражении."
)
STATEMENT_MINEC_22 = (
    "Минэкономразвития прогнозирует рост реальных зарплат в России в 2026 году на 2,2% "
    "(майский прогноз)."
)


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# ─── фикстуры зафиксированы дословно: ключевые фразы трёх замеров на месте ───


def test_fixtures_carry_the_measured_sentences() -> None:
    interfax = _fixture("interfax_1121468.txt")
    kommersant = _fixture("kommersant_8924678.txt")
    assert "превысила верхнюю границу прогноза ЦБ на 2026 год (6-7%)" in interfax
    assert "и прогноз Минэкономразвития (6,8%)" in interfax
    assert "подтвердил свой прогноз" in interfax
    assert "повысило свой прогноз по инфляции на 2026 год до 6,8%" in interfax
    assert "сообщил Росстат" in kommersant
    assert "Согласно майскому прогнозу Минэкономразвития" in kommersant


# ─── т1: «6,0–7,0%» с interfax-улики → пересказ Банка России (было: отказа нет) ──


def test_range_claim_with_decimal_ends_is_attributed_to_cbr() -> None:
    assert claim_value_numbers(STATEMENT_RANGE_FULL) == ("6", "7")
    decision = attribute_value_in_fragment(
        canonical_uri=INTERFAX_URL,
        text=_fixture("interfax_1121468.txt"),
        claim_statement=STATEMENT_RANGE_FULL,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "cbr"
    assert decision.primary_name == "Банк России"
    assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT


# ─── т2: «6–7%» → прежнее решение Банку России, теперь с обоими концами ──────


def test_short_range_claim_still_attributed_to_cbr() -> None:
    # было (v7): значения утверждения ('7',) — левый конец диапазона терялся при чтении
    # утверждения; стало: ('6', '7'). Само решение не изменилось — relay ЦБ работал и работал.
    assert claim_value_numbers(STATEMENT_RANGE_SHORT) == ("6", "7")
    decision = attribute_value_in_fragment(
        canonical_uri=INTERFAX_URL,
        text=_fixture("interfax_1121468.txt"),
        claim_statement=STATEMENT_RANGE_SHORT,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "cbr"


# ─── т3: 6,8% Минэкономразвития с той же страницы → Минэкономразвитию (было: ЦБ) ──


def test_minec_forecast_is_no_longer_glued_to_cbr() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=INTERFAX_URL,
        text=_fixture("interfax_1121468.txt"),
        claim_statement=STATEMENT_MINEC_68,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "minec"
    assert decision.primary_name == "Минэкономразвития России"
    assert decision.parent_uri == "https://www.economy.gov.ru/"
    assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT


# ─── т4: kommersant — два значения, два первоисточника, каждое своему ────────


def test_kommersant_forecast_row_goes_to_minec() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=KOMMERSANT_URL,
        text=_fixture("kommersant_8924678.txt"),
        claim_statement=STATEMENT_MINEC_22,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "minec"
    assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT


def test_kommersant_measured_row_still_goes_to_rosstat() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=KOMMERSANT_URL,
        text=_fixture("kommersant_8924678.txt"),
        claim_statement=STATEMENT_ROSSTAT_34,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "rosstat"


# ─── т4 (страновой уровень): страница kommersant с новым словарём — два первоисточника.
# Было (v5): производная Росстата по шаблону «сообщил Росстат»; стало: `ambiguous_primaries`
# — на той же странице назван ещё и Минэкономразвития со своим прогнозом. Честный отказ
# безопаснее ложной склейки (AGENTS §7 T7.98).───────────────────────────────────


def test_kommersant_page_is_now_ambiguous() -> None:
    decision = detect_source_attribution(
        canonical_uri=KOMMERSANT_URL, text=_fixture("kommersant_8924678.txt")
    )
    assert decision.status == STATUS_AMBIGUOUS, decision.basis_fragment


def test_interfax_page_stays_ambiguous() -> None:
    """Страница-агрегатор interfax была `ambiguous_primaries` и до T7.109 — остаётся."""
    decision = detect_source_attribution(
        canonical_uri=INTERFAX_URL, text=_fixture("interfax_1121468.txt")
    )
    assert decision.status == STATUS_AMBIGUOUS, decision.basis_fragment


# ─── т5: равнозначность чисел (детектор значения и текстовый гейт) ───────────


def test_trailing_zeros_do_not_change_a_value() -> None:
    assert claim_value_numbers("Значение составило 7,0%.") == ("7",)
    assert claim_value_numbers("Значение составило 7%.") == ("7",)
    assert claim_value_numbers("Значение составило 2,20%.") == ("2.2",)
    assert claim_value_numbers("Значение составило 2,2%.") == ("2.2",)


def test_integer_part_and_other_numbers_stay_distinguishable() -> None:
    assert claim_value_numbers("Значение составило 6,35%.") == ("6.35",)
    assert claim_value_numbers("Значение составило 6,3%.") == ("6.3",)
    # целая часть сравнивается по-прежнему (стендовая страница-заглушка T7.108):
    assert claim_value_numbers("Значение составило 52.22%.") == ("52.22",)
    assert claim_value_numbers("Значение составило 12,2%.") == ("12.2",)
    assert claim_value_numbers("Значение составило 122,2%.") == ("122.2",)


def test_relay_pairs_a_whole_number_with_a_trailing_zero_claim() -> None:
    """Стык, на котором молчало реле: страница пишет «на уровне 7%», утверждение — «7,0%».

    Было (v7): `value_not_attributed` (число фрагмента «7» не совпадало со значением «7.0»);
    стало: пересказ Банка России — то же сравнение чисел, что у гейта T7.108a."""
    text = "ЦБ подтвердил прогноз инфляции на 2026 год на уровне 7%."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/9999999",
        text=text,
        claim_statement="Прогноз Банка России по инфляции на 2026 год составляет 7,0%.",
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "cbr"


def test_a_different_decimal_is_still_a_different_value() -> None:
    """Равнозначность хвостовых нулей не превращает 6,3 в 6,35."""
    text = "По данным Росстата, инфляция в сентябре 2026 года составила 6,35%."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/9999998",
        text=text,
        claim_statement="Годовая инфляция в России в сентябре 2026 года составила 6,3%.",
    )
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision.basis_fragment


def test_gate_sees_trailing_zero_forms_both_ways() -> None:
    """Текстовый гейт T7.108a остаётся текстовым: «7,0» в тексте подтверждает «7%» и наоборот."""
    assert claim_values_found_in_texts(
        values=frozenset(claim_value_numbers("Значение составило 7%.")),
        texts=("По оценке, значение составило 7,0%.",),
    ) == frozenset({"7"})
    assert claim_values_found_in_texts(
        values=frozenset(claim_value_numbers("Значение составило 2,20%.")),
        texts=("Таблица первоисточника: 2,2%.",),
    ) == frozenset({"2.2"})
    # граничные числа страницы («52.22 Кб», «12,2 Мб») по-прежнему не подтверждают 2,2%:
    assert claim_values_found_in_texts(
        values=frozenset(claim_value_numbers("Значение составило 2,2%.")),
        texts=("Файлы раздела (52.22 Кб) и таблица (12,2 Мб). Архив 2019–2026.",),
    ) == frozenset()


# ─── т5: оба конца диапазона в утверждении (единица — после правого конца) ───


@pytest.mark.parametrize(
    ("statement", "expected"),
    [
        ("Прогноз составляет 6,0–7,0%.", ("6", "7")),
        ("Прогноз составляет 6-7%.", ("6", "7")),
        ("Прогноз составляет 6—7%.", ("6", "7")),
        ("Прогноз составляет 6 – 7%.", ("6", "7")),
        ("Прогноз составляет от 6 до 7%.", ("6", "7")),
        ("Прогноз составляет 7,0–7,2%.", ("7", "7.2")),
        # единица у обоих концов — те же два конца (правый и так измерен):
        ("Прогноз составляет 6,0% – 7,0%.", ("6", "7")),
        # годы — не значения: единицы нет ни у одного конца
        ("Показатель за 2025–2026 годы снизился.", ()),
        # «и» — не соединитель диапазона: правый конец остаётся единственным значением
        ("Между 6,0 и 7,0%.", ("7",)),
        # год перед диапазоном не склеивается с левым концом диапазона
        ("Прогноз на 2026 год: инфляция 6-7%.", ("6", "7")),
    ],
)
def test_range_ends_become_claim_values(statement: str, expected: tuple[str, ...]) -> None:
    assert claim_value_numbers(statement) == expected


# ─── т6: ближайший первоисточник (часть б2) ──────────────────────────────────

# дословная фраза стенды (interfax 1121468): два прогноза в одном предложении
FRAGMENT_BOTH_FORECASTS = (
    "Годовая инфляция на 5 октября превысила верхнюю границу прогноза ЦБ на 2026 год (6-7%). "
    "Чтобы инфляция уложилась в прогноз ЦБ (6-7%) и прогноз Минэкономразвития (6,8%), ставка "
    "должна быть жёстче."
)


def test_each_forecast_belongs_to_the_primary_in_front_of_it() -> None:
    """6–7% стоит сразу после «прогноз ЦБ», 6,8% — сразу после «прогноз Минэкономразвития»:
    каждое значение тому алиасу, который ближе всего перед ним."""
    cbr = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121468",
        text=FRAGMENT_BOTH_FORECASTS,
        claim_statement=STATEMENT_RANGE_SHORT,
    )
    assert cbr.status == STATUS_DERIVATIVE, cbr.basis_fragment
    assert cbr.primary_key == "cbr"
    minec = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121468",
        text=FRAGMENT_BOTH_FORECASTS,
        claim_statement=STATEMENT_MINEC_68,
    )
    assert minec.status == STATUS_DERIVATIVE, minec.basis_fragment
    assert minec.primary_key == "minec"


def test_two_primaries_without_proximity_are_ambiguous() -> None:
    """«ЦБ и Минэкономразвития ожидают … на уровне 6,8%»: оба алиаса стоят перед значением
    одинаково близко — однозначного ближайшего нет, решение — прежний честный отказ."""
    text = (
        "20 февраля. ЦБ и Минэкономразвития ожидают инфляцию в 2027 году на уровне 6,8%. "
        "Об этом говорится в обзоре рынка."
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.garant.ru/topics/1111111",
        text=text,
        claim_statement="Прогноз инфляции в России на 2027 год составляет 6,8%.",
    )
    assert decision.status == STATUS_AMBIGUOUS, decision.basis_fragment
    assert decision.primary_key is None
    assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT


def test_own_other_number_closer_to_a_primary_blocks_the_attribution() -> None:
    """Росстат назвался своим числом (6,8%) раньше чужих прогнозов (6–7%): значение 6,8% —
    Росстата, а Минэкономразвития и ЦБ остаются со своим числом (7), к значению 6,8% не
    относящимся: ближайший алиас — тот, чьё ближайшее число и есть значение утверждения."""
    text = "Росстат зафиксировал инфляцию 6,8% на фоне прогнозов Минэкономразвития и Банка России 6-7%."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121467",
        text=text,
        claim_statement="Годовая инфляция в России в сентябре 2026 года составила 6,8%.",
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "rosstat"


def test_both_forecasting_primaries_close_to_the_same_value_stay_ambiguous() -> None:
    """Зеркало предыдущего замера: то же предложение, значение 6–7% — у Росстата своё другое
    число ближе (6,8%), а оба прогнозиста стоят перед 6-7% одинаково близко → прежний отказ."""
    text = "Росстат зафиксировал инфляцию 6,8% на фоне прогнозов Минэкономразвития и Банка России 6-7%."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121467",
        text=text,
        claim_statement=STATEMENT_RANGE_SHORT,
    )
    assert decision.status == STATUS_AMBIGUOUS, decision.basis_fragment


def test_third_actor_marker_silences_proximity_as_it_silences_relay() -> None:
    """Тот же гвард (б) relay действует и в близости: фрагмент, называющий третье действующее
    лицо, не решает ничего (прежние режимы здесь тоже молчат: шаблона нет)."""
    text = "Прогноз ЦБ (6-7%) и прогноз Минэкономразвития (6,8%) — вдвое ниже оценок аналитиков."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121466",
        text=text,
        claim_statement=STATEMENT_MINEC_68,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision.basis_fragment


def test_actor_wording_with_a_template_stays_on_the_old_disagreement_rule() -> None:
    """«по оценке аналитиков» — шаблон атрибуции прежнего строгого режима: два алиаса в одном
    фрагменте дают прежнее `ambiguous_primaries`. Было (v7): ложная склейка значения
    Минэкономразвития на ЦБ; стало: честный отказ. Правило разночтения не ослаблено."""
    text = "Прогноз ЦБ (6-7%) и прогноз Минэкономразвития (6,8%), по оценке аналитиков, занижен."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121466",
        text=text,
        claim_statement=STATEMENT_MINEC_68,
    )
    assert decision.status == STATUS_AMBIGUOUS, decision.basis_fragment


def test_proximity_never_makes_a_page_a_retelling_of_itself() -> None:
    """Страница самого ЦБ с чужим прогнозом: кандидат ЦБ — домашний хост (вето home-host),
    кандидат Минэкономразвития остаётся: 6,8% — приписано Минэкономразвития даже на cbr.ru.

    Значение 6–7% на странице ЦБ режим близости не приписывает никому: первое решение —
    прежнее (`self_primary` строгого режима: relay одиночного алиаса + home-host вето,
    T7.104), и к нему T7.109 не прикасался."""
    minec_on_cbr = attribute_value_in_fragment(
        canonical_uri="https://www.cbr.ru/press_center/1111111",
        text=FRAGMENT_BOTH_FORECASTS,
        claim_statement=STATEMENT_MINEC_68,
    )
    assert minec_on_cbr.status == STATUS_DERIVATIVE, minec_on_cbr.basis_fragment
    assert minec_on_cbr.primary_key == "minec"
    own = attribute_value_in_fragment(
        canonical_uri="https://www.cbr.ru/press_center/1111111",
        text=FRAGMENT_BOTH_FORECASTS,
        claim_statement=STATEMENT_RANGE_SHORT,
    )
    assert own.status == STATUS_SELF_PRIMARY, own.basis_fragment


def test_single_alias_fragments_are_untouched_by_proximity() -> None:
    """Режим близости включается только во фрагментах с ДВУМЯ словарными алиасами; одиночный
    алиас решает прежний relay ровно как решал (interfax строка 64 — про Минэкономразвития)."""
    text = "Минэкономразвития повысило прогноз инфляции на 2026 год до 6,8% с 5,2% в майском прогнозе."
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.interfax.ru/business/1121465",
        text=text,
        claim_statement=STATEMENT_MINEC_68,
    )
    assert decision.status == STATUS_DERIVATIVE, decision.basis_fragment
    assert decision.primary_key == "minec"


# ─── словарь (часть б1): Минэкономразвития — отдельный первоисточник ─────────


def test_minec_is_a_registered_primary_source() -> None:
    spec = primary_source("minec")
    assert spec is not None
    assert spec.name == "Минэкономразвития России"
    assert spec.home_uri == "https://www.economy.gov.ru/"
    assert spec.home_hosts == ("economy.gov.ru",)
    assert is_home_host("https://www.economy.gov.ru/press/news/", spec.home_hosts)
    assert is_home_host("https://economy.gov.ru/", spec.home_hosts)
    # тот же `.gov.ru` — не тот же первоисточник (ловушка registrable_domain, AGENTS §7 T7.75)
    assert not is_home_host("https://minfin.gov.ru/", spec.home_hosts)
    assert not is_home_host("https://rosstat.gov.ru/", spec.home_hosts)


def test_minec_aliases_and_minfin_stays_a_different_primary() -> None:
    assert "minec" in named_primary_keys("по данным Минэкономразвития России")
    assert "minec" in named_primary_keys("Министерство экономического развития РФ")
    assert "minec" in named_primary_keys("Ministry of Economic Development")
    assert "minec" in named_primary_keys("на economy.gov.ru")
    # голое «минэк» алиасом не стало (обрывок ловит чужие слова):
    assert "minec" not in named_primary_keys("по данным минэка")
    # Минфин ≠ Минэкономразвития: прежние решения по Минфину от нового словаря не меняются
    assert named_primary_keys("сообщил Минфин России") == ("minfin",)
