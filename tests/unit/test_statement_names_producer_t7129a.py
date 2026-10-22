"""Unit: T7.129a (часть 3, ADR-0029 §26.3) — пятый режим «утверждение называет производителя».

Метод `host-value-attribution-v18`: производителем значения считается тот, кого назвало САМО
утверждение, если страница это имя у значения не называет. Правила закрытые: имена только из
`PRIMARY_SOURCES`, шаблоны утверждения — закрытый список из четырёх (см.
`STATEMENT_PRODUCER_TEMPLATES`), значение ищется тем же сравнением, что гейт «значение на связи»
(T7.108a/T7.113/T7.120, включая индексную форму T7.128), действуют вето собственной оценки
утверждения и страницы, сверка совместимости с показателем (v16) и самоисключение домашнего хоста
P (или его семьи).

Что закрепляет этот файл:
- (а) стендовые пересказы из фикстуры `tests/fixtures/t7129/` атрибутированы pairing
  `statement_names_producer`, родитель — сам названный производитель (его якорь), а не страница;
  случай garant идёт именно через индексную форму значения (106,35 для «6,35%»);
- (б) residue §26.2: страница brainhire остаётся отказом — вето собственной оценки консультанта
  (§26.2) v17 НЕ снимает;
- (в) grouping: записи нового pairing дают ОДНУ группу производителя вместо второй
  «независимой» группы (вход группировки — `grouping_arguments`, сам алгоритм не тронут);
- (г) запись с новым pairing проходит `build_value_attribution` → `as_scope` →
  `parse_value_attribution` без потерь (это и был критерий СТОП T7.129);
- (д) каждый из четырёх закрытых шаблонов называет производителя; имя вне шаблонов и два
  производителя в утверждении — правило не применяется;
- (е) копия страницы, с которой убрано значение, не атрибутируется (исключение (д) §26.3);
- новый pairing не кормит сборщик ключей (цепочек нет) и не проверяется гейтом пересмотра
  записей, как библиография и упоминание;
- (часть 1) цепочка value-attribution не импортирует `apps.orchestrator`: модуль индексной формы
  перенесён в `apps/research_proxy`, а не импортирован в обратную сторону.

Тесты не обращаются к LLM, сети и стенду.
"""

from __future__ import annotations

import json
import re
import uuid
from pathlib import Path
from typing import Any

import pytest

import apps.research_proxy.value_attribution as va
from apps.orchestrator.attribution_keys import known_primary_key_from_decision
from apps.research_proxy.index_value_form import index_form_confirms_value
from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    STATUS_DERIVATIVE,
    STATUS_OWN_ASSESSMENT,
    primary_source,
)
from apps.research_proxy.value_attribution import (
    PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER,
    PAIRING_STATEMENT_NAMES_PRODUCER,
    PAIRING_VALUE_AND_PRIMARY,
    VALUE_ATTRIBUTION_METHOD_VERSION,
    attribute_value_in_fragment,
    claim_indicator_profile,
    claim_match_values,
    claim_values_found_in_texts,
    record_pairing_gate_passes,
)
from packages.memory.independence import group_source_graph
from packages.memory.scope import (
    EVIDENCE_VALUE_ATTRIBUTION_KEY,
    VALUE_ATTRIBUTION_PAIRING_STATEMENT_NAMES_PRODUCER,
    VALUE_ATTRIBUTION_PAIRINGS,
    build_value_attribution,
    parse_value_attribution,
)
from packages.memory.source_graph import GraphNode, SourceGraphAssembly, grouping_arguments

pytestmark = pytest.mark.unit

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "t7129"
_CASES: dict[str, Any] = json.loads((FIXTURE_DIR / "cases.json").read_text())
_BY_ID: dict[str, Any] = {case["claim_id"][:8]: case for case in _CASES["cases"]}

_NEWS_URI = "https://news.example.ru/economy/inflation"


def _case(case_id: str) -> Any:
    return _BY_ID[case_id]


def _evidence(case_id: str, domain: str) -> Any:
    return next(e for e in _case(case_id)["evidence"] if e["domain"] == domain)


def _page(case_id: str, domain: str) -> str:
    return (FIXTURE_DIR / _evidence(case_id, domain)["page"]).read_text()


