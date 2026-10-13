"""ADR-0034 вариант C (T7.106 часть б): раунд исправления кураторского предложения.

Когда валидация хоста или движок правил ОТКЛОНЯЕТ ответ куратора ЦЕЛИКОМ (любое из
четырёх мест apps/orchestrator/orchestrator.py: structural-гейт, снятие всех операций
перепроверкой, as_of-гейт, отказ правил), хост ровно ОДИН раз за вызов куратора
(= за сессию) возвращает предложение модели на исправление: второй запрос с разделом
«# Исправление предложения» и повторная проверка тем же помощником. Вариант B (T7.105)
не затронут: частичный пооперационный отказ раунд НЕ запускает.

Модуль держит ЧИСТЫЕ части этого потока: исходы граничной проверки (`BoundaryAccepted`
/ `BoundaryRejected`) и раздел исправления второго запроса. Ни БД, ни LLM-шлюза здесь
нет — та же дисциплина, что у apps/orchestrator/curator_rejections.py.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from packages.domain.models.base import JsonDict
from packages.domain.sanitization import mask_nul

if TYPE_CHECKING:
    from packages.domain.schemas.staging import ClaimProposal, CuratorProposal

#: Потолок длины раздела исправления во втором запросе куратора (символов).
#: Раздел не может раздуть контекст: предложения claims режутся ЦЕЛЫМИ записями
#: (хвост списка), как в apps/orchestrator/rejected_proposal.py.
CORRECTION_SECTION_MAX_CHARS = 6000
#: Потолок длины одной строки перечня claims (в JSON остаётся дословно).
CORRECTION_CLAIM_SNIPPET_CHARS = 120


@dataclass(frozen=True)
class BoundaryAccepted:
    """Граничная проверка приняла предложение. Поля — те же локальные величины,
    которые считал `_curator` до staging (T7.105), только вынесенные наружу:
    `staged_claims` — claims после снятия отклонённых операций, `dropped` —
    индекс операции -> причина (пооперационный отказ, ключ `rejected_operations`
    в claim_created), `value_duplicates` — аддитивный журнал гейта дублей (T7.83/ADR-0033),
    `relied_claim_ids` / `relied_claims_rejected` — журнал факультативного поля
    `relied_claim_ids` (T7.82/ADR-0032)."""

    staged_claims: list[ClaimProposal]
    dropped: dict[int, str]
    value_duplicates: list[JsonDict]
    relied_claim_ids: list[str]
    relied_claims_rejected: list[str]


@dataclass(frozen=True)
class BoundaryRejected:
    """Хост ОТКЛОНЯЕТ предложение целиком. `payload` и `public_summary` — ровно те,
    которые хост пишет в audit_events при прежнем отказе (байтовая совместимость
    ключей T7.105), `reasons` — список причин этого отказа; он же уходит в раздел
    исправления и в ключ `correction_round` итогового события."""

    payload: JsonDict
    public_summary: str
    reasons: list[str]


def _snippet(text: str) -> str:
    clean = mask_nul(text)
    if len(clean) <= CORRECTION_CLAIM_SNIPPET_CHARS:
        return clean
    return clean[: CORRECTION_CLAIM_SNIPPET_CHARS - 1].rstrip() + "…"


def _reasons_block(reasons: list[str], budget: int) -> tuple[str, int]:
    """Блок причин хоста: дословные строки (при переполнении режутся целыми
    строками), возвращает (текст, сколько причин опущено)."""
    lines: list[str] = []
    used = 0
    omitted = 0
    for index, reason in enumerate(reasons):
        line = f"- {mask_nul(reason)}"
        if used + len(line) + 1 > budget:
            omitted = len(reasons) - index
            break
        lines.append(line)
        used += len(line) + 1
    text = "\n".join(lines)
    if omitted:
        text += f"\n…ещё {omitted} причин не показано из-за ограничения длины"
    return text, omitted


def _proposal_json(proposal: CuratorProposal, budget: int) -> tuple[str, list[ClaimProposal], int, int]:
    """JSON отклонённого предложения (то, что вернула модель, model_dump(mode="json")).
    При переполнении режется ЦЕЛЫМИ записями: сначала хвост claims, затем хвост
    evidence_links; пропущенное количество названо ключами omitted_claims /
    omitted_evidence_links. Возвращает (json_text, сохранённые_claims,
    omitted_claims, omitted_evidence_links)."""

    data: JsonDict = dict(proposal.model_dump(mode="json"))
    dump_claims = list(data.get("claims", []))
    dump_links = list(data.get("evidence_links", []))
    dump_relied = list(data.get("relied_claim_ids", []))
    dump_questions = list(data.get("new_questions", []))
    staged = list(proposal.claims)
    omitted_claims = 0
    omitted_links = 0
    omitted_relied = 0
    omitted_questions = 0
    while True:
        payload: JsonDict = dict(data)
        payload["claims"] = dump_claims
        payload["evidence_links"] = dump_links
        payload["relied_claim_ids"] = dump_relied
        payload["new_questions"] = dump_questions
        if omitted_claims:
            payload["omitted_claims"] = omitted_claims
        if omitted_links:
            payload["omitted_evidence_links"] = omitted_links
        if omitted_relied:
            payload["omitted_relied_claim_ids"] = omitted_relied
        if omitted_questions:
            payload["omitted_new_questions"] = omitted_questions
        text = json.dumps(payload, ensure_ascii=False)
        if len(text) <= budget or (
            not dump_claims and not dump_links and not dump_relied and not dump_questions
        ):
            return text, staged[: len(dump_claims)], omitted_claims, omitted_links
        if dump_claims:
            dump_claims.pop()
            staged.pop()
            omitted_claims += 1
        elif dump_links:
            dump_links.pop()
            omitted_links += 1
        elif dump_relied:
            dump_relied.pop()
            omitted_relied += 1
        else:
            dump_questions.pop()
            omitted_questions += 1


_CORRECTION_INTRO = (
    "# Исправление предложения\n"
    "Хост отклонил твоё предложение ЦЕЛИКОМ. Верни ПОЛНОЕ предложение заново, по той же\n"
    "схеме ответа, исправив перечисленные проблемы. Индексы claims и evidence — с нуля\n"
    "(первый claim — 0). Ничего не добавляй ради исправления: ни новых claims, ни новых\n"
    "evidence, ни новых вопросов; значения и периоды бери только из текста улик выше.\n"
)


def build_correction_section(reasons: list[str], proposal: CuratorProposal) -> str:
    """Раздел второго запроса куратора (ADR-0034 вариант C, T7.106).

    Состав: дословные причины хоста; отклонённое предложение JSON'ом (то, что вернула
    сама модель); перечень claims с индексами `[0] …`, `[1] …` и их число; напоминание
    про индексы с нуля и полный повтор предложения. Никакого внешнего текста (страниц,
    улик) раздел не несёт: только причины хоста и собственные данные модели.
    Детерминирован и ограничен `CORRECTION_SECTION_MAX_CHARS` (режется целыми
    записями, пропущенное количество названо в самом JSON).
    """

    intro = _CORRECTION_INTRO
    remaining = CORRECTION_SECTION_MAX_CHARS - len(intro) - 8
    reasons_text, _omitted_reasons = _reasons_block(reasons, remaining // 3)
    reasons_part = f"\nПричины хоста (дословно):\n{reasons_text}\n"
    json_budget = max(600, CORRECTION_SECTION_MAX_CHARS - len(intro) - len(reasons_part) - 400)
    json_text, kept_claims, omitted_claims, omitted_links = _proposal_json(proposal, json_budget)
    json_part = f"\nОтклонённое предложение (JSON, как ты его вернул):\n{json_text}\n"

    header = f"\nClaims отклонённого предложения (всего {len(proposal.claims)}), индексы с нуля:\n"
    lines: list[str] = []
    budget = max(0, CORRECTION_SECTION_MAX_CHARS - len(intro) - len(reasons_part) - len(json_part) - len(header))
    used = 0
    shown = 0
    for index, claim in enumerate(kept_claims):
        line = f"[{index}] «{_snippet(str(claim.statement))}»"
        if used + len(line) + 1 > budget:
            break
        lines.append(line)
        used += len(line) + 1
        shown += 1
    claims_part = header + "\n".join(lines)
    hidden = len(kept_claims) - shown
    if hidden or omitted_claims:
        parts_note: list[str] = []
        if hidden:
            parts_note.append(f"{hidden} claims не показаны из-за ограничения длины")
        if omitted_claims:
            parts_note.append(f"{omitted_claims} claims опущены из JSON (omitted_claims)")
        if omitted_links:
            parts_note.append(f"{omitted_links} evidence_links опущены из JSON (omitted_evidence_links)")
        claims_part += "\n…" + "; ".join(parts_note)
    claims_part += "\n"
    return intro + reasons_part + json_part + claims_part
