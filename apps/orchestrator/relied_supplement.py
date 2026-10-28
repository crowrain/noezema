"""T7.139 (ADR-0038 §16): хостовое дополнение опоры на границе куратора.

Две части, оба — чистые функции (ни БД, ни LLM, ни изменения гейтов):

* часть 2 (`relied_from_dropped_operations`) — снятая операция-перепроверка больше
  не теряет уже записанное утверждение: если хост снял операцию (гейт даты прогноза,
  период, значение — причина не важна) и у неё был `existing_claim_id`, запись под
  этим id остаётся в знании, и хост переносит её id в опору ответа («использовано
  без перепроверки»). Это НЕ возвращение операции: ADR-0034 вариант B снял ОПЕРАЦИЮ,
  а не знание; опора — отдельное отношение карточки (T7.82, ADR-0032).
* часть 3 (`fresher_forecast_supplement`) — свежий прогноз той же серии: если в
  контекст-паке есть запись той же серии прогнозов, что и записи карточки, она
  актуальна, а ВСЕ карточные записи этой серии устарели, хост добавляет её в опору.
  Единственный источник правила серии — `apps/web/forecast_series.py` (витрина
  T7.135/T7.138); импорт ленивый внутри функции: это первое ребро orchestrator →
  модуль витрины (ADR-0038 §16).

Правила (закрытые, кодом):
(а) переносимый id разрешается строго по `resolve_claim_reference` среди id секции
    `claims_evidence` контекст-пака; id из `pending_claims`/`contradictions` или
    невидимый — именованный отказ `dropped_operation[k]: <причина>`;
(б) id уже в опоре — молчаливый пропуск; id остался действующей операцией сессии —
    не дублируется;
(в) потолок общий `RELIED_CLAIMS_LIMIT`: переполнение — именованный отказ
    `dropped_operation[k]: more than 8 relied ids` (часть 3 — `fresher_forecast[k]:`);
(г) новые утверждения (без `existing_claim_id`) не трогаются — записи под ними нет;
(д) причина снятия не важна — в опору идёт ЗАПИСАННАЯ запись.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

#: id записей ПРЕДЛОЖЕНИЯ (не записанных ещё утверждений) в карточке части 3:
#: та же форма, что ключ `new<i>` в причинных строках границы (`claim[i]`).
NEW_RECORD_REF = "new"


@dataclass(frozen=True)
class SupplementRecord:
    """Запись прогноза для части 3: `claim_id` — полный id записанного утверждения
    либо `new<i>` для нового утверждения предложения; `statement`/`as_of` — то, что
    видит витрина (формулировка и записанный якорь даты)."""

    claim_id: str
    statement: str
    as_of: datetime | date | None = None


def claims_evidence_ids(chunk_ids: Sequence[str]) -> list[uuid.UUID]:
    """Id утверждений секции `claims_evidence` контекст-пака (её `chunk_id` —
    `claim:<uuid>`) в порядке следования. Только эта секция — множество видимости
    переноса части 2: `pending_claims` и `contradictions` называют записи, на
    которые ответ НЕ опирается (они не переносятся)."""

    ids: list[uuid.UUID] = []
    for chunk_id in chunk_ids:
        if not chunk_id.startswith("claim:"):
            continue
        try:
            claim_id = uuid.UUID(chunk_id[len("claim:") :])
        except ValueError:
            continue
        if claim_id not in ids:
            ids.append(claim_id)
    return ids


def _relied_uuids(relied_ids: Iterable[str]) -> set[uuid.UUID]:
    """Уже занятые id опоры (полные UUID payload'а; неразбираемые строки не считаются)."""

    taken: set[uuid.UUID] = set()
    for raw in relied_ids:
        try:
            taken.add(uuid.UUID(str(raw)))
        except (ValueError, TypeError):
            continue
    return taken


def relied_from_dropped_operations(
    dropped_refs: Sequence[tuple[int, str]],
    pack_claims_evidence: list[uuid.UUID],
    relied_ids: Iterable[str],
    session_operation_ids: Iterable[uuid.UUID],
    limit: int,
) -> tuple[list[str], list[str]]:
    """Часть 2: перенос id снятых операций-перепроверок в опору.

    `dropped_refs` — пары (индекс операции в предложении, `existing_claim_id`) для снятых
    операций с записью; индексы без `existing_claim_id` в список не входят (правило (г)).
    Индекс `k` в строках отказов — ровно индекс операции в предложении (тот же, что в
    `claim[i]` причинных строках границы и в `rejected_operations`).
    `pack_claims_evidence` — id секции `claims_evidence` пака (правило (а)).
    Возвращает (перенесённые id, строки отказов) — строки отказов хост дописывает
    в `relied_claims_rejected` существующего payload'а.
    """

    from packages.memory.service import resolve_claim_reference

    taken = _relied_uuids(relied_ids)
    operation_ids = set(session_operation_ids)
    added: list[str] = []
    rejected: list[str] = []
    for k, raw in dropped_refs:
        ref_id, problem = resolve_claim_reference(str(raw), pack_claims_evidence)
        if ref_id is None or problem is not None:
            rejected.append(f"dropped_operation[{k}]: {problem or 'unparseable claim reference'}")
            continue
        if ref_id in taken:
            continue  # (б): id уже в опоре — молча
        if ref_id in operation_ids:
            continue  # (б): id остался действующей операцией — не дублируется
        if len(taken) >= limit:
            rejected.append(f"dropped_operation[{k}]: more than {limit} relied ids")
            continue
        taken.add(ref_id)
        added.append(str(ref_id))
    return added, rejected


def fresher_forecast_supplement(
    card_records: Sequence[SupplementRecord],
    pack_records: Sequence[SupplementRecord],
    relied_count: int,
    limit: int,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Часть 3: свежий прогноз той же серии, не тронутой карточкой.

    `card_records` — записи карточки (действующие операции предложения: новые — с
    id `new<i>`, перепроверки/слияния — записанные утверждения; плюс вся опора после
    части 2 и T7.115); `pack_records` — записи секции `claims_evidence`, НЕ попавшие
    на карточку (индекс `k` в строках отказов — позиция паковой записи в этом списке,
    т. е. порядок секции `claims_evidence`). Метки серий считаются функцией витрины `forecast_marks` по ОБЪЕДИНЕНИЮ
    карточных и паковых записей (иначе одинокая карточная запись серии осталась бы
    без метки и сравнение было бы невозможным). Паковая запись P добавляется, только
    если её метка — `actual`, среди карточных записей её серии есть хотя бы одна и
    ВСЕ карточные записи этой серии — `outdated`; карточка серии `undated` или уже
    `actual` — не добавляем. Возвращает (добавления, строки отказов); добавление —
    `{claim_id, series, replaces}`.
    """

    # ленивый импорт: первое ребро orchestrator → модуль витрины (ADR-0038 §16)
    from apps.web.forecast_series import ForecastRecord, forecast_marks

    def as_forecast(record: SupplementRecord) -> ForecastRecord:
        return ForecastRecord(claim_id=record.claim_id, statement=record.statement, as_of=record.as_of)

    card = [as_forecast(record) for record in card_records]
    pack = [as_forecast(record) for record in pack_records]
    marks = forecast_marks([*card, *pack])

    added: list[dict[str, Any]] = []
    rejected: list[str] = []
    used_total = relied_count
    for k, record in enumerate(pack):
        mark = marks.get(record.claim_id)
        if mark is None or mark.kind != "actual":
            continue
        same_series = [
            other
            for other in card
            if marks.get(other.claim_id) is not None and marks[other.claim_id].series == mark.series
        ]
        if not same_series:
            continue  # серии на карточке нет — добавлять «замещение» нечего
        if any(marks[other.claim_id].kind != "outdated" for other in same_series):
            continue  # на карточке уже актуальная или недатированная запись серии
        if used_total >= limit:
            rejected.append(f"fresher_forecast[{k}]: more than {limit} relied ids")
            continue
        used_total += 1
        added.append(
            {
                "claim_id": record.claim_id,
                "series": mark.series,
                "replaces": [other.claim_id for other in same_series],
            }
        )
    return added, rejected
