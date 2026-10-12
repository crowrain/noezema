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

Модуль чистый: никаких БД и LLM — только разбор строк и дайджесты (формы записей совпадают
с дайджестом T7.84: те же `_claim_entry` / `_link_entry` из `rejected_proposal`, чтобы
«digest операции» в записи отказа был тем же снимком операции, что видит оператор в отказе
предложения).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from apps.orchestrator.rejected_proposal import _claim_entry, _link_entry
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
