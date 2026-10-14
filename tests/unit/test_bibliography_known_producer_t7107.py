"""Unit: вето плашки площадки и третий режим pairing `bibliography_known_producer` (T7.107,
ADR-0029 дополнение).

Два дефекта стенда (утверждение 7cfbe942 «безработица 2,2%», smart-lab.ru/blog/1349924.php):

1. шаблонная плашка площадки («Данная публикация является личным мнением автора. Мнение владельца
   сайта может не совпадать с мнением автора.») вызывала `own_assessment` на ПУСТОМ месте:
   блог пересказывал официальную цифру, собственной оценки в тексте не было;
2. строка-список «Источники: Росстат, …» не читалась ни одним прежним режимом, хотя производитель
   утверждения был уже записан другой уликой того же утверждения (строгий режим на interfax.ru).

Закреплено: закрытый список плашек (`SITE_DISCLAIMER_PHRASES`) замазывается только в копии вето;
словарь `OWN_ASSESSMENT_MARKERS` НЕ сокращён, настоящая оценка остаётся vetoed; новый режим
собирается лишь при всех условиях одновременно (fail-closed), ложная склейка дороже пропуска.
"""

from __future__ import annotations

import pytest

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    OWN_ASSESSMENT_MARKERS,
    SITE_DISCLAIMER_PHRASES,
    STATUS_AMBIGUOUS,
    STATUS_DERIVATIVE,
    STATUS_NO_VALUE_ATTRIBUTION,
    STATUS_OWN_ASSESSMENT,
    detect_source_attribution,
)
from apps.research_proxy.value_attribution import (
    BIBLIOGRAPHY_LINE_MARKERS,
    PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
)
from packages.memory.scope import (
    EVIDENCE_VALUE_ATTRIBUTION_KEY,
    VALUE_ATTRIBUTION_PAIRING_BIBLIOGRAPHY,
    VALUE_ATTRIBUTION_PAIRINGS,
    build_value_attribution,
    parse_value_attribution,
)

pytestmark = pytest.mark.unit

# ─── фикстуры: синтетические страницы той же синтаксической формы, что стенд ────────

PLAQUE = (
    "Данная публикация является личным мнением автора. "
    "Мнение владельца сайта может не совпадать с мнением автора."
)

# блог-пересказ: значение есть, строгой пары нет, плашка площадки есть, список источников есть;
# строки набраны абзацами (тот же приём, что стендовые фикстуры), расстояние до алиаса — за окно
SMARTLAB_PAGE = "\n".join(
    [
        "Блог инвестора. Безработица в России.",
        "Раньше писал про уровень выше, сейчас он опустился до 2,2%.",
        "Настроения на рынке труда медленно улучшаются третий квартал подряд.",
        "Зарплаты растут быстрее цен, но не везде одинаково.",
        "Число резюме выросло на 13%, а вакансий — на 22%; резюме — 8,7 млн.",
        PLAQUE,
        "Источники: Росстат и данные портала HeadHunter.",
    ]
)

INTERFAX_PAGE = (
    "Уровень безработицы в России опустился до 2,2%, следует из данных Росстата. "
    "Это минимальное значение за последние годы."
)
INTERFAX_PAGE_URL = "https://interfax.example/business/1119461"

STATEMENT_22 = "Уровень безработицы в России опустился до 2,2%."
STATEMENT_24 = "Уровень безработицы в России опустился до 2,4%."
URL_SMARTLAB = "https://smart-lab.example/blog/1349924.php"

FRAGMENT_NO_PAIR = "Раньше писал про уровень выше, сейчас он опустился до 2,2%."


# ─── 1: плашка площадки больше не даёт own_assessment (уровень страницы и значения) ──


def test_page_detector_no_longer_vetoes_the_site_plaque() -> None:
    """Было: `own_assessment` на пустом месте. Стало: страница честно «не решена»."""
    decision = detect_source_attribution(canonical_uri=URL_SMARTLAB, text=SMARTLAB_PAGE)
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision
    assert decision.status != STATUS_OWN_ASSESSMENT


