"""Unit (T7.104, ADR-0035 §19 дополнение): единый сборщик записи для пути новой улики.

T7.101 закрыл запись `research-reattribute --evidence-level`, но оркестратор при fetch писал
только `value_attribution` (пересказ), а `self_primary` оставался незаписанным (STATUS T7.101:
«новые страницы получат запись только при перечитывании окна»). T7.104 закрывает ограничение:
путь новой улики пользуется тем же помощником — та же `self_publication_candidate` (те же гейты:
статус `self_primary`, ровно один производитель, домашний хост прочитанной страницы, вето
третьего актора ADR-0035 §15) и тот же `build_self_publication` с тем же `method`.

Тесты закрепляют идентичность двух путей: для того же фрагмента и той же формулировки запись
помощника побайтово равна тому, что пишет переатрибуция (кандидат → `build_self_publication`
с адресом прочитанной страницы и методом текущей эпохи — именно так собран plan/apply в
`apps/research_proxy/reattribution.py`). Стендовые тексты — те же фикстуры T7.101
(`tests/fixtures/t7101/`, sha256 = `normalized_sha256` строк источников базы .92).

Гварды обязаны НЕ писать запись: ловушка T7.87 (цифра Росстата на странице ЦБ, CPD_2025-12),
вето третьего актора (аналитики на странице ЦБ, Infl_exp_25-12), производный пересказ на
чужом сайте.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.evidence import reselect_assertion_fragment
from apps.research_proxy.value_attribution import (
    VALUE_ATTRIBUTION_METHOD_VERSION,
    self_publication_candidate,
    self_publication_scope_for_fragment,
)
from packages.memory.scope import (
    EVIDENCE_SELF_PUBLICATION_KEY,
    SELF_PUBLICATION_SCHEMA,
    build_self_publication,
    parse_self_publication,
)

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t7101"

URL_CPD_2026_7 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2026-7/"
URL_INFL_25_12 = "https://cbr.ru/analytics/dkp/inflationary_expectations/Infl_exp_25-12/"
URL_CPD_2025_12 = "https://cbr.ru/analytics/dkp/dinamic/CPD_2025-12/"
URL_DERIVATIVE = "https://interfax.example/news/prognoz-cbr-2026"

STATEMENT_607 = "Прогноз Банка России по годовой инфляции в России на 2026 год составляет 6,0–7,0%."
STATEMENT_63 = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным "
    "декабрьского макроэкономического опроса Банка России составил 6,3%."
)
STATEMENT_559 = (
    "Годовая инфляция в России по итогам 2025 года (декабрь 2025 к декабрю 2024) составила "
    "5,59% (Банк России и Росстат, опубликовано 16–21 января 2026 г.)"
)

#: та же связка «вопрос + план + финальная rationale», что в сценарном тесте T7.104:
#: план — литерал `PLAN_TEMPLATE` оркестратора, rationale — дословная цитата исследователя,
#: по которой T7.82 пересобирает окно улики (сигнал снят ПОСЛЕ complete-раунда)
QUESTION_607 = "Каков прогноз Банка России по годовой инфляции в России на 2026 год?"
PLAN = "Исследовать вопрос, собрать evidence инструментами, предложить claims."
RATIONALE_607 = (
    'Прочитал бюллетень ЦБ: "Банк России будет принимать дальнейшие решения по ключевой '
    'ставке в зависимости от динамики инфляции и инфляционных ожиданий". Прогноз на 2026 год '
    "в тексте есть."
)

#: пересказ прогноза ЦБ на чужом сайте (тот же текст, что в сценарном тесте)
DERIVATIVE_TEXT = (
    "По прогнозу Банка России, годовая инфляция в России в 2026 году составит 6,0–7,0%. "
    "Об этом говорится в опубликованных материалах ведомства."
)


def _fixture(sha_prefix: str) -> str:
    files = [p for p in FIXTURE_DIR.glob(f"{sha_prefix}*.txt") if p.name.startswith(sha_prefix)]
    assert len(files) == 1, (sha_prefix, files)
    data = files[0].read_bytes()
    assert hashlib.sha256(data).hexdigest() == files[0].name[: -len(".txt")], sha_prefix
    return data.decode("utf-8")


def _reattribution_style_record(canonical_uri: str, text: str, statement: str) -> dict[str, Any]:
    """Как собирает запись apply переатрибуции: кандидат по тексту страницы →
    `build_self_publication` с адресом прочитанной страницы и методом текущей эпохи."""
    candidate = self_publication_candidate(
        canonical_uri=canonical_uri, text=text, claim_statement=statement
    )
    assert candidate is not None and not candidate.actor_veto
    record = build_self_publication(
        primary_key=candidate.primary_key,
        primary_name=candidate.primary_name,
        primary_uri=canonical_uri,
        method=VALUE_ATTRIBUTION_METHOD_VERSION,
        basis_fragment=candidate.basis_fragment,
    )
    assert record is not None
    return record.as_scope()


# ─── 1. запись собрана и идентична записи переатрибуции ─────────────────────


def test_helper_record_equals_reattribution_record_on_forecast_page() -> None:
    text = _fixture("65af8c97e7f9")
    scope = self_publication_scope_for_fragment(
        canonical_uri=URL_CPD_2026_7, text=text, claim_statement=STATEMENT_607
    )
    assert scope is not None and set(scope) == {EVIDENCE_SELF_PUBLICATION_KEY}, scope
    record = scope[EVIDENCE_SELF_PUBLICATION_KEY]
    # побайтово та же запись, что записал бы apply `research-reattribute --evidence-level`
    assert record == _reattribution_style_record(URL_CPD_2026_7, text, STATEMENT_607), record
    assert record["schema"] == SELF_PUBLICATION_SCHEMA, record
    assert record["primary_key"] == "cbr" and record["primary_name"] == "Банк России", record
    # адрес записи — адрес прочитанной страницы (витрина проверит его о домашнем хосте)
    assert record["primary_uri"] == URL_CPD_2026_7, record
    # метод текущей эпохи — тот же гейт, по которому переатрибуция отвечает «уже записано»
    assert record["method"] == VALUE_ATTRIBUTION_METHOD_VERSION, record
    assert "По прогнозу Банка России" in record["basis_fragment"], record
    # родителя нет по построению: первоисточник не может быть родителем самого себя
    assert "parent_source_id" not in record and "pairing" not in record, record
    # запись читается тем же разборщиком, что и запись переатрибуции
    assert parse_self_publication(scope) is not None


def test_window_fragment_and_full_page_give_the_same_record() -> None:
    """Идентичность двух путей зафиксирована на том же фрагменте, что видит сессия.

    Оркестратор решает по окну улики (T7.82 пересобирает его по финальной rationale),
    переатрибуция — по полному тексту страницы из артефакта. Для одной формулировки запись
    обязана выйти одинаковой: тот же кандидат, тот же сборщик, тот же basis."""
    text = _fixture("65af8c97e7f9")
    fragment = reselect_assertion_fragment(text, QUESTION_607, PLAN, RATIONALE_607)
    assert fragment is not None, "окно не пересобрано по цитате исследователя"
    assert "6,0–7,0%" in fragment, fragment
    by_window = self_publication_scope_for_fragment(
        canonical_uri=URL_CPD_2026_7, text=fragment, claim_statement=STATEMENT_607
    )
    by_page = self_publication_scope_for_fragment(
        canonical_uri=URL_CPD_2026_7, text=text, claim_statement=STATEMENT_607
    )
    assert by_window is not None and by_window == by_page, (by_window, by_page)


# ─── 2. гварды: запись не пишется там, где её не было бы и у переатрибуции ──


def test_third_actor_veto_writes_nothing() -> None:
    """ADR-0035 §15: «Аналитики прогнозируют…» на странице ЦБ — self_primary с вето:
    решение есть, запись не пишется (хост здесь только издатель)."""
    text = _fixture("7e2eb44100d5")
    candidate = self_publication_candidate(
        canonical_uri=URL_INFL_25_12, text=text, claim_statement=STATEMENT_63
    )
    assert candidate is not None and candidate.actor_veto, candidate
    assert (
        self_publication_scope_for_fragment(
            canonical_uri=URL_INFL_25_12, text=text, claim_statement=STATEMENT_63
        )
        is None
    )


def test_t787_trap_cbr_page_with_rosstat_figure_writes_nothing() -> None:
    """Ловушка T7.87: страница ЦБ с цифрой Росстата (CPD_2025-12, 5,59%) — не self_primary
    вовсе; запись о «собственной публикации» не появится ни при каких условиях."""
    text = _fixture("218e9a1ea998")
    assert (
        self_publication_candidate(
            canonical_uri=URL_CPD_2025_12, text=text, claim_statement=STATEMENT_559
        )
        is None
    )
    assert (
        self_publication_scope_for_fragment(
            canonical_uri=URL_CPD_2025_12, text=text, claim_statement=STATEMENT_559
        )
        is None
    )


def test_derivative_page_writes_no_self_publication() -> None:
    """Пересказ на чужом сайте — не собственная публикация: этот путь закрывает
    `value_attribution` (T7.78), помощник к нему не причастен."""
    assert (
        self_publication_scope_for_fragment(
            canonical_uri=URL_DERIVATIVE, text=DERIVATIVE_TEXT, claim_statement=STATEMENT_607
        )
        is None
    )


def test_helper_requires_page_address_and_text() -> None:
    """Fail-closed: без адреса прочитанной страницы домашний хост не проверить — записи нет."""
    text = _fixture("65af8c97e7f9")
    assert (
        self_publication_scope_for_fragment(
            canonical_uri=None, text=text, claim_statement=STATEMENT_607
        )
        is None
    )
    assert (
        self_publication_scope_for_fragment(
            canonical_uri=URL_CPD_2026_7, text="", claim_statement=STATEMENT_607
        )
        is None
    )


# ─── 3. один построитель на оба пути и невидимость ключа для знания ──────────


REPO_ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def test_both_paths_call_the_single_builder_and_gates() -> None:
    """Второй копии гейтов нет: оркестратор зовёт общий помощник, переатрибуция — того же
    кандидата и того же сборщика записи (`ADR-0035 §19.2` в обоих путях)."""

    orchestrator_src = _source("apps/orchestrator/orchestrator.py")
    reattribution_src = _source("apps/research_proxy/reattribution.py")
    helper_src = _source("apps/research_proxy/value_attribution.py")

    # путь новой улики: ровно один детектор фрагмента и ровно один вызов помощника
    assert orchestrator_src.count("attribute_value_in_fragment(") == 1, "вторая детекция в оркестраторе"
    assert orchestrator_src.count("self_publication_scope_for_fragment(") == 1, "оркестратор не зовёт помощника"
    # путь переатрибуции: ровно один кандидат и ровно один сборщик записи
    assert reattribution_src.count("self_publication_candidate(") == 1, "вторая копия кандидата"
    assert reattribution_src.count("build_self_publication(") == 1, "вторая копия сборщика записи"
    # сам помощник ничего не решает заново: он только собирает запись из кандидата
    assert helper_src.count("def self_publication_scope_for_fragment(") == 1
    body = helper_src.split("def self_publication_scope_for_fragment(", 1)[1].split("\n\n\n", 1)[0]
    assert "self_publication_candidate(" in body and "build_self_publication(" in body, body
    assert "attribute_value_in_fragment(" not in body, "помощник не имеет права решать заново"


def test_new_key_is_invisible_to_grouping_rules_and_gates() -> None:
    """Группировка, движок правил, каскад, переоценка, гейт дублей и витрина оценок новый ключ
    не читают (ADR-0035 §19.4). Читатели записи — только хранение (`scope.py`), перенос
    (`service.py`), сборка (`value_attribution.py`), запись (`reattribution.py`) и метка
    витрины (`producer_view.py`)."""

    forbidden = (
        "packages/memory/independence.py",
        "packages/memory/source_graph.py",
        "packages/memory/rules_engine.py",
        "packages/memory/cascade.py",
        "packages/memory/reassessment.py",
        "packages/memory/reverify.py",
        "apps/orchestrator/value_duplicate.py",
        "apps/web/estimates.py",
        "apps/web/labels.py",
        "apps/web/reliability.py",
        "packages/evaluation/gates.py",
    )
    markers = ("value_self_publication", "EVIDENCE_SELF_PUBLICATION_KEY", "parse_self_publication")
    for relative in forbidden:
        source = _source(relative)
        for marker in markers:
            assert marker not in source, f"{relative} читает новый ключ: {marker}"