def _decide(case_id: str, domain: str) -> Any:
    evidence = _evidence(case_id, domain)
    return attribute_value_in_fragment(
        canonical_uri=evidence["uri"],
        text=_page(case_id, domain),
        claim_statement=_case(case_id)["statement"],
    )


#: участок страницы garant вокруг индексной формы «106,35» — основание пятого режима для этого
#: случая: обычной текстовой формы «6,35%» на странице нет ни в одной строке
_GARANT_PAGE = _page("060d979a", "garant.ru")
_INDEX_AT = _GARANT_PAGE.index("106,35")
_INDEX_BASIS = _GARANT_PAGE[_INDEX_AT - 140 : _INDEX_AT + 140]


# ─── (а) стендовые пересказы: значение приписано тому, кого назвало утверждение ──────────


@pytest.mark.parametrize(
    ("case_id", "domain", "primary_key", "home_uri"),
    [
        # копия таблиц Росстата: собственного имени производителя у значения нет, а само значение
        # на странице существует только индексной формой (106,35 / 100,35 / 105,04)
        ("060d979a", "garant.ru", "rosstat", "https://rosstat.gov.ru/"),
        # дашборд ЦМИ Сбера: «до 3.3% в июле» без имени Росстата у этого числа
        ("738548ca", "sberbank.ru", "rosstat", "https://rosstat.gov.ru/"),
        # заголовок «Хэдхантер. Рынок труда в сентябре: hh.индекс снизился до 8,5» без шаблона
        ("a7cac406", "smart-lab.ru", "hh", "https://hh.ru/"),
        # пост пользователя соцсети `setka.ru` (не домашний хост `hh`, решение T7.129)
        ("a7cac406", "setka.ru", "hh", "https://hh.ru/"),
        # rambler: имя производителя далеко от значения, значение в абзаце про hh-индекс
        ("b262da08", "rambler.ru", "hh", "https://hh.ru/"),
    ],
)
def test_stand_retelling_is_attributed_by_the_statement(
    case_id: str, domain: str, primary_key: str, home_uri: str
) -> None:
    decision = _decide(case_id, domain)
    assert (decision.status, decision.primary_key) == (STATUS_DERIVATIVE, primary_key), decision
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision
    # родитель — сам названный производитель (его якорь), а не прочитанная страница
    assert decision.parent_uri == home_uri, decision
    assert primary_source(primary_key) is not None
    assert decision.basis_fragment, "основание обязано показывать участок страницы со значением"


def test_garant_value_is_carried_by_the_index_form_only() -> None:
    """Гарантия ветви индексной формы: на странице garant нет ни одной обычной формы «6,35%».

    Значение подтверждено только формой «106,35» (T7.128), поэтому к этой ветви сверка
    совместимости показателя (б) неприменима — её охраняет закрытый список маркеров индексной
    формы. Если ветвь сломается, случай garant вернётся к честному отказу.
    """
    statement = _case("060d979a")["statement"]
    page = _page("060d979a", "garant.ru")
    singles = claim_match_values(statement)
    assert not claim_values_found_in_texts(values=singles, texts=(page,)), (
        "обычная текстовая форма значения на странице garant появилась unexpectedly"
    )
    confirmed = index_form_confirms_value(statement=statement, values=singles, texts=(page,))
    assert confirmed == frozenset({"6.35"}), confirmed
    decision = _decide("060d979a", "garant.ru")
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision


# ─── (б) residue §26.2: вето собственной оценки v17 НЕ снимает ──────────────────────────


def test_brainhire_stays_refused_by_the_own_assessment_veto() -> None:
    """brainhire.ru остаётся `own_assessment` (§26.2): документ целиком про собственные оценки
    консультанта, и для «голого» значения вето действует на весь текст. Правка вето — вне задачи
    (ADR-0029 §26.2 «для brainhire нужна отдельная правка вето»)."""
    decision = _decide("b262da08", "brainhire.ru")
    assert decision.status == STATUS_OWN_ASSESSMENT, decision
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert decision.primary_key != "hh", decision


# ─── (д) закрытый список шаблонов: четыре работают, вне списка — нет ────────────────────

_PAGE_WITH_VALUE = (
    "Экономика\n\n"
    "Годовая инфляция в России в сентябре 2026 года ускорилась до 6,35% после 6,18% в августе. "
    "Продовольственная корзина подорожала на 7,4%.\n"
)