def test_page_detector_pair_survives_the_plaque() -> None:
    """Строгий страничный pairing и плашка рядом: плашка не глушит решение о производности."""
    decision = detect_source_attribution(
        canonical_uri=INTERFAX_PAGE_URL,
        text=f"{INTERFAX_PAGE}\n{PLAQUE}",
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision


def test_slot_variants_of_the_plaque_are_masked_too() -> None:
    """Закрытый список покрывает слотовые варианты наблюдаемой плашки («частным», зеркальную
    форму «мнение автора … с мнением редакции»), а не только побайтовую находку."""
    variants = [
        "Данная публикация является частным мнением автора. "
        "Мнение владельца сайта может не совпадать с мнением автора.",
        "Эта публикация является личным мнением автора. "
        "Мнение редакции может не совпадать с мнением автора.",
        "Мнение автора может не совпадать с мнением редакции.",
    ]
    for variant in variants:
        page = f"{FRAGMENT_NO_PAIR}\n{variant}"
        value = attribute_value_in_fragment(
            canonical_uri=URL_SMARTLAB, text=page, claim_statement=STATEMENT_22
        )
        assert value.status != STATUS_OWN_ASSESSMENT, variant
        page_decision = detect_source_attribution(canonical_uri=URL_SMARTLAB, text=page)
        assert page_decision.status != STATUS_OWN_ASSESSMENT, variant


# ─── 2: вето собственной оценки живёт по-прежнему (маска его не отменяет) ──────────


def test_genuine_assessment_with_plaque_stays_own_assessment() -> None:
    """«По нашим расчётам 2,2%» + плашка площадки: настоящая оценка замазана НЕ бывает."""
    page = (
        "По нашим расчётам безработица опустилась до 2,2%, что расходится с публикациями "
        "ведомств.\n" + PLAQUE + "\nИсточники: Росстат и данные портала HeadHunter."
    )
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=page,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=page,
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision
    page_decision = detect_source_attribution(canonical_uri=URL_SMARTLAB, text=page)
    assert page_decision.status == STATUS_OWN_ASSESSMENT, page_decision


def test_genuine_assessment_without_plaque_is_untouched() -> None:
    """Прежнее поведение сохранено без плашки: вето действует ровно как до T7.107."""
    page = "По нашей оценке безработица опустилась до 2,2%."
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB, text=page, claim_statement=STATEMENT_22
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


def test_unlisted_plaque_variant_stays_conservative() -> None:
    """Вне закрытого списка маска НЕ срабатывает: консервативность сохраняется, склейки нет."""
    page = f"{FRAGMENT_NO_PAIR}\nМнение автора может не совпадать с позицией редакции."
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=page,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=page,
    )
    # «не совпадать» — маркер словаря; вне закрытого списка он остаётся в копии вето
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


# ─── 3: третий режим pairing — список источников + известный производитель ──────────


def test_bibliography_line_with_known_producer_is_derivative() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=FRAGMENT_NO_PAIR,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=SMARTLAB_PAGE,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision
    assert decision.primary_name == "Росстат", decision
    assert decision.parent_uri == "https://rosstat.gov.ru/", decision
    assert decision.pairing == PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER, decision
    assert "Источники: Росстат" in decision.basis_fragment, decision.basis_fragment
    assert decision.claim_values == ("2.2",), decision


def test_no_known_producer_means_the_old_refusal_unchanged() -> None:
    """Пустое `known_claim_primaries` — поведение ровно прежнее: плашка снята, но glue нет."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB, text=SMARTLAB_PAGE, claim_statement=STATEMENT_22
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


def test_two_known_producers_named_in_the_lists_is_ambiguous() -> None:
    page = f"{FRAGMENT_NO_PAIR}\nИсточники: Росстат и Банк России."
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=FRAGMENT_NO_PAIR,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat", "cbr"}),
        page_text=page,
    )
    assert decision.status == STATUS_AMBIGUOUS, decision
    assert decision.pairing == PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER, decision


def test_claim_value_absent_from_the_page_means_honest_refusal() -> None:
    """Условие (ii): число утверждения обязано стоять на странице измеренным."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=FRAGMENT_NO_PAIR,
        claim_statement=STATEMENT_24,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=SMARTLAB_PAGE,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


def test_known_producer_named_only_outside_the_bibliography_lines_is_refused() -> None:
    """Условие (i): алиас должен стоять В СТРОКЕ-СПИСКЕ; упоминание в меню сайта не считается."""
    page = "Меню: Минфин России, новости ведомства.\n" + SMARTLAB_PAGE
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=FRAGMENT_NO_PAIR,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"minfin"}),
        page_text=page,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


def test_bibliography_line_without_marker_never_counts() -> None:
    """«Источники сообщили» без двоеточия — не строка-список закрытого маркера."""
    page = "Источники сообщили о снижении безработицы.\n" + FRAGMENT_NO_PAIR
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SMARTLAB,
        text=FRAGMENT_NO_PAIR,
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=page,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


