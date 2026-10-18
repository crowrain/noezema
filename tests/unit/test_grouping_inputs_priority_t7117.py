"""Unit (pure): приоритет записи значения над страничным указателем во ВХОДЕ
группировщика (T7.117, решение пользователя 2026-10-15 «вариант 1», ADR-0029).

Закреплено поведение чистой сборки входа `grouping_arguments`
(`packages/memory/source_graph.py`):
- у источника с единственным эффективным родителем по записям значения во вход
  идёт он, даже когда у строки источника есть страничный указатель на ДРУГОЙ
  источник (случай T7.117: lenta → Минфин по указателю страницы, запись значения
  → Банк России);
- записей нет (или родители записей разные — эффективного нет) — работает
  страничный указатель, прежнее поведение T7.75/T7.78;
- указатель и запись называют один и тот же источник — поведение не меняется;
- коррекция оператора merge поверх нового приоритета склеивает группы как прежде
  (основание `correction:merge`), рёбра графа — как прежде;
- родитель по записи остаётся узлом графа и склеивает своих детей (тот же
  `parent:<short12>`), который бы ни указывал на него страничный указатель.

Сам алгоритм группировки (`packages/memory/independence.py`), пороги и PSL-список
не изменены: тесты сравнивают группы, полученные чистым алгоритмом на Сборке-до
и Сборке-после одного и того же графа.
"""

from __future__ import annotations

import uuid

import pytest

from packages.memory.independence import SourceGraphCorrection, group_source_graph
from packages.memory.source_graph import GraphNode, SourceGraphAssembly, grouping_arguments

pytestmark = pytest.mark.unit


def _node(uri: str, *, pointer: uuid.UUID | None = None) -> GraphNode:
    return GraphNode(
        source_id=uuid.uuid4(),
        canonical_uri=uri,
        content_hash=f"hash-{uri}",
        pointer_parent=pointer,
    )


def _assembly(
    nodes: tuple[GraphNode, ...],
    effective: dict[uuid.UUID, uuid.UUID] | None = None,
    corrections: tuple[SourceGraphCorrection, ...] = (),
) -> SourceGraphAssembly:
    return SourceGraphAssembly(
        member_ids=tuple(n.source_id for n in nodes),
        nodes=nodes,
        edges=(),
        corrections=corrections,
        effective_parent=effective or {},
    )


def test_value_record_beats_the_page_pointer() -> None:
    """Т1-форма: указатель ведёт к Минфину, запись значения — к Банку России."""
    cbr = _node("https://cbr.ru/press/2026-key")
    lenta = _node("https://lenta.ru/news/2026-10-08/sprognozirovano", pointer=uuid.uuid4())
    minfin_anchor = _node("https://minfin.gov.ru/")
    assembly = _assembly(
        (lenta, cbr, minfin_anchor),
        effective={lenta.source_id: cbr.source_id},
    )
    inputs, _, _ = grouping_arguments(assembly)
    chosen = {inp.source_id: inp.parent_source_id for inp in inputs}
    assert chosen[lenta.source_id] == cbr.source_id, "запись значения должна вытеснить указатель"

    result = group_source_graph(*grouping_arguments(assembly))
    assert result.groups[lenta.source_id] == result.groups[cbr.source_id]
    assert result.bases[lenta.source_id].startswith("parent:")
    # страничный узел Минфина остаётся отдельной группой: сам указатель никуда не делся
    assert result.groups[minfin_anchor.source_id] != result.groups[cbr.source_id]


def test_conflicting_value_parents_fall_back_to_the_page_pointer() -> None:
    """Т2: у источника две улики с РАЗНЫМИ родителями записей → эффективного нет
    (консерватизм T7.78) → работает страничный указатель — прежнее поведение."""
    pointer_parent = _node("https://rosstat.gov.ru/")
    source = _node("https://sbercib.ru/analitics/inflation", pointer=pointer_parent.source_id)
    other_recorded = _node("https://cbr.ru/press/key-rate")
    # загрузчик при разных родителях записей НЕ кладёт источник в effective_parent
    assembly = _assembly((source, pointer_parent, other_recorded), effective={})
    inputs, _, _ = grouping_arguments(assembly)
    chosen = {inp.source_id: inp.parent_source_id for inp in inputs}
    assert chosen[source.source_id] == pointer_parent.source_id

    result = group_source_graph(*grouping_arguments(assembly))
    assert result.groups[source.source_id] == result.groups[pointer_parent.source_id]
    assert result.groups[source.source_id] != result.groups[other_recorded.source_id]


