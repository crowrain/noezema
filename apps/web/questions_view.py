"""Нумерация, порядок выдачи и краткий итог ответа для `GET /api/v1/questions` (T7.67a).

Модуль только ЧИТАЕТ существующие таблицы (`questions`, `sessions`, `claims`,
`claim_assessment_heads`, `claim_assessments`) и подписывает уже посчитанные
значения через `apps.web.labels` / `apps.web.reliability` (ADR-0026): оценки и
пороги никто не пересчитывает. Миграций нет: номер считается оконной функцией
по всей таблице.

Три правила закреплены тестами `tests/unit/test_web_questions_view.py` и
`tests/scenario/test_web_questions_view_api.py`:

* номер вопроса — `row_number() OVER (ORDER BY created_at ASC, id ASC)` ПО ВСЕЙ
  таблице вопросов, а не по странице выдачи: №1 — самый первый вопрос узла.
  `created_at` строки не меняется никогда, состояние вопроса меняется часто —
  состояние в нумерации не участвует, поэтому номер постоянен; новый вопрос
  только добавляется в конец нумерации. Совпадающие `created_at` (строки одной
  транзакции, AGENTS §7) ранжируются по id — детерминированный общий порядок;
* `position` остаётся место в FIFO-очередине кандидатов (`priority DESC,
  created_at ASC, id ASC`) даже когда выдача отсортирована по времени: порядок
  обработки ≠ порядок показа;
* итог ответа строится теми же построителями, что и карточка
  (`apps.web.answer.build_answer_result`, `apps.web.knowledge.assessment_view`),
  и показывает только действующее утверждение (голова `current` на действующем
  снимке правил); pending/invalid никогда не подписываются как ответ.

Стоимость страницы фиксирована и не зависит от числа вопросов: один запрос за
вопросами (окна считаются до `LIMIT`), один за сессии, один за действующие
утверждения, один за пороги типов из снимка — итого не больше четырёх; N+1
запрещён и проверяется счётчиком запросов в тесте.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.web.answer import (
    ANSWER_KINDS,
    build_answer_result,
    known_label,
    touched_claims_cte,
)
from apps.web.knowledge import EFFECTIVE_SNAPSHOT_SQL, assessment_view, effective_claim_rules
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionOrigin, QuestionState
from packages.domain.sanitization import mask_nul

#: Потолок длины главного утверждения в колонке «Ответ» (с многоточием включительно).
SUMMARY_STATEMENT_CHARS: Final = 160

#: Разрешённые порядки выдачи. `queue` — прежний порядок очереди (по умолчанию,
#: его consuming'ы не тронуты); `recent` — новые вопросы сверху.
QUEUE_ORDER: Final = "queue"
RECENT_ORDER: Final = "recent"
ORDERS: Final[frozenset[str]] = frozenset({QUEUE_ORDER, RECENT_ORDER})

_CANDIDATE = QuestionState.CANDIDATE.value

#: Общая часть: окна считаются по всей таблице до внешнего ORDER BY/LIMIT.
_WINDOWED_CORE = """
    SELECT id, text, origin, state, priority, created_at, number, queue_rank
    FROM (
        SELECT q.id, q.text, q.origin, q.state, q.priority, q.created_at,
               row_number() OVER (ORDER BY q.created_at ASC, q.id ASC) AS number,
               row_number() OVER (ORDER BY (q.state = :cand) DESC,
                                  q.priority DESC, q.created_at ASC, q.id ASC) AS queue_rank
        FROM questions q
    ) numbered
