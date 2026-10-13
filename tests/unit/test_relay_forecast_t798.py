"""T7.98 (ADR-0029 дополнение): relay-кандидат прогноза — закреплённые решения.

Репродюсер стенда .92 (сессия ca2484b2, контроль c3d5cd03). Фикстуры — нормализованные
тексты страниц стенда (`tests/fixtures/t798/`, sha256 файлов — префиксы имён):

    internationalinvestment_f7c64c13  https://internationalinvestment.biz/analytics/8583-…
    sbercib_34ecbe51                  https://sbercib.ru/publication/inflyatsiya-v-rossii-v-2026-godu-…
    t-j_9b74f6f6                      https://t-j.ru/prognoz-2026/
    mo_br_dbb97e6e                    https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/

Тест закрепляет обе стороны правки:

(1) где relay ВИДИТ прогноз первоисточника без шаблона — страница становится честным
    `ambiguous_primaries` (два первоисточника), а улика прогноза — `derivative → Банк России`
    с pairing `value_and_primary_in_fragment`;
(2) где relay обязан молчать — гварды (а)–(е): два алиаса, маркер третьего действующего
    лица, зазор алиас→слово прогноза, число ПЕРЕД словом прогноза, значение утверждения не
    на позиции значения прогноза, отсутствие слова прогноза.

Отрицательные строки закреплены так же строго, как в T7.85: тест краснеет и при ослаблении
(relay заговорил там, где должен молчать), и при пропуске (перестал видеть честный relay).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    RELAY_ALIAS_FORECAST_MAX_CHARS,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    _relay_forecast_keys,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    PAIRING_VALUE_AND_PRIMARY,
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "t798"
T785_DIR = Path(__file__).parent.parent / "fixtures" / "t785"

STAND_URI: dict[str, str] = {
    "internationalinvestment": (
        "https://internationalinvestment.biz/analytics/"
        "8583-alfa-bank-uhudshil-prognoz-po-jekonomike-rossii.html"
    ),
    "sbercib": "https://sbercib.ru/publication/inflyatsiya-v-rossii-v-2026-godu-dinamika-i-prognozi",
    "t-j": "https://t-j.ru/prognoz-2026/",
    "mo_br": "https://www.cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_26-09/",
}


def _text(name: str) -> str:
    files = {
        "internationalinvestment": "internationalinvestment_f7c64c13.normalized.txt",
        "sbercib": "sbercib_34ecbe51.normalized.txt",
        "t-j": "t-j_9b74f6f6.normalized.txt",
        "mo_br": "mo_br_dbb97e6e.normalized.txt",
    }
    return (FIXTURE_DIR / files[name]).read_text(encoding="utf-8")


def _t785_text(name: str) -> str:
    return (T785_DIR / f"{name}.normalized.txt").read_text(encoding="utf-8")


# утверждения стенда (claims_live.tsv, дословно)
STATEMENT_CBR = "Банк России в июле 2026 года повысил прогноз инфляции на 2026 год до 6–7%."
STATEMENT_ALFA = "Аналитики Альфа-Банка прогнозируют инфляцию в России по итогам 2026 года на уровне 7,5%."
STATEMENT_SBERCIB = "Аналитики SberCIB прогнозируют инфляцию в России на конец 2026 года на уровне 6,5%."
STATEMENT_CONSENSUS = (
    "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 6,6% "
    "(по данным макроэкономического опроса Банка России за сентябрь 2026 года)."
)
STATEMENT_559 = "Годовая инфляция в России по итогам 2025 года составила 5,59%."


# ─── (1) страничные решения на текстах стенда (v4) ─────────────────────────


def test_page_decisions_on_t798_stand_texts() -> None:
    expected = {
        # Росстат по шаблону (недельный ИПЦ) + relay-прогноз ЦБ («Банк России также повысил
        # оценку инфляции … 6–7%») → два первоисточника → честный отказ вместо указателя
        # на Росстат (дефект стенда 136a0ca0/07126d06)
        "internationalinvestment": STATUS_AMBIGUOUS,
        # шаблон Росстата + relay «24 июля Банк России повысил прогноз инфляции … до 6–7%»
        "sbercib": STATUS_AMBIGUOUS,
        # собственная оценка Т—Ж — вето own_assessment на всю страницу (прежнее поведение)
        "t-j": STATUS_OWN_ASSESSMENT,
        # страница самого ЦБ: relay молчит (нет пары «алиас + слово прогноза + число после»),
        # честный отказ прежний
        "mo_br": STATUS_NO_VALUE_ATTRIBUTION,
    }
    for name, status in expected.items():
        decision = detect_source_attribution(canonical_uri=STAND_URI[name], text=_text(name))
        assert decision.status == status, (name, decision.status, decision.basis_fragment)
        if status != STATUS_DERIVATIVE:
            assert decision.primary_key is None, name


# ─── (2) поуровневая атрибуция: прогноз ЦБ — пересказ ЦБ, не Росстата ──────


def test_cbr_forecast_evidence_is_derivative_cbr_on_both_stand_pages() -> None:
    for name in ("internationalinvestment", "sbercib"):
        decision = attribute_value_in_fragment(
            canonical_uri=STAND_URI[name], text=_text(name), claim_statement=STATEMENT_CBR
        )
        assert decision.status == STATUS_DERIVATIVE, (name, decision.status)
        assert decision.primary_key == "cbr" and decision.primary_name == "Банк России"
        assert decision.pairing == PAIRING_VALUE_AND_PRIMARY
        assert "Банк России" in decision.basis_fragment


def test_third_actor_own_estimates_stay_negative() -> None:
    # Альфа-Банк (07126d06): собственная оценка на чужой странице — не пересказ Росстата
    # и не пересказ ЦБ (гвард (б): «Альфа-Банк» — маркер третьего действующего лица)
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI["internationalinvestment"],
        text=_text("internationalinvestment"),
        claim_statement=STATEMENT_ALFA,
    )
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision.status
    assert decision.primary_key is None

    # SberCIB (44bd1460): собственная оценка на своей странице — прежнее поведение
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI["sbercib"],
        text=_text("sbercib"),
        claim_statement=STATEMENT_SBERCIB,
    )
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision.status
    assert decision.primary_key is None

    # консенсус-прогноз 6,6% на странице ЦБ (2f456e85): home-host + relay молчит — прежний отказ
    decision = attribute_value_in_fragment(
        canonical_uri=STAND_URI["mo_br"], text=_text("mo_br"), claim_statement=STATEMENT_CONSENSUS
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision.status
    assert decision.primary_key is None


def test_interfax_b_teaser_relay_silent_and_559_stays_rosstat() -> None:
    """Заголовок-тизер interfax_b («…ниже официальных прогнозов») relay не склеивает:
    слово прогноза там не про алиас (гвард (в)); улика 5,59% остаётся честным пересказом
    Росстата по шаблону, а страница — ambiguous (два первоисточника: Росстат + relay ЦБ)."""
    text = _t785_text("interfax_1068021_b")
    uri = "https://www.interfax.ru/business/1068021"
    page = detect_source_attribution(canonical_uri=uri, text=text)
    assert page.status == STATUS_AMBIGUOUS, page.status
    decision = attribute_value_in_fragment(
        canonical_uri=uri, text=text, claim_statement=STATEMENT_559
    )
    assert decision.status == STATUS_DERIVATIVE and decision.primary_key == "rosstat"
    assert decision.pairing == PAIRING_VALUE_AND_PRIMARY


# ─── (3) гварды relay на точных синтетических фрагментах ───────────────────

DANGEROUS_FRAGMENT = (
    "Инфляция в России по итогам 2025 года составила 5,59%, оказавшись ниже прогноза "
    "Банка России на 2026 год (6–7%)."
)


def test_guard_a_two_dictionary_keys_in_one_fragment_relay_silent() -> None:
    fragment = "По прогнозу Банка России и Росстата, инфляция в 2026 году ускорится до 6–7%."
    assert _relay_forecast_keys(fragment) == {}
    decision = attribute_value_in_fragment(
        canonical_uri="https://some-news.example/article", text=fragment, claim_statement=STATEMENT_CBR
    )
    assert decision.status != STATUS_DERIVATIVE, decision.status


def test_guard_b_third_actor_marker_vetoes_relay() -> None:
    fragment = (
        "Аналитики Сбербанка повысили прогноз инфляции на 2026 год до 6,5%, "
        "хотя Банк России ждал 4,5–5,5%."
    )
    assert _relay_forecast_keys(fragment) == {}
    decision = attribute_value_in_fragment(
        canonical_uri="https://some-news.example/article", text=fragment, claim_statement=STATEMENT_SBERCIB
    )
    assert decision.status != STATUS_DERIVATIVE, decision.status


def test_guard_c_forecast_word_must_be_named_by_its_own_subject() -> None:
    # зазор алиас → слово прогноза больше RELAY_ALIAS_FORECAST_MAX_CHARS: «прогнозов»
    # относится к фону сравнения, а не к алиасу — relay молчит
    far = "По оценке Банка России инфляция 2025 года оказалась ниже официальных прогнозов"
    assert len(far) > RELAY_ALIAS_FORECAST_MAX_CHARS
    assert _relay_forecast_keys(far) == {}
    # плотная связка «Банк России повысил прогноз» — relay видит кандидата
    near = "Банк России повысил прогноз инфляции на 2026 год до 6–7%."
    assert _relay_forecast_keys(near) != {}


def test_guard_d_number_before_forecast_word_is_never_the_forecast_value() -> None:
    # relay-кандидат на уровне страницы есть (ЦБ назван субъектом прогноза)…
    assert _relay_forecast_keys(DANGEROUS_FRAGMENT) != {}
    # …но реализованное число 5,59 стоит ДО слова прогноза и ЦБ не принадлежит:
    # честный отказ, а не ложный пересказ ЦБ
    decision = attribute_value_in_fragment(
        canonical_uri="https://some-news.example/article",
        text=DANGEROUS_FRAGMENT,
        claim_statement=STATEMENT_559,
    )
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision.status
    assert decision.primary_key is None
    # а значение прогноза 6–7% тем же фрагментом атрибутируется ЦБ честно
    decision = attribute_value_in_fragment(
        canonical_uri="https://some-news.example/article",
        text=DANGEROUS_FRAGMENT,
        claim_statement=STATEMENT_CBR,
    )
    assert decision.status == STATUS_DERIVATIVE and decision.primary_key == "cbr"


def test_guard_e_required_numbers_relay_pairs_only_the_claim_value() -> None:
    assert _relay_forecast_keys(DANGEROUS_FRAGMENT) != {}
    assert _relay_forecast_keys(DANGEROUS_FRAGMENT, required_numbers=frozenset({"5.59"})) == {}
    assert _relay_forecast_keys(DANGEROUS_FRAGMENT, required_numbers=frozenset({"7"})) != {}


def test_relay_is_forecast_specific_no_forecast_word_no_candidate() -> None:
    # «уточнил … составит» — ни шаблона, ни слова прогноза: relay молчит (сознательная
    # граница метода: relay собирает прогнозы, а не любые высказывания первоисточника)
    fragment = "Банк России уточнил, что инфляция в 2026 году составит 6–7%."
    assert _relay_forecast_keys(fragment) == {}
    decision = attribute_value_in_fragment(
        canonical_uri="https://some-news.example/article", text=fragment, claim_statement=STATEMENT_CBR
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision.status


# ─── версии методов (окно пересмотра прежних записей) ──────────────────────


def test_method_versions_bumped_for_reattribution_window() -> None:
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v7"
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v10"
