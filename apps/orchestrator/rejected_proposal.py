"""Компактный дайджест ОТКЛОНЁННОГО кураторского предложения (T7.84, часть A).

Дефект стенда .92 (сессия ed36f4a0, config-v20, curator-v10): куратор принёс два
`temporal_fact` без `as_of`; хост отклонил всё предложение fail-closed. В журнале остались
только строки причины (`curator_rejected: ["claim[0]: temporal_fact requires as_of", …]`).
Содержимое отклонённого предложения — что именно модель предлагала записать — не осталось
нигде: `claim_created` не написан, сырой ответ модели в `model_runs` не сохраняется. Без
дайджеста нельзя отличить перепроверку уже записанного утверждения от выдуманного нового —
ровно то и требовал оператор на стенде.

Границы правки (не двигаются): дайджест кладётся В PAYLOAD ТОГО ЖЕ существующего события
отказа (`SESSION_STATE_CHANGED`; ключи `curator_rejected`, `curator_reject_kind`,
`curator_rejected_by_rules` остаются прежними), нового `AuditEventType` нет, миграции нет,
API-поля не меняются. Текст модели идёт только в журнал: ни в `claims`, ни в `evidence`, ни в
search-индекс он не попадает и никогда не становится знанием.

Обоснование потолков. Предложение куратора на стенде — 1–2 операции, 5 уликовых связей,
`max_output_tokens = 693`: реальный дайджест всегда помещается целиком. Потолки нужны против
вырожденного предложения, выжатого из схемы: `statement` ≤ 2000 знаков на операцию,
`summary` ≤ 2000. Без потолка payload отказа стал бы «второй копией отклонённого предложения»
(10+ КБ) в событии, которое читают глазами. Поэтому:

* формулировка операции — 300 знаков (та же длина, что хост уже показывает в строках
  причины и в fence'ах контекста);
* `summary` — 500 знаков;
* опорные id — столько, сколько хост записывает вообще (`RELIED_CLAIMS_LIMIT = 8`);
* весь дайджест — `REJECTED_PROPOSAL_MAX_CHARS = 4096`, тот же порядок, что у прежнего
  payload'а `problems[:10]`, и гарантированно меньше отклонённого предложения.

Не помещившееся снимается ЦЕЛЫМИ записями и называется числом (урок T7.76: усечённая
посередине запись уже не запись): операции приоритетнее связей — именно их содержимое и
терялось. Резерв `_COUNTER_RESERVE` оставляет место самим счётчикам, чтобы итоговый payload
никогда не превышал потолок.

Маскирование NUL (T7.46a): текст приходит от модели, JSONB не переносит U+0000, и один NUL
уже ронял whole-транзакцию фазы 1. Строки дайджеста маскируются до обрезки (граница аудита
маскирует ещё раз; операция идемпотентна), так что метка `\x00` видна, а длина считается по
маскированному тексту.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from packages.domain.models.base import JsonDict
from packages.domain.sanitization import mask_nul

if TYPE_CHECKING:
    from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

#: длина одной формулировки в дайджесте (знаков)
REJECTED_STATEMENT_CHARS = 300
#: длина кураторского summary в дайджесте (знаков)
REJECTED_SUMMARY_CHARS = 500
#: сколько опорных id показывается — столько же, сколько хост записывает в payload ответа
REJECTED_RELIED_LIMIT = 8
#: потолок всего дайджеста, в знаках сериализованного JSON
REJECTED_PROPOSAL_MAX_CHARS = 4096
#: место под счётчики снятого (`omitted_claims`, `omitted_evidence_links`)
_COUNTER_RESERVE = 64

#: метка обрезки: усечение названо, а не прятано
_TRUNCATION_MARK = "…"


def _clip(text: str, limit: int) -> str:
    """Смаскировать NUL, затем урезать ровно до `limit` знаков, назвав усечение."""
    masked = mask_nul(text)
    if len(masked) <= limit:
        return masked
    return masked[: limit - len(_TRUNCATION_MARK)] + _TRUNCATION_MARK


def _size(value: object) -> int:
    """Длина JSON, который реально был бы записан в payload."""
    return len(json.dumps(value, ensure_ascii=False))


def _claim_entry(claim: ClaimProposal, index: int) -> JsonDict:
    as_of = claim.as_of
    return {
        "claim_index": index,
        "statement": _clip(claim.statement, REJECTED_STATEMENT_CHARS),
        "claim_type": claim.claim_type.value,
        "as_of": None if as_of is None else as_of.isoformat(),
        "existing_claim_id": claim.existing_claim_id,
        # ключи scope, а не значения: по ним видно, объявил ли куратор период/показатель,
        # и чужой текст (например, URL в scope) журнал не раздувает
        "scope_keys": sorted(key for key in claim.scope if isinstance(key, str)),
    }


def _link_entry(link: EvidenceLink) -> JsonDict:
    """Связь называется индексами предложения — теми же, что проверяет структурный гейт."""
    return {
        "claim_index": link.claim_index,
        "evidence_index": link.evidence_index,
        "relation": link.relation.value,
    }


def rejected_proposal_digest(proposal: CuratorProposal) -> JsonDict:
    """Читаемый снимок предложения, которое хост отклонил, с потолком по размеру.

    Называется только то, что хост уже увидел структурно: операции claim (с полями, по
    которым опознать отказ), уликовые связи (индексы + отношение), объявленные опоры, число
    новых вопросов и summary куратора. Оценок, текстов улик и знания здесь нет — и быть не
    может: дайджест описывает ОТКАЗ, а не фиксирует вывод.
    """
    budget = REJECTED_PROPOSAL_MAX_CHARS - _COUNTER_RESERVE
    base: JsonDict = {
        "summary": _clip(proposal.summary, REJECTED_SUMMARY_CHARS),
        "relied_claim_ids": [mask_nul(item) for item in proposal.relied_claim_ids[:REJECTED_RELIED_LIMIT]],
        "new_questions": len(proposal.new_questions),
    }

    claims: list[JsonDict] = []
    omitted_claims = 0
    for index, claim in enumerate(proposal.claims):
        candidate = [*claims, _claim_entry(claim, index)]
        if _size({**base, "claims": candidate, "evidence_links": []}) <= budget:
            claims = candidate
        else:
            omitted_claims = len(proposal.claims) - index
            break

    links: list[JsonDict] = []
    omitted_links = 0
    for link in proposal.evidence_links:
        candidate = [*links, _link_entry(link)]
        if _size({**base, "claims": claims, "evidence_links": candidate}) <= budget:
            links = candidate
        else:
            omitted_links = len(proposal.evidence_links) - len(links)
            break

    digest: JsonDict = {**base, "claims": claims, "evidence_links": links}
    if omitted_claims:
        digest["omitted_claims"] = omitted_claims
    if omitted_links:
        digest["omitted_evidence_links"] = omitted_links
    return digest