"""

_SQL_QUEUE = text(
    _WINDOWED_CORE
    + "    ORDER BY (state = :cand) DESC, priority DESC, created_at ASC, id ASC\n"
    + "    LIMIT :limit"
)
_SQL_RECENT = text(
    _WINDOWED_CORE + "    ORDER BY created_at DESC, id DESC\n    LIMIT :limit"
)


def summary_statement(value: object, cap: int = SUMMARY_STATEMENT_CHARS) -> str:
    """Одна короткая человеческая строка из утверждения: одна строка текста, потолок длины.

    Переносы сворачиваются в пробелы (табличная ячейка), NUL маскируется тем же
    средством, что и шаги карточки; обрезка заканчивается многоточем внутри потолка.
    """
    if value is None:
        return ""
    collapsed = " ".join(str(mask_nul(str(value))).split())
    if not collapsed:
        return ""
    if len(collapsed) <= cap:
        return collapsed
    return collapsed[: cap - 1].rstrip() + "…"


def build_answer_summary(
    *,
    question_state: object,
    sessions: Sequence[Mapping[str, Any]],
    claims: Sequence[Mapping[str, Any]],
    rules: Mapping[str, Any],
) -> JsonDict:
    """Краткий итог ответа одним построителем с карточкой (чистая функция).

    `sessions` — сессии вопроса свежие первыми; `claims` — утверждения вопроса с
    головой `current` на действующем снимке правил, в том же порядке, что список
    `claims` карточки (`created_at ASC, id`). Пустой `claims` означает «действующего
    утверждения нет»: бейджа и утверждения в итоге не будет никогда — ни для
    pending, ни для invalid head (они отсекаются запросом).
    """
    result = build_answer_result(
        question_state=question_state,
        sessions=sessions,
        answer_claim_count=len(claims),
        # T7.74: перепроверенное или принятое повторно этим вопросом действующее
        # утверждение — тоже ответ вопроса («Ответ не записан» был бы неправдой).
        relations=[claim.get("relation") for claim in claims],
    )
    summary: JsonDict = {"kind": result["kind"], "label": result["label"]}
    if claims and str(result["kind"]) in ANSWER_KINDS:
        first = claims[0]
        summary["statement"] = summary_statement(first.get("statement"))
        summary["reliability"] = assessment_view(
            claim_type=first.get("claim_type"),
            head_state="current",
            epistemic_status=first.get("epistemic_status"),
            effective_grade=first.get("effective_grade"),
            freshness_status=first.get("freshness_status"),
            rules=rules,
        )["reliability"]
        # T7.74: та же связь, что показывает карточка (подпись — из словаря).
        summary["relation"] = first.get("relation")
        summary["relation_label"] = known_label("claim_relation", first.get("relation"))
    else:
        summary["statement"] = None
        summary["reliability"] = None
        summary["relation"] = None
        summary["relation_label"] = ""
    return summary


def _session_json(row: Mapping[str, Any]) -> JsonDict:
    """Прежняя форма ссылки на сессию в строке очереди: {id, state}."""
    return {"id": str(row["id"]), "state": row["state"]}


async def list_question_rows(
    db: AsyncSession,
    *,
    limit: int,
    order: str = QUEUE_ORDER,
) -> list[JsonDict]:
    """Строки `GET /api/v1/questions` со сквозным номером и кратким итогом ответа.

    Порядок по умолчанию (`queue`) совпадает с прежней выдачей очереди: сначала
    кандидаты в порядке FIFO-селектора с их позицией, затем разобранные вопросы;
    добавлены только новые поля `number`, `answer`, `created_by_operator`,
    `queue_place`. `order=recent` меняет только порядок строк выдачи (новые
    первыми), номер и позиция считаются так же по всей таблице.
    """
    if order not in ORDERS:
        raise ValueError(f"unknown questions order: {order}")

    sql = _SQL_QUEUE if order == QUEUE_ORDER else _SQL_RECENT
    raw_rows = list(
        (await db.execute(sql, {"cand": _CANDIDATE, "limit": limit})).mappings().all()
    )
    if not raw_rows:
        return []

    ids = tuple(str(row["id"]) for row in raw_rows)

    # один запрос на сессии всех вопросов страницы (свежие первыми — тот же
    # порядок, что у карточки), и один запрос на действующие утверждения.
    session_clause = ", ".join(f":s{i}" for i in range(len(ids)))
    session_params: dict[str, Any] = {f"s{i}": ids[i] for i in range(len(ids))}
    session_rows = list(
        (
            await db.execute(
                text(
                    "SELECT question_id, id, state, termination_reason FROM sessions "
                    f"WHERE question_id IN ({session_clause}) "
                    "ORDER BY created_at DESC, id DESC"
                ),
                session_params,
            )
        )
        .mappings()
        .all()
    )
    per_question_sessions: dict[str, list[Mapping[str, Any]]] = {}
    for row in session_rows:
        if row["question_id"] is not None:
            # RowMapping -> plain dict: построители работают только с Mapping
            per_question_sessions.setdefault(str(row["question_id"]), []).append(dict(row))

    claim_clause = ", ".join(f":c{i}" for i in range(len(ids)))
    claim_params: dict[str, Any] = {f"c{i}": ids[i] for i in range(len(ids))}
    # T7.74: то же отношение «вопрос → утверждение», что у карточки (та же CTE):
    # перепроверенное этим вопросом действующее утверждение — ответ вопроса и в списке.
    scope = f"SELECT id AS session_id, question_id FROM sessions WHERE question_id IN ({claim_clause})"
    claim_rows = list(
        (
            await db.execute(
                text(
                    f"""
                    WITH {touched_claims_cte(scope)}
                    SELECT r.question_id, r.relation, c.id, c.statement, c.claim_type,
                           c.freshness_status, c.created_at,
                           h.epistemic_status, a.effective_grade
                    FROM ranked r
                    JOIN claims c ON c.id = r.claim_id
                    JOIN claim_assessment_heads h
                           ON h.claim_id = c.id
                          AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
                          AND h.assessment_state = 'current'
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    ORDER BY r.question_id ASC, c.created_at ASC, c.id ASC
                    """,
                ),
                claim_params,
            )
        )
        .mappings()
        .all()
    )
    per_question_claims: dict[str, list[Mapping[str, Any]]] = {}
    for row in claim_rows:
        per_question_claims.setdefault(str(row["question_id"]), []).append(dict(row))

    rules = await effective_claim_rules(db)

    rows: list[JsonDict] = []
    for raw in raw_rows:
        question_id = str(raw["id"])
        state = str(raw["state"])
        sessions = per_question_sessions.get(question_id, [])
        claims = per_question_claims.get(question_id, [])
        position: int | None = int(raw["queue_rank"]) if state == _CANDIDATE else None

        summary = build_answer_summary(
            question_state=state,
            sessions=sessions,
            claims=claims,
            rules=rules,
        )
        queue_place = position if str(summary["kind"]) == "waiting" else None

        rows.append(
            {
                "id": question_id,
                "text": raw["text"],
                "origin": raw["origin"],
                "state": state,
                "priority": int(raw["priority"]),
                "created_at": (
                    raw["created_at"].isoformat() if raw["created_at"] is not None else None
                ),
                "position": position,
                "session": _session_json(sessions[0]) if sessions else None,
                # T7.67a аддитивные поля:
                "number": int(raw["number"]),
                "queue_place": queue_place,
                "created_by_operator": str(raw["origin"]) == QuestionOrigin.MESSAGE.value,
                "answer": summary,
            }
        )
    return rows
