"""Unit: T7.130 (части 3а, 3б, 4в, 4г, 4д, 4е, ADR-0029 §27) — опрос P и вето по числу.

Стенд `363be67-v29` дал два новых ложных «Проверено» (E3) из пяти ответов config-v29:

ДЕФЕКТ 2 (этот файл): утверждение 4adb10fe «Консенсус-прогноз аналитиков по годовой инфляции в
России на 2026 год составляет 7,1% (по данным макроэкономического опроса Банка России за октябрь
2026 года)» на странице sbercib.ru осталось честным отказом (`value_not_attributed`), хотя шаблон
«по данным … P» формально собирает пару. Причина — исключение (г) «прогноз с другим автором»:
`RELAY_FORECAST_NOUN` поймал «прогноз», а `_third_actor_present` нашёл «аналитиков». Аналитики в
такой формулировке — респонденты ОПРОСА Банка России, а не другой автор: консенсус, собранный
ведомством по его же опросу, есть публикация ведомства.

ДЕФЕКТ 1 (его сценарная часть — `tests/scenario/test_reattribute_claim_scoped_lock_t7130.py`):
замок ADR-0031 был наложен на весь источник; здесь закреплена только детекторная часть — что
страница finmarket.ru называет Росстат у значения 6,0%.

Что закреплено:
- (в) стендовая страница sbercib + стендовая формулировка → `derivative` → Банк России, pairing
  `statement_names_producer`; на `363be67` этот же вызов — отказ (красность доказана worktree);
- закрытый список шаблонов «P — издатель опроса»: имена только из `PRIMARY_SOURCES`, форма —
  закрытая, три шаблона; без имени P в шаблоне правило не применяется;
- (д) прогноз, автором которого является сам банк («По прогнозу SberCIB … 6,8%»), Банку России не
  приписывается;
- (г) вето собственной оценки смотрит на ЧИСЛО маркера: маркер у ДРУГОГО числа вето не даёт,
  маркер у значения утверждения («по нашей оценке … 7,1%») ветоет по-прежнему; правка включена
  только в пятом режиме — вето строгой ветки, окна публикации и библиографии не ослаблено
  (его держат тесты `test_own_assessment_fragment_is_not_a_retelling` и
  `test_contrast_with_the_primary_is_not_a_retelling`);
- (е) решения по фикстуре T7.129 не изменились (таблица ниже снята прогоном на `363be67`);
- версия метода — `host-value-attribution-v18`, страничный детектор не тронут.

Тесты не обращаются к LLM, сети и стенду.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import apps.research_proxy.value_attribution as va
from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    PRIMARY_SOURCES,
    STATUS_DERIVATIVE,
    STATUS_OWN_ASSESSMENT,
)
from apps.research_proxy.value_attribution import (
    PAIRING_STATEMENT_NAMES_PRODUCER,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
    claim_match_values,
    claim_value_numbers,
)
from packages.memory.scope import VALUE_ATTRIBUTION_SCHEMA

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t7130"

URL_SBERCIB = (
    "https://sbercib.ru/publication/inflyatsiya-v-rossii-v-2026-godu-dinamika-i-prognozi"
)
URL_FINMARKET = "https://www.finmarket.ru/news/6717673"

#: формулировки стендовых утверждений (дословно из снимка менеджера)
STATEMENT_SURVEY = (
    "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 7,1% "
    "(по данным макроэкономического опроса Банка России за октябрь 2026 года)."
)
STATEMENT_WAGES_JAN_JUL = (
    "Рост реальных заработных плат в России за январь-июль 2026 года составил 6,0% "
    "в годовом выражении (по данным Росстата)."
)
#: тот же консенсус-прогноз БЕЗ опроса: исключение (г) остаётся в силе
STATEMENT_SURVEY_DROPPED = (
    "Консенсус-прогноз аналитиков по годовой инфляции в России на 2026 год составляет 7,1% "
    "(по данным Банка России)."
)
#: прогноз самого дома: страница — домашний хост SberCIB (исключение (д) §26.3)
STATEMENT_SBERCIB_OWN = "По прогнозу SberCIB, годовая инфляция в России в 2026 году составит 6,8%."


def _page(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


def _statement_rule(*, statement: str, page: str, canonical_uri: str) -> Any:
    """Прямой вызов пятого режима (тот же хелпер, что в T7.129a): чтобы проверки вето не
    зависели от того, чем закончили бы остальные режимы."""
    return va._statement_names_producer_decision(
        scanned_page=va.normalize_scan_text(page)[: va.MAX_SCAN_CHARS],
        canonical_uri=canonical_uri,
        values=claim_value_numbers(statement),
        match_values=claim_match_values(statement),
        bare_values=va._claim_bare_values(statement),
        claim_statement=statement,
    )


# ─── (в) стендовый ДЕФЕКТ 2: опрос Банка России на странице банка ─────────────────────────


def test_stand_sbercib_survey_page_attributes_the_bank_of_russia() -> None:
    """`363be67`: `value_not_attributed` (исключение (г)); стало: производная → Банк России.

    Причина правки — в формулировке назван ОПРОС Банка России, значит «аналитики» в ней —
    респонденты опроса P, а не другой автор прогноза (ADR-0029 §27)."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SBERCIB,
        text=_page("page-sbercib-ru-publication-inflyatsiya-v-rossii-v-2026-godu-dinamika-i-pro.normalized.txt"),
        claim_statement=STATEMENT_SURVEY,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "cbr", decision
    assert decision.primary_name == "Банк России", decision
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert "опрос" in decision.basis_fragment, decision.basis_fragment