@pytest.mark.parametrize(
    "statement",
    [
        # 1: «по данным P»
        "Официальная годовая инфляция в России в сентябре 2026 года составила 6,35% (по данным Росстата).",
        # 2: «P:» в начале утверждения
        "Росстат: официальная годовая инфляция в России в сентябре 2026 года составила 6,35%.",
        # 3: «по оценке P»
        "По оценке Росстата, официальная годовая инфляция в России в сентябре 2026 года составила 6,35%.",
        # 4: «P сообщил / опубликовал / представил / привёл данные»
        "Росстат представил данные: официальная годовая инфляция в России в сентябре 2026 года составила 6,35%.",
    ],
    ids=["по данным P", "P:", "по оценке P", "P представил данные"],
)
def test_each_closed_template_names_the_producer(statement: str) -> None:
    decision = attribute_value_in_fragment(
        canonical_uri=_NEWS_URI, text=_PAGE_WITH_VALUE, claim_statement=statement
    )
    assert (decision.status, decision.primary_key) == (STATUS_DERIVATIVE, "rosstat"), decision
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision


@pytest.mark.parametrize(
    "statement",
    [
        # имя словаря есть, но ни один из четырёх закрытых шаблонов не собран
        "Как отмечают в Росстате, официальная годовая инфляция в России в сентябре 2026 года составила 6,35%.",
        # два производителя в утверждении: единого производителя значения нет (§26.3 исключение (г))
        "По данным Росстата, официальная годовая инфляция в России в сентябре 2026 года оказалась "
        "ниже прогноза Банка России и составила 6,35%.",
    ],
    ids=["имя вне закрытых шаблонов", "два производителя в утверждении"],
)
def test_closed_vocabulary_rejects_these_statements(statement: str) -> None:
    """Правило не применяется: ни одно решение с pairing пятого режима."""
    decision = attribute_value_in_fragment(
        canonical_uri=_NEWS_URI, text=_PAGE_WITH_VALUE, claim_statement=statement
    )
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert _statement_rule(statement=statement, page=_PAGE_WITH_VALUE) is None


def _statement_rule(*, statement: str, page: str) -> Any:
    """Прямой вызов пятого режима (без цепочки): чтобы отрицательные проверки не зависели от
    того, чем закончили бы остальные режимы."""
    values = va.claim_value_numbers(statement)
    match_values = claim_match_values(statement)
    return va._statement_names_producer_decision(
        scanned_page=va.normalize_scan_text(page)[: va.MAX_SCAN_CHARS],
        canonical_uri=_NEWS_URI,
        values=values,
        match_values=match_values,
        bare_values=va._claim_bare_values(statement),
        claim_statement=statement,
    )


# ─── (е) значения нет на странице — исключения (д) §26.3 ───────────────────────────────


def test_copy_without_the_value_is_not_attributed() -> None:
    """С копии smart-lab убраны обе записи hh-индекса — атрибуции быть не может (исключение (д))."""
    statement = _case("a7cac406")["statement"]
    page = _page("a7cac406", "smart-lab.ru")
    stripped = re.sub(r"8[.,]5", "…", page)
    singles = claim_match_values(statement) | va._claim_bare_values(statement)
    assert not claim_values_found_in_texts(values=singles, texts=(stripped,)), stripped[:80]
    assert not index_form_confirms_value(statement=statement, values=singles, texts=(stripped,))
    decision = attribute_value_in_fragment(
        canonical_uri=_evidence("a7cac406", "smart-lab.ru")["uri"],
        text=stripped,
        claim_statement=statement,
    )
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert decision.status != STATUS_DERIVATIVE, decision


# ─── (в) сгруппированная атрибуция: одна группа производителя вместо двух «независимых» ──