def test_no_value_record_keeps_the_page_pointer() -> None:
    """Т3: записи значения нет вовсе → страничный указатель (прежнее поведение)."""
    parent = _node("https://rosstat.gov.ru/")
    source = _node("https://vedomosti.ru/economics/inflation", pointer=parent.source_id)
    assembly = _assembly((source, parent), effective={})
    inputs, _, _ = grouping_arguments(assembly)
    assert {inp.source_id: inp.parent_source_id for inp in inputs}[source.source_id] == parent.source_id
    result = group_source_graph(*grouping_arguments(assembly))
    assert result.groups[source.source_id] == result.groups[parent.source_id]


def test_pointer_and_record_naming_the_same_claim_is_unchanged() -> None:
    """Т6: указатель и запись значения называют ОДНА И ТОТ ЖЕ источник — вход не меняется."""
    anchor = _node("https://cbr.ru/press/forecast")
    source = _node("https://interfax.ru/business/1121468", pointer=anchor.source_id)
    assembly_same = _assembly((source, anchor), effective={source.source_id: anchor.source_id})
    assembly_pointer_only = _assembly((source, anchor), effective={})
    inputs_same, _, _ = grouping_arguments(assembly_same)
    inputs_pointer, _, _ = grouping_arguments(assembly_pointer_only)
    assert inputs_same == inputs_pointer
    result = group_source_graph(*grouping_arguments(assembly_same))
    assert result.groups[source.source_id] == result.groups[anchor.source_id]


def test_operator_merge_correction_wins_over_two_value_parents() -> None:
    """Т4: коррекция оператора merge поверх нового приоритета — как прежде:
    две разные группы по записям значения сворачиваются в одну,
    основание участника — `correction:merge`."""
    a_recorded = _node("https://cbr.ru/press/a")
    b_recorded = _node("https://minfin.gov.ru/press/b")
    first = _node("https://interfax.ru/a", pointer=None)
    second = _node("https://lenta.ru/b", pointer=None)
    plain = _node("https://tass.ru/c")
    effective = {first.source_id: a_recorded.source_id, second.source_id: b_recorded.source_id}
    assembly_without = _assembly((first, second, plain, a_recorded, b_recorded), effective=effective)
    assembly_merged = _assembly(
        (first, second, plain, a_recorded, b_recorded),
        effective=effective,
        corrections=(
            SourceGraphCorrection(
                correction_id=uuid.uuid4(),
                from_id=first.source_id,
                to_id=second.source_id,
                kind="merge",
                valid=True,
            ),
            SourceGraphCorrection(
                correction_id=uuid.uuid4(),
                from_id=plain.source_id,
                to_id=first.source_id,
                kind="merge",
                valid=True,
            ),
        ),
    )
    without = group_source_graph(*grouping_arguments(assembly_without))
    assert len({without.groups[s] for s in (first.source_id, second.source_id, plain.source_id)}) == 3
    merged = group_source_graph(*grouping_arguments(assembly_merged))
    assert len({merged.groups[s] for s in (first.source_id, second.source_id, plain.source_id)}) == 1
    # основания записаны прежним порядком приоритетов (parent > correction:merge) — как прежде:
    # у каждого «записного» участника своё `parent:<short12>`, у участника без родителя — `correction:merge`
    assert merged.bases[plain.source_id] == "correction:merge"
    assert merged.bases[first.source_id] == f"parent:{str(a_recorded.source_id)[:12]}"
    assert merged.bases[second.source_id] == f"parent:{str(b_recorded.source_id)[:12]}"


def test_recorded_parent_is_a_graph_node_even_without_any_pointer() -> None:
    """Родитель по записи значения — узел графа: два «детских» источника с одной
    записью склеиваются через него, даже если ни у кого нет страничного указателя."""
    anchor = _node("https://cbr.ru/press/forecast-2026")
    first = _node("https://interfax.ru/a")
    second = _node("https://lenta.ru/b")
    assembly = _assembly(
        (first, second, anchor),
        effective={first.source_id: anchor.source_id, second.source_id: anchor.source_id},
    )
    result = group_source_graph(*grouping_arguments(assembly))
    assert result.groups[first.source_id] == result.groups[second.source_id]
    assert result.bases[first.source_id].startswith("parent:")
    assert result.bases[second.source_id].startswith("parent:")