def test_survey_without_named_publisher_is_still_refused() -> None:
    """Тот же консенсус-прогноз без слова «опрос»: правило не применяется, (г) работает."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SBERCIB,
        text=_page("page-sbercib-ru-publication-inflyatsiya-v-rossii-v-2026-godu-dinamika-i-pro.normalized.txt"),
        claim_statement=STATEMENT_SURVEY_DROPPED,
    )
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert decision.primary_key != "cbr", decision


# ─── закрытый список шаблонов «P — издатель опроса» ───────────────────────────────────────


def test_survey_publisher_templates_are_closed_and_name_only_from_dictionary() -> None:
    templates = va.SURVEY_PUBLISHER_TEMPLATES
    assert len(templates) == 3, templates
    assert all("{name}" in template for template in templates)
    # имена подставляет только словарь `PRIMARY_SOURCES`: своего алфавита имён у правила нет
    dictionary_keys = {spec.key for spec in PRIMARY_SOURCES}
    for key in dictionary_keys:
        patterns = va._survey_publisher_templates(key)
        assert len(patterns) == len(templates)
    named = va._statement_names_producer_as_survey_publisher(
        "по данным макроэкономического опроса Банка России за октябрь 2026 года", "cbr"
    )
    assert named
    # опрос назван, но не назван его издатель — правило не применяется
    assert not va._statement_names_producer_as_survey_publisher(
        "по данным октябрьского опроса, консенсус-прогноз составил 7,1%", "cbr"
    )
    # издатель другого опроса не открывает правило для Банка России
    assert not va._statement_names_producer_as_survey_publisher(
        "по данным опроса ВЦИОМ, инфляция ускорилась", "cbr"
    )


# ─── (д) прогноз, автором которого является сам дом, чужим производителем не становится ────


def test_host_own_forecast_is_not_attributed_to_the_survey_publisher() -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=URL_SBERCIB,
        text=_page("page-sbercib-ru-publication-inflyatsiya-v-rossii-v-2026-godu-dinamika-i-pro.normalized.txt"),
        claim_statement=STATEMENT_SBERCIB_OWN,
    )
    assert decision.primary_key != "cbr", decision
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision


# ─── (г) вето собственной оценки — по числу маркера ───────────────────────────────────────


_HEAD = "Инфляция в России: динамика и прогнозы на 2026 год.\n"
#: маркер собственной оценки стоит у СВОЕГО числа (6,8%), а значение утверждения (7,1%) — в
#: соседнем предложении: страница не отказывает
_PAGE_MARKER_AT_OTHER_NUMBER = (
    _HEAD
    + "Консенсус-прогноз по годовой инфляции в России повышен до 7,1% с 6,6% на конец 2026 года.\n"
    + "Ранее по нашей оценке рост цен должен был замедлиться до 6,8%.\n"
)
#: маркер собственной оценки стоит у значения утверждения — вето по-прежнему
_PAGE_MARKER_AT_CLAIM_VALUE = (
    _HEAD
    + "Годовая инфляция в России, по нашей оценке, замедлится до 7,1% на конец 2026 года.\n"
)


def test_marker_at_another_number_does_not_veto_the_statement_rule() -> None:
    """Было (v17): вето собственной оценки по окрестности значения снимало и этот случай.

    Стало (v18, часть 3б): «по нашей оценке … 6,8%» — оценка ДРУГОГО числа, она не отвечает на
    вопрос «чьё это 7,1%», и пятый режим работает.
    """
    decision = _statement_rule(
        statement=STATEMENT_SURVEY,
        page=_PAGE_MARKER_AT_OTHER_NUMBER,
        canonical_uri="https://news.example.ru/economy/inflation",
    )
    assert decision is not None, "маркер у чужого числа заблокировал пятый режим"
    assert decision.primary_key == "cbr", decision


def test_marker_at_the_claimed_value_still_vetoes() -> None:
    """«по нашей оценке … 7,1%» — значение утверждения объявлено своей оценкой: вето."""
    decision = _statement_rule(
        statement=STATEMENT_SURVEY,
        page=_PAGE_MARKER_AT_CLAIM_VALUE,
        canonical_uri="https://news.example.ru/economy/inflation",
    )
    assert decision is None, decision


def test_entry_veto_is_not_weakened_by_the_number_rule() -> None:
    """Прежнее вето строгой ветки сохранено байт в байт (флаг `by_own_number` там не стоит)."""
    page = (
        "По данным Росстата, инфляция за 2025 год составила 5,59%.\n"
        "В отличие от Росстата, наша методика даёт 6,3%.\n"
    )
    decision = attribute_value_in_fragment(
        canonical_uri="https://news.example.ru/economy/inflation",
        text=page,
        claim_statement="Годовая инфляция в России по итогам 2025 года составила 5,59%.",
    )
    assert decision.status == STATUS_OWN_ASSESSMENT, decision


# ─── ДЕФЕКТ 1: детекторная часть (замок — в сценарном тесте T7.130) ────────────────────────


def test_finmarket_wage_page_attributes_rosstat() -> None:
    """Страница «…сообщил в среду Росстат. В целом за январь-июль … на 6,0%…» — производная."""
    decision = attribute_value_in_fragment(
        canonical_uri=URL_FINMARKET,
        text=_page("page-finmarket-ru-news-6717673.normalized.txt"),
        claim_statement=STATEMENT_WAGES_JAN_JUL,
    )
    assert decision.status == STATUS_DERIVATIVE, decision
    assert decision.primary_key == "rosstat", decision
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision


# ─── (е) решения по фикстуре T7.129 не изменились ─────────────────────────────────────────

def _t7129_rows() -> list[tuple[str, str, str, str | None]]:
    cases = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "t7129" / "cases.json").read_text()
    )["cases"]
    rows: list[tuple[str, str, str, str | None]] = []
    for case in cases:
        for evidence in case["evidence"]:
            page = (
                Path(__file__).resolve().parents[1] / "fixtures" / "t7129" / evidence["page"]
            ).read_text(encoding="utf-8")
            decision = attribute_value_in_fragment(
                canonical_uri=evidence["uri"],
                text=page,
                claim_statement=case["statement"],
            )
            rows.append(
                (
                    case["claim_id"][:8],
                    evidence["uri"],
                    decision.status,
                    decision.primary_key,
                )
            )
    return rows


def test_t7129_fixture_decisions_are_unchanged() -> None:
    """Было (v17) → стало (v18): ни одного изменения решений по стендовой фикстуре T7.129."""
    expected = {
        # (утверждение, страница) → (статус, ключ) на `363be67`
        ("060d979a", "https://www.cbr.ru/press/reginfl/?id=70291"): ("derivative", "rosstat"),
        (
            "060d979a",
            "https://rosstat.gov.ru/storage/mediabank/157_09-10-2026.html",
        ): ("no_value_attribution", None),
        ("060d979a", "https://base.garant.ru/415048373/"): ("derivative", "rosstat"),
        (
            "738548ca",
            "https://www.sberbank.ru/common/img/uploaded/news/2026/cmi/termometr_trud.html",
        ): ("derivative", "rosstat"),
        ("a7cac406", "https://smart-lab.ru/company/mozgovik/blog/1359553.php"): ("derivative", "hh"),
        ("a7cac406", "https://setka.ru/posts/01a11c97-43db-7b60-a22a-036755095369"): (
            "derivative",
            "hh",
        ),
        (
            "b262da08",
            "https://www.rambler.ru/pro/zarplata/57077146-u-kogo-upali-realnye-zarplaty-v-2026-godu/",
        ): ("derivative", "hh"),
        ("b262da08", "https://brainhire.ru/blog/rekruting/rynok-truda-rossii-2026"): (
            "own_assessment",
            None,
        ),
    }
    got = {(claim, uri): (status, key) for claim, uri, status, key in _t7129_rows()}
    assert got == expected, sorted(set(got.items()) ^ set(expected.items()))


# ─── версия метода ─────────────────────────────────────────────────────────────────────────


def test_method_version_bumped_and_page_detector_untouched() -> None:
    """Было: `host-value-attribution-v17`; стало: v18 (меняются решения, не форма записи)."""
    assert VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v18"
    assert VALUE_ATTRIBUTION_SCHEMA == "host-value-attribution-v1"
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v10"


# ─── снимок стенда: ПРИЧИНА отказа зафиксирована данными ───────────────────────────────────


def _stand_cases() -> dict[str, Any]:
    document = json.loads((FIXTURE_DIR / "cases.json").read_text(encoding="utf-8"))
    return {case["claim_id"][:8]: case for case in document["cases"]}


def test_stand_snapshot_names_the_foreign_dispute_as_the_blocker() -> None:
    """ДЕФЕКТ 1 данными: улика 9c43a83b стоит под коррекцией ДРУГОГО утверждения.

    Именно это замок T7.114 не умел различать: `claim_id_in_event_payload` у коррекции,
    накрывающую строку finmarket, равен 738548ca, а не 9c43a83b.
    """
    cases = _stand_cases()
    jan_jul = cases["9c43a83b"]
    row = next(e for e in jan_jul["evidence"] if e["domain"] == "finmarket.ru")
    assert row["value_attribution_on_stand"] is None, row
    linked = {c["claim_id_in_event_payload"] for c in row["under_operator_correction"]}
    assert linked == {"738548ca"}, linked

    disputed = cases["738548ca"]
    own = next(e for e in disputed["evidence"] if e["domain"] == "finmarket.ru")
    assert {c["claim_id_in_event_payload"] for c in own["under_operator_correction"]} == {"738548ca"}


def test_stand_snapshot_keeps_defect2_under_its_own_dispute() -> None:
    """ДЕФЕКТ 2 данными: улики 4adb10fe на sbercib — оспоренная пара САМОГО этого утверждения.

    Поэтому §27.1 раскрывает их только после отмены спора; замок по утверждению (ADR-0031 §10)
    снимает их для чужих утверждений сразу (44bd1460, 136a0ca0).
    """
    case = _stand_cases()["4adb10fe"]
    rows = [e for e in case["evidence"] if (e["domain"] or "").startswith("sbercib")]
    assert rows, case["evidence"]
    for row in rows:
        assert row["value_attribution_on_stand"] is None, row
        assert {c["claim_id_in_event_payload"] for c in row["under_operator_correction"]} == {
            "4adb10fe"
        }
