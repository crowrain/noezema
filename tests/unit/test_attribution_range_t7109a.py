"""Unit: T7.109a — диапазон страницы является одним значением (замечание приёмки T7.109).

Замечание менеджера (приёмка T7.109, ложная склейка): утверждение «Консенсус-прогноз
экономистов, опрошенных „Интерфаксом“, по инфляции в России на 2026 год составляет 7,0%.»
приписывался Банку России (derivative cbr) — единственное число утверждения совпало с ПРАВЫМ
концом диапазона прогноза ЦБ «(6-7%)» во фрагментах страницы interfax_1121468. Число консенсуса
и конец чужого диапазона — разные значения: ложная склейка дороже ложного пропуска (ADR-0029).

Правило детектора значений (v9): диапазон страницы («6-7%», «6–7%», «от 6 до 7%»; соединители
те же `_RANGE_CONNECTOR`, левый конец без своей единицы, единица после правого) — ОДНО значение
(составной токен `6-7`). Одиночное значение утверждения концу диапазона не приписывается;
диапазон утверждения совпадает только с диапазоном текста с теми же концами (эквиваленция
T7.109 сохранена: «6,0–7,0%» ≡ «6–7%» ≡ «от 6 до 7%»); одиночное число страницы по-прежнему
приписывает одиночное значение утверждения. Правило действует во всех точках сравнения
значений: фильтр «другого числа», relay (через фильтр вызывающего кода), окно публикации,
режим близости, библиография. Страничный детектор значения утверждения с числами страницы
не сравнивает → его версия остаётся v6.

Тот же замер закрывает и обратную сторону диапазона: правый конец «5-7%» — тоже не «7»,
левый конец «6-7%» — тоже не «6».
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
)
from apps.research_proxy.value_attribution import (
    STATUS_VALUE_NOT_ATTRIBUTED,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    VALUE_ATTRIBUTION_PAIRING_STRICT,
    _measured_value_tokens,
    attribute_value_in_fragment,
    claim_match_values,
)

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "t7109"

INTERFAX_URL = "https://www.interfax.ru/business/1121468"
KOMMERSANT_URL = "https://www.kommersant.ru/doc/8924678"
SYNTHETIC_URL = "https://synthetic-news.example/article"

# ─── замеры менеджера (приёмка T7.109) и утверждения синтетики ──────────────────

STATEMENT_CONSENSUS_70 = (
    "Консенсус-прогноз экономистов, опрошенных «Интерфаксом», по инфляции в России "
    "на 2026 год составляет 7,0%."
)
STATEMENT_RANGE_FULL = "Прогноз Банка России по инфляции на 2026 год составляет 6,0–7,0%."
STATEMENT_RANGE_SHORT = "Прогноз Банка России по инфляции на 2026 год составляет 6–7%."
STATEMENT_MINEC_68 = "Прогноз Минэкономразвития России по инфляции на 2026 год составляет 6,8%."
STATEMENT_MINEC_22 = (
    "Минэкономразвития прогнозирует рост реальных зарплат в России в 2026 году на 2,2% "
    "(майский прогноз)."
)
STATEMENT_ROSSTAT_34 = (
    "Реальные зарплаты в России в июне 2026 года увеличились на 3,4% в годовом выражении."
)

# синтетика: relay-формы «подтвердил … на уровне» работают без шаблона атрибуции
PAGE_RANGE = "Банк России подтвердил прогноз инфляции на 2026 год на уровне 6-7%."
PAGE_SINGLE = "Банк России подтвердил прогноз инфляции на 2026 год на уровне 7%."
PAGE_RANGE_WORD = "Банк России подтвердил прогноз инфляции на 2026 год на уровне от 6 до 7%."
PAGE_LEFT_RANGE = "Банк России подтвердил прогноз инфляции на 2026 год на уровне 5-7%."


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _decide(text: str, claim: str, url: str = SYNTHETIC_URL):
    return attribute_value_in_fragment(canonical_uri=url, text=text, claim_statement=claim)


# ─── т5.0: профиль сравнения — диапазон не раскладывается на концы ───────────────


def test_claim_match_profile_keeps_range_whole() -> None:
    assert claim_match_values(STATEMENT_CONSENSUS_70) == frozenset({"7"})
    assert claim_match_values(STATEMENT_MINEC_68) == frozenset({"6.8"})
    # эквиваленция T7.109 сохранена: все три формы диапазона — один и тот же токен
    assert claim_match_values("Прогноз составляет 6,0–7,0%.") == frozenset({"6-7"})
    assert claim_match_values("Прогноз составляет 6–7%.") == frozenset({"6-7"})
    assert claim_match_values("Прогноз составляет от 6 до 7%.") == frozenset({"6-7"})
    # год без единицы значением не становится (тот же измеритель, что и прежде)
    assert claim_match_values("Период 2025–2026 год.") == frozenset()


def test_page_side_tokens_treat_range_as_one_value() -> None:
    # правый конец «6-7%» — не «7», левый конец «5-7%» — не «5»: один составной токен
    assert _measured_value_tokens(PAGE_RANGE) == frozenset({"6-7"})
    assert _measured_value_tokens(PAGE_LEFT_RANGE) == frozenset({"5-7"})
    assert _measured_value_tokens(PAGE_SINGLE) == frozenset({"7"})
    # одиночные значения рядом с диапазоном остаются одиночными
    assert _measured_value_tokens("прогноз (6-7%), факт 6,8%.") == frozenset({"6-7", "6.8"})


# ─── т1: консенсус 7,0% с interfax-страницы — НЕ Банк России (прежнее ложное склеивание) ──


def test_interfax_consensus_single_is_not_attributed_to_cbr() -> None:
    decision = _decide(
        _fixture("interfax_1121468.txt"), STATEMENT_CONSENSUS_70, INTERFAX_URL
    )
    # честный отказ: атрибуции в основании есть (прогнозы ЦБ названы), но значение
    # утверждения среди значений основания отсутствует
    assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, decision.basis_fragment
    assert decision.primary_key is None
    assert decision.claim_values == ("7",)


# ─── т2/т3: диапазонные утверждения склеиваются как раньше (диапазон с диапазоном) ─────


def test_interfax_range_statements_still_attributed_to_cbr() -> None:
    for statement in (STATEMENT_RANGE_FULL, STATEMENT_RANGE_SHORT):
        decision = _decide(_fixture("interfax_1121468.txt"), statement, INTERFAX_URL)
        assert decision.status == STATUS_DERIVATIVE, statement
        assert decision.primary_key == "cbr", statement
        assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT
        # запись остаётся плоским списком концов (гейт T7.108a и позиции relay не тронуты)
        assert decision.claim_values == ("6", "7")


# ─── т4: 6,8% Минэкономразвития — без изменений ────────────────────────────────────


def test_interfax_minec_68_unchanged() -> None:
    decision = _decide(_fixture("interfax_1121468.txt"), STATEMENT_MINEC_68, INTERFAX_URL)
    assert decision.status == STATUS_DERIVATIVE
    assert decision.primary_key == "minec"


# ─── т6: kommersant 2,2 → Минэкономразвития и 3,4 → Росстат — без изменений ─────────


def test_kommersant_decisions_unchanged() -> None:
    kommersant = _fixture("kommersant_8924678.txt")
    decision_22 = _decide(kommersant, STATEMENT_MINEC_22, KOMMERSANT_URL)
    assert decision_22.status == STATUS_DERIVATIVE
    assert decision_22.primary_key == "minec"
    decision_34 = _decide(kommersant, STATEMENT_ROSSTAT_34, KOMMERSANT_URL)
    assert decision_34.status == STATUS_DERIVATIVE
    assert decision_34.primary_key == "rosstat"


# ─── т5 (синтетика): одиночное значение против диапазона, одиночное против одиночного ──


def test_single_claim_value_not_attributed_to_page_range() -> None:
    # концы диапазонов «6-7%» и «5-7%» одиночному значению «7» не приписываются
    for page in (PAGE_RANGE, PAGE_LEFT_RANGE):
        decision = _decide(page, "Инфляция в России по итогам 2026 года составит 7%.")
        assert decision.status == STATUS_VALUE_NOT_ATTRIBUTED, page
        assert decision.primary_key is None


def test_single_claim_value_still_attributed_to_page_single() -> None:
    decision = _decide(PAGE_SINGLE, "Инфляция в России по итогам 2026 года составит 7%.")
    assert decision.status == STATUS_DERIVATIVE
    assert decision.primary_key == "cbr"
    assert decision.pairing == VALUE_ATTRIBUTION_PAIRING_STRICT


def test_claim_range_matches_page_range_in_all_connector_forms() -> None:
    for page in (PAGE_RANGE, PAGE_RANGE_WORD):
        decision = _decide(page, "Инфляция в России по итогам 2026 года составит 6–7%.")
        assert decision.status == STATUS_DERIVATIVE, page
        assert decision.primary_key == "cbr", page


# ─── т5 (режим близости): диапазон не даёт кандидата одиночному значению ──────────────


def test_nearest_mode_single_claim_vs_range_end_no_longer_glues() -> None:
    page = "ЦБ подтвердил прогноз на 2026 год (6-7%), а Минэкономразвития повысило прогноз до 6,8%."
    single = _decide(page, "Инфляция в России по итогам 2026 года может составить 7%.")
    # ни один первоисточник не заявляет 7% своим числом: честный отказ вместо склейки с ЦБ
    assert single.status == STATUS_NO_VALUE_ATTRIBUTION, single.basis_fragment
    assert single.primary_key is None
    # диапазонное утверждение того же фрагмента приписывается ЦБ как раньше (близость конца
    # диапазона алиасу ЦБ сохранена)
    ranged = _decide(page, "Инфляция в России по итогам 2026 года может составить 6–7%.")
    assert ranged.status == STATUS_DERIVATIVE
    assert ranged.primary_key == "cbr"


# ─── версии: значения v9, страницы v6 (страничный детектор значений не сравнивает);
#     с T7.112 — значения v11, страницы v8 (STATUS T7.112) ──────────────────────────────


def test_versions_after_t7109a() -> None:
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v11"
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v8"