@pytest.mark.parametrize(
    ("case_id", "primary_key"), [("060d979a", "rosstat"), ("738548ca", "rosstat"), ("a7cac406", "hh")]
)
def test_recorded_attributions_make_one_producer_group(case_id: str, primary_key: str) -> None:
    """ВХОД группировки с записями нового pairing склеивает пересказы с якорем производителя.

    Сам алгоритм `group_source_graph` не изменён: меняются только родители, которые даёт
    `grouping_arguments` (T7.117: запись о значении важнее страничного указателя). Без записей
    (пустой `effective_parent`) те же страницы остаются разными группами — это и есть стендовый
    дефект §26.1, ради которого проектировалось правило."""
    statement = _case(case_id)["statement"]
    spec = primary_source(primary_key)
    assert spec is not None
    anchor = GraphNode(
        source_id=uuid.uuid4(),
        canonical_uri=spec.home_uri,
        content_hash=None,
        pointer_parent=None,
    )
    pages: list[tuple[GraphNode, Any]] = []
    for evidence in _case(case_id)["evidence"]:
        node = GraphNode(
            source_id=uuid.uuid4(),
            canonical_uri=evidence["uri"],
            content_hash=f"hash-{evidence['domain']}",
            pointer_parent=None,
        )
        decision = attribute_value_in_fragment(
            canonical_uri=evidence["uri"],
            text=(FIXTURE_DIR / evidence["page"]).read_text(),
            claim_statement=statement,
        )
        pages.append((node, decision))

    attributed = [(node, dec) for node, dec in pages if dec.is_derivative and dec.parent_uri == anchor.canonical_uri]
    assert attributed, f"{case_id}: ни одна страница не приписана якорю производителя"
    effective = {node.source_id: anchor.source_id for node, _dec in attributed}

    members = tuple(node.source_id for node, _dec in pages)
    page_nodes = tuple(node for node, _dec in pages)
    assembly = SourceGraphAssembly(
        member_ids=(*members, anchor.source_id),
        nodes=(*page_nodes, anchor),
        edges=(),
        corrections=(),
        effective_parent=effective,
    )
    result = group_source_graph(*grouping_arguments(assembly))
    group_of_anchor = result.groups[anchor.source_id]
    for node, _dec in attributed:
        assert result.groups[node.source_id] == group_of_anchor, (case_id, node.canonical_uri)
        assert str(result.bases[node.source_id]).startswith("parent:")

    # контроль красности смысла: без записей те же пересказы — разные группы
    bare = SourceGraphAssembly(
        member_ids=assembly.member_ids,
        nodes=assembly.nodes,
        edges=(),
        corrections=(),
        effective_parent={},
    )
    bare_result = group_source_graph(*grouping_arguments(bare))
    assert len({bare_result.groups[node.source_id] for node, _dec in attributed}) == len(attributed), (
        f"{case_id}: без записей пересказы обязаны были оставаться разными группами"
    )


# ─── (г) запись проходит сборку, scope и обратное чтение ───────────────────────────────


def test_new_pairing_record_survives_build_scope_and_parse_roundtrip() -> None:
    """Критерий СТОП T7.129: неизвестный pairing молча отбрасывался — теперь запись читается."""
    decision = _decide("060d979a", "garant.ru")
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision
    spec = primary_source(decision.primary_key or "")
    assert spec is not None
    parent_source_id = str(uuid.uuid4())

    record = build_value_attribution(
        primary_key=spec.key,
        primary_name=spec.name,
        primary_uri=spec.home_uri,
        parent_source_id=parent_source_id,
        method=VALUE_ATTRIBUTION_METHOD_VERSION,
        basis_fragment=decision.basis_fragment,
        pairing=decision.pairing,
    )
    assert record is not None, "сборщик отклонил запись с новым pairing"
    scope = {EVIDENCE_VALUE_ATTRIBUTION_KEY: record.as_scope()}
    parsed = parse_value_attribution(scope)
    assert parsed == record
    assert parsed is not None and parsed.pairing == VALUE_ATTRIBUTION_PAIRING_STATEMENT_NAMES_PRODUCER
    assert parsed.method == VALUE_ATTRIBUTION_METHOD_VERSION == "host-value-attribution-v18"
    assert parsed.parent_source_id == parent_source_id
    assert parsed.basis_fragment == record.basis_fragment
    assert VALUE_ATTRIBUTION_PAIRING_STATEMENT_NAMES_PRODUCER in VALUE_ATTRIBUTION_PAIRINGS
    # страничный детектор не тронут: словарь имён и его версия прежние
    assert ATTRIBUTION_METHOD_VERSION == "host-source-attribution-v10"


