"""T7.123 (часть 3, ADR-0038 дополнение): пустой ответ куратора с содержательной сводкой.

Стендовый случай b7e93ff6 (config-v28, curator-v16): куратор вернул `claims: []` и пустую
опору, finish_reason=stop, а в `summary` прямым текстом объявил новое утверждение
(«предлагается создать новый claim с актуальным значением», «если факт изменился, мы
фиксируем новое состояние»). Хост принял пустое предложение как законное: карточка вопроса
оказалась в состоянии «Ответ не записан» при сводке, которая обещала запись. Пустое
предложение с объявленным намерением записать — дефект ответа, а не честный отказ:
модель сказала одно, предложила другое.

Правило (закрытый список оснований): если `claims` пуст, `relied_claim_ids`
пуст и в `summary` встречается одно из оснований списка ниже — предложение отклоняется
ЦЕЛИКОМ той же границей, что и остальные отказы (ADR-0034 вариант B: отказ предложения
целиком), и догоняется ровно одним раундом исправления варианта C. Второй ответ больше
этим основанием не проверяется (ровно один раунд, как сейчас): если claims пусты и после
раунда, предложение принимается как прежнее пустое, и работает запасной путь опоры T7.115
из `c:`-ссылок финального ответа исследователя. Основания — фиксированные формулировки;
новых AuditEventType нет: отказ называется аддитивным `curator_reject_kind` в уже
существующем событии отказа предложения.

T7.136 (часть 2, ADR-0038 дополнение) к правилу добавлены два основания, оба — по
зафиксированным сводкам стенда:

* (а) ОБОРВАННАЯ НА ПОТОЛКЕ сводка (стенд 33298707, контрольный прогон aaca104-v30): при
  рассуждении куратора, ВЫКЛЮЧЕННОМ в фазе consolidation (config-v18…v30, ADR-0030), модель
  ведёт рассуждение прямо в `summary`, упирается в `max_length` этого поля и обрывает текст
  на полуслове; JSON при этом валиден, `finish_reason=stop`, и хост принимал пустое
  предложение как честный итог. Основание срабатывает, только если пусты `claims`,
  `relied_claim_ids` И `new_questions`: честный пустой итог стенда короткий
  (самый длинный из пяти честных — 721 знак), а сводка на потолке — всегда оборванное
  рассуждение (четыре строки таблицы T7.136, все дефектные). Потолок берётся ИЗ СХЕМЫ
  (`CuratorProposal.summary.max_length`), а не литералом: правка поля двигает и гейт.
* (б) расширение закрытого списка формулировок («предлагаются перепроверки»,
  «предлагаю два computed_result-claim»): обе — из сводок стенда (33298707, fa5448d5), где
  куратор объявил запись операций при пустом `claims`. Общая формулировка («предлага…»,
  «перепровер…») не выбрана: по таблице T7.136 она задевает честные пустые ответы
  (099ddccb «…утверждение не предлагается», 96a0c535 и eaacdb15 содержат «перепровер»,
  96c710ff «новых claims не предлагается»).

Модуль чистый: ни БД, ни LLM (та же дисциплина, что у curator_rejections.py).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from packages.domain.schemas.staging import CuratorProposal

#: закрытый список оснований: сводка объявляет запись нового утверждения.
#: Формулировки взяты из зафиксированных сводок стенда (b7e93ff6, 33298707, fa5448d5 и
#: подсказка приёмки); новые основания списку не добавляются без отдельного решения.
DECLARED_NEW_CLAIM_BASES: Final[tuple[str, ...]] = (
    "создаем новый claim",
    "создаём новый claim",
    "предлагается создать",
    "фиксируем новое состояние",
    "предлагаются перепроверки",
    "предлагаю два computed_result-claim",
)


def _summary_max_length() -> int:
    """Потолок `summary` ИЗ СХЕМЫ: `max_length` первого поля `CuratorProposal`.

    Число не дублируется литералом: поле схемы — единственное место, где живёт потолок,
    и гейт обязан двигаться вместе с ним. Fail-closed: если ceiling исчез из схемы,
    молчаливый гейт хуже падения импорта.
    """
    for meta in CuratorProposal.model_fields["summary"].metadata:
        limit = getattr(meta, "max_length", None)
        if isinstance(limit, int):
            return limit
    raise RuntimeError("CuratorProposal.summary has no max_length to derive the ceiling from")


#: потолок сводки в знаках (T7.136): берётся из схемы, не из литерала
SUMMARY_MAX_CHARS: Final[int] = _summary_max_length()

#: именованная причина отказа (строка `curator_rejected` того же формата, что остальные)
DECLARED_EMPTY_REASON: Final[str] = "в сводке объявлено новое утверждение, но список claims пуст"

#: аддитивный `curator_reject_kind` уже существующего события отказа предложения
CURATOR_REJECT_KIND_DECLARED_EMPTY: Final[str] = "claims_empty_declared_in_summary"

#: публичный текст отказа (формальный, как у других отказов границы)
DECLARED_EMPTY_PUBLIC_SUMMARY: Final[str] = (
    "curator proposal rejected: summary declares a new claim but the claims list is empty"
)

#: причина отказа (а): текст говорит модели по-русски, что делать в раунде исправления;
#: число знаков — потолок поля из схемы, подставляется при сборке строки
_SUMMARY_CUT_REASON_TEMPLATE: Final[str] = (
    "сводка оборвалась на пределе {limit} знаков: сводка — 1–3 фразы итога, не рассуждение; "
    "предложи claims по уликам, о которых пишешь, или одной фразой скажи, "
    "почему записывать нечего"
)
SUMMARY_CUT_REASON: Final[str] = _SUMMARY_CUT_REASON_TEMPLATE.format(limit=SUMMARY_MAX_CHARS)

#: аддитивный `curator_reject_kind` того же события отказа предложения (T7.136 (а))
CURATOR_REJECT_KIND_SUMMARY_CUT: Final[str] = "claims_empty_summary_cut_at_limit"

#: публичный текст отказа (а)
SUMMARY_CUT_PUBLIC_SUMMARY: Final[str] = (
    "curator proposal rejected: summary is cut at its field limit and the claims list is empty"
)

#: сводка складывается в одну строку: регистр не значим, произвольный пробельный шум
#: (включая неразрывные пробелы и U+2028/U+2029 нормализованных текстов) сворачивается
_WS_RE: Final = re.compile(r"[\s\u00a0\u2028\u2029]+")


def summary_declares_new_claim(summary: str) -> bool:
    """Встречается ли в сводке одно из закрытых оснований «объявлено новое утверждение»."""
    folded = _WS_RE.sub(" ", summary.lower())
    return any(base in folded for base in DECLARED_NEW_CLAIM_BASES)


def summary_cut_at_limit(summary: str) -> bool:
    """Достигла ли сводка потолка `max_length` поля схемы (T7.136 (а)).

    Ровно `>=`, а не `>`: pydantic отбраковал бы и 2001 знак целиком, так что ответ модели
    физически не может быть длиннее потолка — обрыв виден именно как достижение потолка.
    """
    return len(summary) >= SUMMARY_MAX_CHARS


@dataclass(frozen=True)
class EmptyProposalDefect:
    """Названный отказ пустому предложению: строка причины, kind и публичный текст."""

    reason: str
    kind: str
    public_summary: str


def empty_proposal_defect(proposal: CuratorProposal) -> EmptyProposalDefect | None:
    """Чем именно пустое предложение — дефект ответа (или ничем).

    Основание (а) проверяется только для предложения без операций и без опоры, поэтому
    условие `claims`/`relied_claim_ids` повторено здесь, а не только на вызове: сводка на
    потолке с claims или с опорой — не дефект (стенды 8181a76a, c7e825e6, 066e1e5f).

    Порядок оснований значим: оборванная на потолке сводка называется ПЕРВОЙ, иначе
    случай 33298707 попал бы под формулировку (б) и модель получила бы менее полезную
    подсказку. `new_questions` учитывает только основание (а): пустой итог с вопросами —
    уже содержательная работа (стенд 96c710ff, 8a97308b), а не оборванное рассуждение.
    """
    if proposal.claims or proposal.relied_claim_ids:
        return None
    if not proposal.new_questions and summary_cut_at_limit(proposal.summary):
        return EmptyProposalDefect(
            reason=SUMMARY_CUT_REASON,
            kind=CURATOR_REJECT_KIND_SUMMARY_CUT,
            public_summary=SUMMARY_CUT_PUBLIC_SUMMARY,
        )
    if summary_declares_new_claim(proposal.summary):
        return EmptyProposalDefect(
            reason=DECLARED_EMPTY_REASON,
            kind=CURATOR_REJECT_KIND_DECLARED_EMPTY,
            public_summary=DECLARED_EMPTY_PUBLIC_SUMMARY,
        )
    return None
