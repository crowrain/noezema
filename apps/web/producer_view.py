"""Случай «кластер вокруг производителя» (ADR-0035 вариант A, T7.87).

Чистая презентационная функция: ни базы, ни rules engine, ни модели. Ей передают ровно то,
что витрина уже прочитала — улики действующей оценки утверждения и записанные факты графа
этой же оценки (`source_independence_members.group_id`, `assessment_evidence.role`,
`evidence.scope.value_attribution`, якорь-родитель строки источника). Функция только называет
уже вычисленное состояние (ADR-0026): она не оценивает знание, не меняет уровень, grade,
пороги и состав групп.

Что она различает (формулировка ADR-0035 §2): подтверждения оценки образовали одну группу
независимости потому, что все прочитанные источники восходят к одному производителю значения
(`original:первоисточник`, `derivative → тот же производитель`), а не потому, что знания о
факте правда мало. «Подтверждено слабо» такое состояние описывает неверно: оно звучит как
«мало свидетельств», тогда как свидетельство есть — публикация производителя и её пересказы.

Правило fail-closed во всём: любое условие, которое нельзя вывести из переданных строк,
означает «случай не признан» (возвращается None), а не «придумаем пояснение поправее».
Только что добавленные колонки и индексы — источник фактов; новых значений в закрытые
перечисления, миграций и порогов правило не требует.

Условия признания случая:

(а) действующая оценка объяснена нехваткой независимости — в её записанных причинах есть
    `insufficient_independence` (единственный код правил, означающий «групп меньше нужного»:
    `packages/memory/rules_engine.py:237`; семейство `independence_<relation>_not_met` — про
    связь между источниками, а не про число групп, :242);
(б) среди подтверждающих улик этой оценки ровно одна группа независимости;
(в) в ней есть хотя бы один указатель на производителя — запись происхождения значения улики
    (`value_attribution.primary_key` из словаря первоисточников) либо якорь-родитель строки
    источника, чей хост является домашним хостом производителя из того же словаря, — и все
    такие указатели называют одного и того же производителя;
(г) контр-улик у этой оценки нет.

Сам прочитанный адрес вида `cbr.ru` производителем не называет: страница ЦБ может сообщать цифру
Росстата (стендовый случай 9266248e), поэтому хост строки источника в указатели не входит —
указателем считается только записанное решение о происхождении значения или якорь-родитель.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Final

from apps.research_proxy.source_attribution import PRIMARY_SOURCES, is_home_host, primary_source
from packages.domain.models.enums import AssessmentEvidenceRole
from packages.memory.scope import parse_value_attribution

#: Единственная причина rules engine, которая означает «независимых групп меньше нужного».
#: Семейство `independence_<relation>_not_met` говорит о связи между источниками (повторяемость
#: одним методом), а не о числе групп: подставлять под него подпись про производителя нельзя.
INDEPENDENCE_SHORTFALL_REASONS: Final[frozenset[str]] = frozenset({"insufficient_independence"})

_SUPPORT_ROLE: Final = AssessmentEvidenceRole.SUPPORT.value
_COUNTER_ROLE: Final = AssessmentEvidenceRole.COUNTER.value

#: Маркер «указатель есть, но назвать производителя по нему нельзя». Такое поле означает, что
#: строка противоречит условию (в), а не то, что указателя нет.
_UNNAMED_POINTER: Final = object()


def _attribution_pointer(row: Mapping[str, Any]) -> Any:
    """Указатель из записанного решения о происхождении значения улики (T7.85).

    Имя наружу берётся ИЗ СЛОВАРЯ по `primary_key`, а не из записанной строки: запись не может
    подставить в человеческую подпись свой текст. Пустая строка — указателя нет; битая или
    незнакомая запись, запись с именем не из словаря или с адресом вне домашнего хоста —
    маркер (условие (в) не выполнен).
    """
    attribution = parse_value_attribution(row.get("scope"))
    if attribution is None:
        return None
    spec = primary_source(attribution.primary_key)
    if spec is None or not spec.home_hosts:
        return _UNNAMED_POINTER
    if str(attribution.primary_name) != spec.name:
        # запись противоречит словарю: называть производителя по такой улике нельзя
        return _UNNAMED_POINTER
    primary_uri = attribution.primary_uri
    if not primary_uri or not is_home_host(primary_uri, spec.home_hosts):
        # запись назвала производителя, но её адрес не принадлежит его домашнему хосту:
        # сравнивать надо метку хоста, а registrable domain у `.gov.ru` один на всех (T7.75)
        return _UNNAMED_POINTER
    return str(spec.name)


def _parent_pointer(row: Mapping[str, Any]) -> Any:
    """Указатель из якоря-родителя строки источника: хост якоря — производитель из словаря.

    `resolve_primary_source` (`apps/research_proxy/derivative_pointer.py`) ставит родителем либо
    прочитанную страницу первоисточника, либо якорь с адресом словаря — поэтому домашний хост
    родителя и есть указатель. Родитель без адреса или на чужом хосте указателем не считается:
    он ничего не говорит о производителе значения.
    """
    if row.get("parent_source_id") is None and row.get("parent_uri") is None:
        return None
    parent_uri = row.get("parent_uri")
    if not isinstance(parent_uri, str) or not parent_uri:
        return _UNNAMED_POINTER
    for spec in PRIMARY_SOURCES:
        if spec.home_hosts and is_home_host(parent_uri, spec.home_hosts):
            return str(spec.name)
    return None


def producer_publication_name(
    evidence_rows: Iterable[Mapping[str, Any]],
    *,
    reasons: Iterable[str],
) -> str | None:
    """Имя производителя, вокруг которого свернулась действующая оценка, либо None.

    `evidence_rows` — улики утверждения с колонками действующей оценки:
    `assessment_role` (роль улики в этой оценке; None — оценка эту улику не использовала),
    `group_id` (записанная группа независимости из снимка этой оценки), `scope` (payload улики,
    включая `value_attribution`) и `parent_uri`/`parent_source_id` (якорь-родитель источника).
    `reasons` — записанные причины этой же оценки.

    None означает «случай не признан»: витрина показывает прежнюю подпись без изменений.
    """
    if not any(str(reason) in INDEPENDENCE_SHORTFALL_REASONS for reason in reasons):
        return None

    supports: list[Mapping[str, Any]] = []
    for row in evidence_rows:
        role = row.get("assessment_role")
        if role is None:
            # эта улика не входила в действующую оценку: она ничего не утверждает о ней
            continue
        if str(role) == _COUNTER_ROLE:
            return None  # условие (г)
        if str(role) != _SUPPORT_ROLE:
            continue
        supports.append(row)

    if not supports:
        return None

    group_ids: set[str] = set()
    for row in supports:
        group_id = row.get("group_id")
        if not isinstance(group_id, str) or not group_id:
            return None  # условие (б) нельзя прочитать из записанных групп
        group_ids.add(group_id)
    if len(group_ids) != 1:
        return None

    names: set[str] = set()
    for row in supports:
        for pointer in (_attribution_pointer(row), _parent_pointer(row)):
            if pointer is _UNNAMED_POINTER:
                return None  # условие (в): указатель есть, но производитель по нему не читается
            if isinstance(pointer, str) and pointer:
                names.add(pointer)
    if not names or len(names) > 1:
        return None

    return next(iter(names))
