"""ADR-0034 вариант C (T7.106 часть б): раунд исправления кураторского предложения.

Когда валидация хоста или движок правил ОТКЛОНЯЕТ ответ куратора ЦЕЛИКОМ (любое из
четырёх мест apps/orchestrator/orchestrator.py: structural-гейт, снятие всех операций
перепроверкой, as_of-гейт, отказ правил), хост ровно ОДИН раз за вызов куратора
(= за сессию) возвращает предложение модели на исправление: второй запрос с разделом
«# Исправление предложения» и повторная проверка тем же помощником. Вариант B (T7.105)
не затронут: частичный пооперационный отказ раунд НЕ запускает.

T7.106a (ADR-0034 §9): раздел НИКОГДА не теряет операции и связи — JSON отклонённого
предложения полный (без omitted_* счётчиков), перечень `[i]` — по ВСЕМ claims. Если
полный раздел не помещается в потолок, построитель возвращает «не помещается»
(`CorrectionOverLimit` с длиной), а не урезанный текст: раунда нет, прежний отказ
целиком стоит с аддитивным ключом `correction_skipped` (ветка `_curator`).

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
#: T7.106a: раздел не режется — JSON полный, перечень по всем claims, поэтому потолок
#: выбирается из бюджета ВХОДА куратора (§5.4.1), а не из скромности самого раздела:
#: input_budget = min(131072, 131072) − 8192 − 2048 = 120832 оценённых токенов
#: (model-блок config-v24; packages/cognition/tokenizer.py:48-50). Остальной вход
#: куратора ограничен: суммы лимитов секций контекст-пака = 26624 токена
#: (token_budgets config-v24, проверяются тем же модулем tokenizer.py:62-67), системный
#: промпт curator-v14 ≈ 3418 токенов, блок «# Evidence» при потолке
#: max_evidence_items_per_session = 64 и фрагментах ≤ 2 × SOURCE_ASSERTION_TEXT_BUDGET
#: (apps/orchestrator/evidence.py:73, :83) ≈ ≤ 45568 токенов измеримого худшего
#: случая; вопрос ≤ 2000 знаков. Оценка токенов никогда не превышает число знаков
#: (packages/cognition/tokenizer.py:28-32), поэтому раздел в 24000 знаков добавляет
#: ≤ 24000 токенов в абсолютном худшем случае (измеренная плотность полного JSON
#: claims — ≈ 4 раза меньше). Худшая сумма ≈ 99610 < 120832: потолок 24000 знаков не
#: способен вывести запрос куратора за бюджет входа.
CORRECTION_SECTION_MAX_CHARS = 24_000
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


@dataclass(frozen=True)
class CorrectionOverLimit:
    """T7.106a (ADR-0034 §9, правило 3): ПОЛНЫЙ раздел (целиком JSON предложения и
    перечень по всем claims) не помещается в `CORRECTION_SECTION_MAX_CHARS`. Раздел не
    урезается: построитель называет длину (`chars`) и потолок (`limit`), а `_curator`
    оставляет прежний отказ целиком с аддитивным ключом `correction_skipped`. Раунда
    исправления нет — модель не получает предложение, из которого что-то пропало."""

    chars: int
    limit: int


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


_CORRECTION_INTRO = (
    "# Исправление предложения\n"
    "Хост отклонил твоё предложение ЦЕЛИКОМ. Верни ПОЛНОЕ предложение заново, по той же\n"
    "схеме ответа, исправив перечисленные проблемы. Индексы claims и evidence — с нуля\n"
    "(первый claim — 0). Ничего не добавляй ради исправления: ни новых claims, ни новых\n"
    "evidence, ни новых вопросов; значения и периоды бери только из текста улик выше.\n"
)


def build_correction_section(reasons: list[str], proposal: CuratorProposal) -> str | CorrectionOverLimit:
    """Раздел второго запроса куратора (ADR-0034 вариант C, T7.106; правило T7.106a).

    Состав: дословные причины хоста; отклонённое предложение JSON'ом ЦЕЛИКОМ (то, что
    вернула сама модель, model_dump(mode="json") — без резки и без omitted_* счётчиков);
    перечень ВСЕХ claims с индексами `[0] …`, `[1] …` и их число; напоминание про
    индексы с нуля и полный повтор предложения. Резать целыми строками с маркером
    разрешено только причины хоста (T7.106a, правило 1): они описывают отказ, а не
    входят в предложение. Никакого внешнего текста (страниц, улик) раздел не несёт:
    только причины хоста и собственные данные модели.
    Детерминирован и чист (ни БД, ни шлюза). Если полный раздел превышает потолок
    `CORRECTION_SECTION_MAX_CHARS`, возвращается `CorrectionOverLimit` (с длиной), а не
    урезанный текст: раунда исправления не будет (T7.106a, правило 3).
    """

    intro = _CORRECTION_INTRO
    remaining = CORRECTION_SECTION_MAX_CHARS - len(intro) - 8
    reasons_text, _omitted_reasons = _reasons_block(reasons, remaining // 3)
    reasons_part = f"\nПричины хоста (дословно):\n{reasons_text}\n"
    json_text = json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False)
    json_part = f"\nОтклонённое предложение (JSON, как ты его вернул):\n{json_text}\n"

    header = f"\nClaims отклонённого предложения (всего {len(proposal.claims)}), индексы с нуля:\n"
    claims_part = header + "\n".join(f"[{i}] «{_snippet(c.statement)}»" for i, c in enumerate(proposal.claims)) + "\n"

    section = intro + reasons_part + json_part + claims_part
    if len(section) > CORRECTION_SECTION_MAX_CHARS:
        return CorrectionOverLimit(chars=len(section), limit=CORRECTION_SECTION_MAX_CHARS)
    return section
