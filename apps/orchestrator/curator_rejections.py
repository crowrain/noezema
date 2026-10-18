"""ADR-0034 вариант B (T7.105): пооперационный отказ на границе куратора.

Прежняя политика границы: любая отклонённая проверка — и проверка операции claim, и
свойство предложения целиком — отменяла ВСЁ предложение (fail-closed `return 0, 0`).
Решение пользователя от 2026-10-12 зафиксировало вариант B: проверки, касающиеся ОДНОЙ
операции claim, снимают только эту операцию с именованной причиной; проверки свойства
предложения целиком (индексы уликовых связей вне списка, бюджеты новых вопросов,
структурная невалидность всего ответа) по-прежнему отклоняют предложение полностью.

Разделение строк — по их форме, а не по месту вызова: строки `claim[<i>]…search_statements[…]`
и `claim <i> (<тип>): …` (строки правил) называют ОДНУ операцию; остальные строки
структурного гейта (`evidence_link[…]: … out of range`, `new_questions: …`,
`claim[<i>].dependencies[…]: duplicate …`) — свойства предложения целиком (ADR-0032 §4:
бюджет — свойство предложения, выбрасывать операции по частям тут нельзя).

Что происходит со снятой операцией (ADR-0034 §7):
* её уликовые связи (`evidence_links`) снимаются вместе с ней и называются в записи отказа;
  наблюдения и источники остаются в базе — знание не удаляется; связи оставшихся операций
  НЕ переносится на другие claim (никто не решает за модель, какое evidence что подтверждает);
* `dependencies` снятой операции снимаются с той же причиной — висячих рёбер не появляется;
* `new_questions` и `relied_claim_ids` не тронуты: это не операции claim.

Запись отказа — аддитивный ключ `rejected_operations` В УЖЕ СУЩЕСТВУЮЩЕМ событии
`claim_created` (паттерн `value_duplicates` T7.83 / `relied_claims_rejected` T7.82):
нового `AuditEventType` нет, миграции нет. Счётчики «предложено» и «применено» разделены и
названы (`claims_proposed` / `claims_applied`). Ключи `rejected_proposal` и
`curator_rejected_by_rules` остаются тем, чем были: отказом предложения ЦЕЛИКОМ (так же и
когда сняты все операции — поведение как раньше).

Гейт «значение на связи» (T7.108 часть а, ADR-0034 §10) использует тот же механизм снятия
операций: `links_without_claim_value` называет уликовые связи, у которых измеренного
значения claim'а нет ни во фрагменте улики, ни во всём сохранённом тексте страницы (проверка
касается только `supports`-связей улик `source_assertion`; claim без измеренного значения и
связи опровержения гейтом не проверяются), `rejected_link_entries` даёт записи снятых связей
для ленты отказа. Связь снимается ещё до проверки правил; снятая связь не переносится ни на
какой другой claim (тот же запрет, что у T7.105). Если у операции не осталось ни одной
supports-связи со значением, она снимается этой функцией целиком с причиной
`no linked evidence contains the claim value` — ровно как отказ по правилам, через
`rejected_operations`; когда сняты все операции, предложение отклоняется целиком (публичный
отказ называет и причину гейта, и снятые связи).

Дополнение T7.113 (часть 2, ADR-0034 §10): значение claim'а — диапазон
(`claim_match_values`, составной токен вида `2.1-2.2`) — подтверждает связь только целиком:
диапазон как одно значение (те же формы и эквивалентность T7.109/T7.109a) либо оба конца
отдельными числами (таблица «мин./макс.» с ячейками «2,1» и «2,2»). Одна граница диапазона
связью не подтверждается: стендовая связка 4e7ba8c2 («2,1–2,2% по данным Росстата») ↔ страница
finmarket с одним «2,2%» проходила гейт эпохи T7.108a и подтверждала половину утверждения.
Одиночные значения claim'а читает прежний `claim_values_found_in_texts` — их поведение не
изменено ни на пункт; форма причины снятия не изменена.

Дополнение T7.123 (часть 2): когда измеренного профиля у утверждения нет вовсе, гейт
использует «голые» десятичные числа T7.120 (`_claim_bare_values`) — тот же профиль, что у
pairing-фильтров атрибуции; утверждение без измеренного значения остаётся вне гейта только
если у него нет и голого числа. Связи перепроверок (явных и обращённых гейтом дублей)
проверяются наравне со связями новых claims: освобождения для `existing_claim_id` в этом
гейте не было и нет (стенд 64837cb5/claim[3]: «9,6» не измеренное значение, гейт молчал,
связь к чужой странице прошла).

Модуль чистый: никаких БД и LLM — только разбор строк и дайджесты (формы записей совпадают
с дайджестом T7.84: те же `_claim_entry` / `_link_entry` из `rejected_proposal`, чтобы
«digest операции» в записи отказа был тем же снимком операции, что видит оператор в отказе
предложения).
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from typing import TYPE_CHECKING

from apps.orchestrator.rejected_proposal import _claim_entry, _link_entry
from apps.research_proxy.value_attribution import (
    _claim_bare_values,  # T7.123: тот же «голый» профиль значения, что у pairing-фильтров T7.120
    claim_match_values,
    claim_range_tokens,
    claim_ranges_found_in_texts,
    claim_value_numbers,
    claim_values_found_in_texts,
)
from packages.domain.models.base import JsonDict

if TYPE_CHECKING:
    from packages.domain.schemas.staging import CuratorProposal

#: сколько записей отказа операций показывается — тот же порядок, что `problems[:10]`
REJECTED_OPERATIONS_LIMIT = 10

# строка структурного гейта про одну операцию: `claim[<i>].search_statements[<k>]: …`
_SEARCH_STATEMENT_LINE_RE = re.compile(r"^claim\[(\d+)\]\.search_statements\[\d+\]:")
# строка структурного гейта, называющая операцию индексом: `claim[<i>]…`
_CLAIM_BRACKET_RE = re.compile(r"^claim\[(\d+)\]")
# строка правил движка, называющая операцию: `claim <i> (<claim_type>): …` или
# `claim <i>: unknown claim_type …` (обе формы даёт validate_claim_proposal)
_RULES_CLAIM_LINE_RE = re.compile(r"^claim (\d+)(?: \([^)]*\))?:")


def claim_index_from_line(line: str) -> int | None:
    """Индекс операции, которую называет строка отказа; None — строка не про операцию."""
    match = _CLAIM_BRACKET_RE.match(line) or _RULES_CLAIM_LINE_RE.match(line)
    return None if match is None else int(match.group(1))


def split_structural_problems(
    problems: list[str],
) -> tuple[list[str], dict[int, list[str]]]:
    """Строки структурного гейта: свойства предложения целиком vs строки одной операции.

    Из строк `structural_problems` пооперационной является ровно проверка `search_statements`
    (ADR-0034 §7, решение 1): она называет конкретный claim и не зависит ни от какого другого
    решения границы. Все остальные строки — `evidence_link[…]: evidence_index … out of range`,
    `new_questions …`, `claim[<i>].dependencies[…]: duplicate …` — остаются отказом предложения
    целиком: индексы связей и бюджеты хост не вправе пересобирать за модель.
    """
    whole: list[str] = []
    per_operation: dict[int, list[str]] = {}
    for line in problems:
        match = _SEARCH_STATEMENT_LINE_RE.match(line)
        if match is not None:
            per_operation.setdefault(int(match.group(1)), []).append(line)
        else:
            whole.append(line)
    return whole, per_operation


def rules_problems_by_operation(problems: list[str]) -> dict[int, list[str]]:
    """Строки предпроверки правил, сгруппированные по названной ими операции.

    Строка вида `claim <i> (<claim_type>): <проблема>` (и её строки-уточнения без префикса,
    идущие сразу за ней) относятся к операции `i`; строка без индекса уточняет предыдущую."""
    grouped: dict[int, list[str]] = {}
    current: int | None = None
    for line in problems:
        match = _RULES_CLAIM_LINE_RE.match(line)
        if match is not None:
            current = int(match.group(1))
            grouped.setdefault(current, []).append(line)
        elif current is not None:
            grouped[current].append(line)
    return grouped


def rejected_operation_entries(
    proposal: CuratorProposal,
    dropped: dict[int, str],
) -> list[JsonDict]:
    """Записи снятых операций: индекс, именованная причина, digest операции.

    К записи добавляются снимки снятых вместе с операцией уликовых связей и dependencies —
    ровно те формы, что дайджест T7.84 использует для связей и операций. Уликовые связи
    оставшихся операций при этом не переносится никуда: связь без своей операции просто
    перестаёт существовать, и это видно в отказе.
    """
    entries: list[JsonDict] = []
    for index in sorted(dropped):
        claim = proposal.claims[index]
        entry: JsonDict = {
            "claim_index": index,
            "reason": dropped[index],
            "operation": _claim_entry(claim, index),
        }
        links = [_link_entry(link) for link in proposal.evidence_links if link.claim_index == index]
        if links:
            entry["evidence_links_rejected"] = links
        dependencies = [
            {"claim_id": str(dep.claim_id), "kind": dep.kind.value} for dep in claim.dependencies
        ]
        if dependencies:
            entry["dependencies_rejected"] = dependencies
        entries.append(entry)
    return entries[:REJECTED_OPERATIONS_LIMIT]


# ─── T7.108 (часть а, ADR-0034 §10): гейт «значение на связи» ────────────────
#
# Связь `supports` обязана нести значение утверждения: она остаётся только если фрагмент
# ИЛИ весь сохранённый текст страницы содержит хотя бы одно измеренное значение утверждения
# целым числовым токеном (стенд 2026-08-29: supports-связи к страницам без значения
# «2,2» — карточка утверждала сверку с источником, которого нет). `link_texts[k]` —
# (фрагмент улики, полный текст страницы) для улик `source_assertion`; None — улика другого
# вида или вне диапазона (гейт молчит). `counters` гейт не трогает принципиально:
# опровержение обязано жить, даже когда опровергающий текст значения не называет.
# Утверждение без измеренного значения не проверяется. Снятая связь — запись
# `rejected_links` (аддитивный ключ уже существующего события, паттерн `rejected_operations`
# выше); если у операции сняты ВСЕ её связи, она снимается своей причиной (вариант B),
# и её связи остаются названными её записью (отдельной записью связи не дублируются).


def links_without_claim_value(
    *,
    proposal: CuratorProposal,
    dropped: dict[int, str],
    link_texts: Mapping[int, tuple[str | None, str | None] | None],
    statement_overrides: Mapping[int, str] | None = None,
) -> tuple[dict[int, str], dict[int, str]]:
    """Связи без значения утверждения и операции, у которых сняты все связи.

    Возвращает `(removed, claim_drops)`: `removed` — индекс связи в `proposal.evidence_links`
    -> именованная причина; `claim_drops` — индекс операции -> причина снятия (только
    операции, которых задел этот гейт и у которых после снятия не осталось ни одной связи).
    Связи уже снятых операций не проверяются и не снимаются повторно: их описывает запись
    операции. Функция чистая и вызывается ДО предпроверки правил и гейта дублей: они обязаны
    видеть отфильтрованные связи (ADR-0034 §10).

    Дополнение T7.123 (часть 2): профиль значения утверждения, как у pairing-фильтров
    T7.120, — измеренные значения, а когда их нет вовсе — «голые» десятичные числа
    (`_claim_bare_values`: число с разделителем без единицы; целые числа и годы не собираются).
    Стенд 64837cb5/claim[3] («hh-индекс … составил 9,6»): измеренного профиля нет, гейт молчал,
    и supports-связь к странице про ключевую ставку (где 9,6 нет) осталась. Смешанный профиль
    (есть измеренное значение) проверяется ровно как прежде: голые числа к нему не добавляются.
    Перепроверка (явная и обращённая) проверяется формулировкой цели (`statement_overrides`):
    собственная строка операции — пересказ (audit-only, ADR-0033).
    """
    removed: dict[int, str] = {}
    claims_count = len(proposal.claims)
    for k, link in enumerate(proposal.evidence_links):
        if link.relation.value != "supports":
            continue  # опровержение без значения живёт: гейт его не трогает
        claim_index = link.claim_index
        if claim_index in dropped or not 0 <= claim_index < claims_count:
            continue  # снятую операцию описывает её запись; вне диапазона — структурный гейт
        texts = link_texts.get(k)
        if texts is None:
            continue  # улика не `source_assertion` (или вне диапазона): гейт молчит
        statement = str(proposal.claims[claim_index].statement or "").strip()
        if proposal.claims[claim_index].existing_claim_id is not None and statement_overrides:
            # T7.123 (часть 2): предмет перепроверки — формулировка ЦЕЛИ (ADR-0033:
            # собственная строка операции есть пересказ, audit-only), профиль значения берётся
            # из неё; при отсутствии цели откатываемся на собственную строку
            statement = str(statement_overrides.get(claim_index) or "").strip() or statement
        values = frozenset(claim_value_numbers(statement))
        match_values = claim_match_values(statement)
        bare = frozenset() if match_values else _claim_bare_values(statement)
        if not values and not match_values and not bare:
            continue  # утверждение без числового профиля не проверяется (T7.108, T7.120)
        # T7.113 (часть 2): диапазоны утверждения подтверждают связь только целиком (один
        # диапазон как значение либо оба конца отдельными числами); одиночные значения —
        # прежний читатель текстовых форм T7.108a, его поведение не изменено
        ranges = claim_range_tokens(match_values)
        singles = (match_values - ranges) | bare
        if claim_values_found_in_texts(values=singles, texts=texts):
            continue
        if ranges and claim_ranges_found_in_texts(ranges=ranges, texts=texts):
            continue
        removed[k] = (
            f"evidence_link[{k}]: evidence {link.evidence_index} "
            f"does not contain the value of claim {claim_index}"
        )
    claim_drops: dict[int, str] = {}
    if removed:
        kept_links = Counter(
            link.claim_index
            for k, link in enumerate(proposal.evidence_links)
            if k not in removed
        )
        touched = sorted({proposal.evidence_links[k].claim_index for k in removed})
        for claim_index in touched:
            if kept_links.get(claim_index, 0) == 0:
                claim_drops[claim_index] = (
                    f"claim[{claim_index}]: no linked evidence contains the claim value"
                )
    return removed, claim_drops


def rejected_link_entries(
    *,
    proposal: CuratorProposal,
    removed_links: Mapping[int, str],
) -> list[JsonDict]:
    """Записи снятых связей: индекс связи + тот же снимок связи, что дайджест T7.84
    (`_link_entry`), + именованная причина. Связи покрытых записью снятой операции сюда
    не пишутся — это решает вызывающая сторона (двойного счёта в журнале быть не должно)."""
    entries: list[JsonDict] = []
    for k in sorted(removed_links):
        if not 0 <= k < len(proposal.evidence_links):
            continue
        entry: JsonDict = {"link_index": k, **_link_entry(proposal.evidence_links[k])}
        entry["reason"] = removed_links[k]
        entries.append(entry)
    return entries[:REJECTED_OPERATIONS_LIMIT]