def test_home_page_of_the_producer_is_not_its_own_retelling() -> None:
    """Условие (v): страница самого первоисточника не приписывается ему новым режимом."""
    page = "\n".join(
        [
            "Оперативные данные о безработице.",
            "Уровень безработицы опустился до 2,2%.",
            "Источники: Росстат и данные портала HeadHunter.",
        ]
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://www.rosstat.gov.ru/operativnye-dannye",
        text="Оперативные данные о безработице.",
        claim_statement=STATEMENT_22,
        known_claim_primaries=frozenset({"rosstat"}),
        page_text=page,
    )
    assert decision.status == STATUS_NO_VALUE_ATTRIBUTION, decision


# ─── 4: замок на месте — словари и множества закрыты, словарь вето не сокращён ──────


def test_own_assessment_marker_dict_is_not_shrunk() -> None:
    """T7.107 обязан был снять вето ТОЛЬКО маскированием плашки: словарь маркеров нетронут
    (22 записи — ровно как на bb1123d; проверка краснеет при любом удалении)."""
    assert len(OWN_ASSESSMENT_MARKERS) == 22, OWN_ASSESSMENT_MARKERS
    for marker in ("не совпада[а-яё]*", "по нашим расч[а-яё]*", "расхождени[а-яё]*"):
        assert marker in OWN_ASSESSMENT_MARKERS, marker


def test_disclaimer_and_bibliography_marker_lists_are_closed_pinned() -> None:
    """Списки закрытые и пинятся побайтово: дополнение без решения пользователя не разрастается.
    Дополнение T7.113 (часть в) — решение пользователя 2026-10-14: наблюдаемая плашка finmarket
    «Мнение посетителей сайта, оставляющих свои комментарии…, может не совпадать с мнением
    редакции…» и её зеркало; вставка между частями ограничена (не более 80 знаков без точки и
    перевода строки). Словарь маркеров собственной оценки при этом не сокращён."""
    assert SITE_DISCLAIMER_PHRASES == (
        r"(?:данная\s+)?публикация\s+является\s+(?:личным|частным)\s+мнением\s+автора",
        r"мнение\s+(?:владельца\s+сайта|редакции|администрации)\s+может\s+не\s+совпадать\s+с\s+"
        r"мнением\s+автора",
        r"мнение\s+автора\s+может\s+не\s+совпадать\s+с\s+мнением\s+(?:владельца\s+сайта|редакции|"
        r"администрации)",
        r"мнение\s+(?:посетителей сайта|читателей|пользователей|комментаторов)[^.\n]{0,80}?"
        r"может\s+не\s+совпадать\s+с\s+мнением\s+(?:редакции|автора|владельца сайта|администрации)",
        r"мнение\s+(?:редакции|автора|владельца сайта|администрации)[^.\n]{0,80}?может\s+не\s+"
        r"совпадать\s+с\s+мнением\s+(?:посетителей сайта|читателей|пользователей|комментаторов)",
    )
    assert BIBLIOGRAPHY_LINE_MARKERS == (
        "источник:",
        "источники:",
        "источники данных:",
        "по материалам:",
        "source:",
        "sources:",
    )


def test_method_versions_bumped_for_reattribution_review() -> None:
    """Метки метода подняты — переатрибуция пересмотрит прежние решения по предикату
    «метка ≠ текущая»; маркер формы записи (`host-value-attribution-v1`) не менялся."""
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v8"
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v12"


def test_bibliography_pairing_is_a_known_scope_pairing() -> None:
    """Новое pairing-значение входит в закрытое множество скоупа и читается кругом
    build→parse; legacy-записи без pairing по-прежнему читаются строгими (не тронуто)."""
    assert VALUE_ATTRIBUTION_PAIRING_BIBLIOGRAPHY in VALUE_ATTRIBUTION_PAIRINGS
    record = build_value_attribution(
        primary_key="rosstat",
        primary_name="Росстат",
        primary_uri="https://rosstat.gov.ru/",
        parent_source_id="33912509-6fa7-44a4-ba30-c7224cc8c36e",
        method=VALUE_ATTRIBUTION_METHOD_VERSION,
        basis_fragment="Источники: Росстат и данные портала HeadHunter.",
        pairing=VALUE_ATTRIBUTION_PAIRING_BIBLIOGRAPHY,
    )
    assert record is not None
    parsed = parse_value_attribution({EVIDENCE_VALUE_ATTRIBUTION_KEY: record.as_scope()})
    assert parsed == record
    assert parsed is not None and parsed.pairing == VALUE_ATTRIBUTION_PAIRING_BIBLIOGRAPHY