def test_new_pairing_gives_no_chaining_key() -> None:
    """Записи пятого режима не кормят `known_claim_primaries`: цепочек «утверждение назвало
    производителя» нет (тот же гвард, что у библиографии T7.107 и упоминания T7.116)."""
    decision = _decide("a7cac406", "setka.ru")
    assert decision.pairing == PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert known_primary_key_from_decision(decision) is None
    strict = _decide("060d979a", "cbr.ru")
    assert strict.pairing == PAIRING_VALUE_AND_PRIMARY, strict
    assert known_primary_key_from_decision(strict) == "rosstat"


@pytest.mark.parametrize(
    ("pairing", "basis", "expected"),
    [
        # пятый режим: основание — область страницы вокруг индексной формы, обычной формы числа
        # утверждения в ней нет
        (PAIRING_STATEMENT_NAMES_PRODUCER, _INDEX_BASIS, True),
        # библиография — тот же режим «значение в основание не обещалось»
        (PAIRING_BIBLIOGRAPHY_KNOWN_PRODUCER, _INDEX_BASIS, True),
        # строгий режим проверяется по-прежнему: без числа в неполном основании запись снимается
        (PAIRING_VALUE_AND_PRIMARY, "Индексы потребительских цен в процентах", False),
    ],
)
def test_record_pairing_gate_treats_new_pairing_like_bibliography(
    pairing: str, basis: str, expected: bool
) -> None:
    """Гейт пересмотра записи (§26.3 «для нового pairing не применяется») ведёт себя для пятого
    режима ровно как для библиографии: основание не обязано содержать число утверждения
    (индексная форма «106,35» его туда не вносит). Строгий режим проверяется по-прежнему."""
    statement = _case("060d979a")["statement"]
    assert len(_INDEX_BASIS) < va.BASIS_FRAGMENT_CHARS, "основание не должно быть обрезанным"
    assert not claim_values_found_in_texts(values=claim_match_values(statement), texts=(_INDEX_BASIS,))
    assert (
        record_pairing_gate_passes(claim_statement=statement, pairing=pairing, basis_fragment=basis)
        is expected
    )


# ─── часть 1: направление зависимости ──────────────────────────────────────────────────


def test_value_attribution_chain_does_not_import_orchestrator() -> None:
    """Цепочка value-attribution не знает про `apps.orchestrator`.

    Модуль индексной формы значения (`index_value_form`) перенесён в `apps/research_proxy` (T7.129a,
    ADR-0029 §26.4): обратный импорт развернул бы направление зависимости, а копия гейта запрещена
    правилом об одном месте решения."""
    root = Path(__file__).resolve().parents[2]
    modules = (
        root / "apps/research_proxy/value_attribution.py",
        root / "apps/research_proxy/source_attribution.py",
        root / "apps/research_proxy/index_value_form.py",
    )
    imports = re.compile(r"^\s*(?:from|import)\s+apps\.orchestrator")
    for path in modules:
        text = path.read_text(encoding="utf-8")
        offenders = [line for line in text.splitlines() if imports.match(line)]
        assert not offenders, f"{path.name}: импорт из apps.orchestrator: {offenders}"
    assert (root / "apps/orchestrator/index_value_form.py").exists() is False, (
        "старый модуль обязан быть удалён, иначе решение живёт в двух местах"
    )


def test_indicator_compatibility_gate_still_decides_the_bare_value_case() -> None:
    """Отрицательная граница §26.3 (б): то же число на странице в чужом показателе — отказ.

    Профиль значений «голого» hh-индекса не даёт единиц измерения, поэтому решает перекрытие
    значимых основ показателя: страница говорит о доходах, утверждение — о конкуренции."""
    statement = "По данным hh.ru, в августе 2026 года hh-индекс составил 8,7."
    page = "Рост реальных денежных доходов населения в августе 2026 года составил 8,7% за месяц."
    profile = claim_indicator_profile(statement)
    assert _statement_rule(statement=statement, page=page) is None
    decision = attribute_value_in_fragment(
        canonical_uri="https://blog.example/post", text=page, claim_statement=statement
    )
    assert decision.pairing != PAIRING_STATEMENT_NAMES_PRODUCER, decision
    assert decision.primary_key != "hh", decision
    assert profile is not None
