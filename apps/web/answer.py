"""Карточка ответа на вопрос оператора (T7.65, ADR-0026).

Модуль только ЧИТАЕТ существующие таблицы (`questions`, `sessions`, `claims`,
`claim_assessment_heads`, `evidence`, `audit_events`, `config_snapshots`) и
переводит уже посчитанные значения в человеческие тексты через `apps.web.labels`
и `apps.web.reliability`. Оценки, пороги и состояние узла он не пересчитывает и
не назначает (ADR-0026): presentation-слой только называет.

Три правила сборки (закреплены тестами `tests/unit/test_web_answer.py`):

* шаги «как получено» строятся ТОЛЬКО из ленты событий сессии, в порядке
  `audit_events.sequence`. Хронология по `created_at` запрещена AGENTS §7: в
  долгой phase-1 транзакции у всех строк одного времени;
* шаг называется только если действие выполнилось (`action_completed` с `ok`) и
  инструмент имеет подпись в словаре: неизвестный инструмент не получает
  выдуманного названия, а проваленное или оборванное действие в короткий рассказ
  не попадает (оно честно считается в замечании о неудачных шагах);
* честные замечания включаются только когда их условие видно в данных этой
  работы: сеть закрыта — из снимка правил, внешних источников не было — из
  отсутствия `research.fetch`, подтверждений нет — из пустого набора доказательств.

Чего здесь сознательно нет: слово «изолированная среда». Исполнитель инструментов
(dev-подставка или одноразовый песочник) в этих строках не различается, поэтому
такая фраза была бы утверждением без данных (тот же разбор — STATUS T7.64 §7 п.9).
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.web import labels as ui_labels
from apps.web.knowledge import (
    EFFECTIVE_SNAPSHOT_SQL,
    assessment_view,
    effective_claim_rules,
)
from apps.web.reliability import LEVEL_VERIFIED, describe_verification
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import QuestionState, SessionState
from packages.domain.sanitization import mask_nul

#: Короткий рассказ о работе: не больше шести шагов (дизайн упрощения интерфейса).
MAX_STEPS: Final = 6
#: Сколько символов поискового запроса показываем в шаге (T7.71); длинный хвост просто отрезается.
SEARCH_QUERY_CHARS: Final = 80
#: Действия, которым разрешено добавить пояснение из записи (домен страницы или текст поиска).
NAMED_ACTIONS: Final[frozenset[str]] = frozenset({"research.fetch", "web.search"})
#: Что дисквалифицирует показ данных в человеческой строке: символ спецификации, номер задачи плана,
#: hex/uuid-подобный идентификатор, код инструмента (те же границы, что проверяются для подписей).
_STEP_UNSAFE: Final = re.compile(r"§|\bT\d+\.\d+\b|[0-9a-fA-F]{16,}|\b[a-z][a-z0-9]*_[a-z0-9_]+\b")
#: Сколько сессий одного вопроса показываем.
MAX_SESSIONS: Final = 25
#: Сколько событий ленты дочитываем у одной сессии (как в GET /api/v1/sessions/{id}).
MAX_EVENTS: Final = 500

#: Закрытые наборы ключей этого модуля: тест полноты `tests/unit/test_web_labels.py`
#: краснеет, если новый ключ появился здесь, но не получил подпись в словаре.
STEP_KEYS: Final[tuple[str, ...]] = (
    "question_selected",
    "context_prepared",
    "verification_ran",
    "curation",
    "recorded",
    "merged_actions",
    "unnamed_action",
)
RESULT_KINDS: Final[tuple[str, ...]] = ("answered", "in_progress", "waiting", "failed", "no_answer")
#: Заголовок строки подтверждения выбирается по бейджу надёжности (см. labels).
VERIFICATION_LEAD_KEYS: Final[tuple[str, ...]] = ("verified", "unconfirmed")
HONESTY_KEYS: Final[tuple[str, ...]] = (
    "no_external_sources",
    "no_external_sources_closed",
    "external_sources_used",
    "computation_only",
    "no_evidence",
    "failed_steps",
)

#: Состояния вопроса, которые означают «работа по нему уже идёт».
WORKING_QUESTION_STATES: Final[frozenset[str]] = frozenset(
    {QuestionState.SELECTED.value, QuestionState.RESEARCHING.value}
)
#: Терминальные состояния сессии, которые означают «попытка не удалась».
FAILED_SESSION_STATES: Final[frozenset[str]] = frozenset(
    {SessionState.FAILED.value, SessionState.CANCELLED.value}
)

_HEXISH: Final = re.compile(r"[0-9a-fA-F]{8,}")
_URL_HOST: Final = re.compile(r"https?://([^\s\"'<>\\]+)")
_HOST_ALLOWED: Final = re.compile(r"[^a-z0-9.\-]")


def _iso(value: object) -> str | None:
    return value.isoformat() if isinstance(value, datetime) else None


# ─── человеческие подписи без выдумывания ─────────────────────────────────


def known_label(category: str, value: object) -> str:
    """Подпись значения, ТОЛЬКО если словарь её знает.

    Запасной путь `describe` возвращает сам код: на простом экране это был бы
    snake_case-код состояния или инструмента. Здесь неизвестное значение даёт
    пустую строку, а не правдоподобную фразу.
    """
    entry = ui_labels.describe(category, value)
    return entry["label"] if entry["hint"] else ""


def _phrase(category: str, key: str) -> str:
    """Название шага/итога из словаря (для ключей этого модуля подпись обязана быть)."""
    return known_label(category, key)


# ─── шаги «как получено» ──────────────────────────────────────────────────


def _sequence_of(event: Mapping[str, Any]) -> int:
    raw = event.get("sequence")
    return raw if isinstance(raw, int) else 0


def _payload_of(event: Mapping[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return dict(payload) if isinstance(payload, Mapping) else {}


def _arguments_text(arguments: object) -> str:
    if isinstance(arguments, Mapping):
        return json.dumps(arguments, ensure_ascii=False)
    if isinstance(arguments, str):
        return arguments
    return ""


def host_of_url(arguments: object) -> str:
    """Домен из записанных аргументов действия — единственное, что мы называем.

    Имя домена берётся целиком из записи действия; символы, которых в имени хоста
    быть не должно, заменяются, hex-подобные поддомены не показываются вовсе.
    """
    match = _URL_HOST.search(_arguments_text(arguments))
    if match is None:
        return ""
    host = match.group(1).split("/")[0].split("?")[0].split("#")[0]
    host = host.split("@")[-1].strip("/").lower()[:64]
    host = re.sub("_", "-", host)
    host = _HOST_ALLOWED.sub("-", host).strip("-.")
    if not host or _HEXISH.search(host):
        return ""
    return host


def search_query_of(arguments: object) -> str:
    """Запрос поиска из записанных аргументов действия — единственное, что мы называем.

    Как и домен, берётся ровно то, что записал узел в действии (T7.71): ничего не добавляется и не
    переформулируется. Одна строка, без управляющих символов, с потолком длины. Если запись содержит
    то, чего в человеческой строке на экране быть не должно (символ спецификации, номер задачи плана,
    hex-подобный идентификатор, код инструмента), запрос не показываем вовсе — шаг остаётся просто с
    названием действия. Название действия при этом берётся из словаря подписей, а не из данных.
    """
    text = _arguments_text(arguments)
    if not text:
        return ""
    try:
        parsed = json.loads(text)
    except ValueError:
        return ""
    raw = parsed.get("query") if isinstance(parsed, Mapping) else None
    if not isinstance(raw, str):
        return ""
    query = mask_nul(raw).replace("\r", " ").replace("\t", " ")
    query = re.sub(r"\s+", " ", query).strip()[:SEARCH_QUERY_CHARS]
    if not query or _STEP_UNSAFE.search(query):
        return ""
    return query


def action_detail_of(tool: str, arguments: object) -> str:
    """Что разрешено назвать рядом с действием: адрес прочитанной страницы или текст поиска.

    Только для инструментов из `_NAMED_ACTIONS`; для остальных пояснения нет — выдумывать его нельзя.
    """
    if tool == "web.search":
        return search_query_of(arguments)
    return host_of_url(arguments)


def times_phrase(count: int) -> str:
    """«2 раза» по-русски; для одного действия суффикс не нужен."""
    if count <= 1:
        return ""
    tail = count % 10
    group = count % 100
    unit = "раз" if (12 <= group <= 14 or tail not in (2, 3, 4)) else "раза"
    return f"{count} {unit}"


def executed_actions(events: Sequence[Mapping[str, Any]]) -> tuple[list[tuple[str, int, str]], int]:
    """Выполненные действия в порядке первого появления: (инструмент, число, пояснение из записи).

    Пояснение — домен прочитанной страницы или текст поискового запроса (T7.71), и только для
    инструментов из `NAMED_ACTIONS`: его берём из записи действия, ничего не придумывая. Пустая строка
    значит «показывать нечего».

    Считаются только `action_started`, чей исход записан успешным `action_completed`
    с `ok = true`. События сортируются по `sequence`: порядок строк на входе не
    считается доказательством хронологии (AGENTS §7).

    Повтор сворачивается только по полному совпадению действия и показываемой детали
    (T7.67a): два `research.fetch` разных доменов — два шага («… : cbr.ru» и «… : expert.ru»),
    а не один шаг с первым доменом и счётчиком 2; то же для двух `web.search` с разными
    запросами. Два точных повтора одного действия сворачиваются в «— N раз» с настоящим числом.
    """
    ordered = sorted(
        (event for event in events if isinstance(event, Mapping)), key=_sequence_of
    )
    pending: dict[str, tuple[str, str]] = {}
    order: list[tuple[str, str]] = []
    counts: dict[tuple[str, str], int] = {}
    failed = 0

    for event in ordered:
        kind = event.get("type")
        payload = _payload_of(event)
        if kind == "action_started":
            tool = payload.get("tool")
            if not isinstance(tool, str):
                continue
            action_id = payload.get("action_id")
            key = action_id if isinstance(action_id, str) else f"anon-{len(pending)}"
            pending[key] = (tool, action_detail_of(tool, payload.get("arguments")))
        elif kind == "action_completed":
            action_id = payload.get("action_id")
            resolved = pending.pop(action_id, None) if isinstance(action_id, str) else None
            tool = resolved[0] if resolved is not None else payload.get("tool")
            if not isinstance(tool, str) or payload.get("ok") is not True:
                continue
            # деталь участвует в свёртке ровно когда её разрешено показывать (тот же
            # фильтр, что у названия шага): скрытая деталь не имеет права раскалывать шаг.
            detail = resolved[1] if resolved is not None and tool in NAMED_ACTIONS else ""
            group = (tool, detail)
            if group not in counts:
                order.append(group)
            counts[group] = counts.get(group, 0) + 1
        elif kind == "action_failed":
            failed += 1

    return [(tool, counts[group], detail) for tool, detail in order], failed


def build_answer_steps(events: Sequence[Mapping[str, Any]]) -> list[JsonDict]:
    """4–6 шагов работы по ленте событий одной сессии (по `sequence`).

    Шагов может быть меньше четырёх: если данных нет, шаг не додумывается.
    Больше шести не бывает — действия сворачиваются в один шаг-подсчёт.
    """
    ordered = sorted(
        (event for event in events if isinstance(event, Mapping)), key=_sequence_of
    )
    types = {str(event.get("type")) for event in ordered}
    groups, _failed = executed_actions(ordered)

    structural = [
        _phrase("answer_step", "question_selected") if "question_selected" in types else "",
        _phrase("answer_step", "context_prepared") if "context_packed" in types else "",
    ]
    action_texts: list[str] = []
    for tool, count, detail in groups:
        label = known_label("action_tool", tool)
        if not label:
            label = _phrase("answer_step", "unnamed_action")
        if tool in NAMED_ACTIONS and detail:
            # T7.65/T7.71: к действию добавляется ровно то, что записано в самом действии
            # (адрес страницы или текст поиска), а не пересказ его результата.
            label = f"{label}: {detail}"
        repeat = times_phrase(count)
        action_texts.append(f"{label} — {repeat}" if repeat else label)

    tail = [
        _phrase("answer_step", "verification_ran") if "verification_completed" in types else "",
        _phrase("answer_step", "curation") if "claim_created" in types else "",
        (
            _phrase("answer_step", "recorded")
            if {"session_committed", "commit_attempt_committed"} & types
            else ""
        ),
    ]

    texts = [text for text in (*structural, *action_texts, *tail) if text]
    if len(texts) > MAX_STEPS:
        total = sum(count for _tool, count, _domain in groups)
        merged = f"{_phrase('answer_step', 'merged_actions')}: {total}"
        texts = [text for text in (*structural, merged, *tail) if text]
    return [{"n": index + 1, "text": text} for index, text in enumerate(texts)]


# ─── итог по вопросу ──────────────────────────────────────────────────────


def is_terminal_session(state: object) -> bool:
    """Терминальность состояния сессии — по перечислению домена, не по своему списку."""
    try:
        return SessionState(str(state)).is_terminal
    except ValueError:
        # неизвестное состояние: честный ответ «не терминальное» (его покажут подписи)
        return False


def build_answer_result(
    *,
    question_state: object,
    sessions: Sequence[Mapping[str, Any]],
    answer_claim_count: int,
) -> JsonDict:
    """Человеческий итог: {kind, label, hint, action, active}.

    `active` — готовый булев признак «страницу надо обновлять»: JS не сравнивает
    коды состояний (словарь кодов в браузере повторять нельзя), а читает флаг.
    """
    state = str(question_state)
    live = [session for session in sessions if not is_terminal_session(session.get("state"))]
    if answer_claim_count > 0:
        kind = "answered"
    elif live or state in WORKING_QUESTION_STATES:
        kind = "in_progress"
    elif sessions and str(sessions[0].get("state")) in FAILED_SESSION_STATES:
        kind = "failed"
    elif sessions:
        kind = "no_answer"
    else:
        kind = "waiting"

    entry = ui_labels.describe("answer_result", kind)
    hints = [entry["hint"]]
    if sessions:
        newest = sessions[0]
        parts = [
            known_label("session_state", newest.get("state")),
            known_label("termination_reason", newest.get("termination_reason")),
        ]
        named = [part for part in parts if part]
        if named:
            hints.append("Последняя сессия: " + " · ".join(named) + ".")
    return {
        "kind": kind,
        "label": entry["label"],
        "hint": " ".join(part for part in hints if part),
        "action": entry["action"],
        "active": kind == "in_progress",
    }


# ─── честные замечания ────────────────────────────────────────────────────


def evidence_kind_counts(rows: Iterable[Mapping[str, Any]]) -> dict[str, int]:
    """Сколько каких свидетельств подкрепляет ответ (учитываются только supports)."""
    counts: dict[str, int] = {}
    for row in rows:
        if not isinstance(row, Mapping) or row.get("relation") != "supports":
            continue
        kind = row.get("evidence_kind")
        if isinstance(kind, str):
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def build_honesty_notes(
    *,
    saw_research_fetch: bool,
    network_mode: object,
    evidence_kinds: Mapping[str, int],
    answer_claim_count: int,
    failed_actions: int,
) -> list[str]:
    """Замечания только про то, что действительно видно в данных этой работы."""
    kinds = {kind: count for kind, count in evidence_kinds.items() if count}
    notes: list[str] = []

    if not saw_research_fetch:
        if network_mode == "none":
            notes.append(_phrase("honesty_note", "no_external_sources_closed"))
        else:
            notes.append(_phrase("honesty_note", "no_external_sources"))
    else:
        notes.append(_phrase("honesty_note", "external_sources_used"))

    if answer_claim_count > 0 and not kinds:
        notes.append(_phrase("honesty_note", "no_evidence"))
    elif answer_claim_count > 0 and set(kinds) == {"computation"}:
        notes.append(_phrase("honesty_note", "computation_only"))

    if failed_actions > 0:
        notes.append(f"{_phrase('honesty_note', 'failed_steps')}: {failed_actions}")
    return [note for note in notes if note]


# ─── запрос целиком (только чтение) ────────────────────────────────────────


def _in_params(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """`IN (:s0, :s1, …)` с привязанными параметрами.

    Динамический `IN` нужен потому, что asyncpg не выводит тип из None-параметра
    (ловушка AGENTS §7): условие присутствует в SQL только когда значения есть.
    """
    names = [f"{prefix}{index}" for index in range(len(values))]
    clause = ", ".join(f":{name}" for name in names)
    params: dict[str, Any] = dict(zip(names, values, strict=True))
    return clause, params


async def question_answer(db: AsyncSession, question_id: uuid.UUID) -> JsonDict | None:
    """Собрать карточку ответа на вопрос. `None` — вопроса нет (эндпоинт ответит 404).

    Неполные данные не роняют эндпоинт: отсутствующий блок отдаётся пустым
    списком, отсутствующая подпись — пустой строкой.
    """
    question = (
        (
            await db.execute(
                text("SELECT id, text, origin, state, priority, created_at FROM questions WHERE id = :id"),
                {"id": question_id},
            )
        )
        .mappings()
        .first()
    )
    if question is None:
        return None

    # T7.67a: сквозной номер вопроса — тот же порядок, что у очереди
    # (`created_at ASC, id ASC` по всей таблице), посчитанный тем же оконным
    # выражением: карточка и список не могут разойтись в нумерации.
    number = (
        await db.execute(
            text(
                "SELECT number FROM ("
                "  SELECT id, row_number() OVER (ORDER BY created_at ASC, id ASC) AS number"
                "  FROM questions"
                ") numbered WHERE id = :id"
            ),
            {"id": question_id},
        )
    ).scalar_one_or_none()

    sessions: list[Any] = list(
        (
            await db.execute(
                text(
                    "SELECT id, state, termination_reason, started_at, created_at, config_snapshot_id "
                    "FROM sessions WHERE question_id = :q ORDER BY created_at DESC, id DESC LIMIT :limit"
                ),
                {"q": question_id, "limit": MAX_SESSIONS},
            )
        )
        .mappings()
        .all()
    )
    session_ids = [str(row["id"]) for row in sessions]

    claims: list[JsonDict] = []
    other_claims: list[JsonDict] = []
    evidence_rows: list[Any] = []
    if session_ids:
        clause, params = _in_params("s", session_ids)
        claim_rows: list[Any] = list(
            (
                await db.execute(
                    text(
                        f"""
                        SELECT c.id, c.statement, c.claim_type, c.freshness_status,
                               c.created_in_session, c.created_at,
                               COALESCE(h.assessment_state, 'none') AS head_state,
                               h.epistemic_status, a.effective_grade, a.confidence
                        FROM claims c
                        LEFT JOIN claim_assessment_heads h
                               ON h.claim_id = c.id
                              AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
                        LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                        WHERE c.created_in_session IN ({clause})
                        ORDER BY c.created_at ASC, c.id
                        """
                    ),
                    params,
                )
            )
            .mappings()
            .all()
        )
        rules = await effective_claim_rules(db)
        claim_ids = [str(row["id"]) for row in claim_rows]
        by_claim: dict[str, list[Mapping[str, Any]]] = {}
        if claim_ids:
            evidence_clause, evidence_params = _in_params("c", claim_ids)
            evidence_rows = list(
                (
                    await db.execute(
                        text(
                            f"""
                            SELECT e.claim_id, e.relation, e.evidence_kind, e.scope,
                                   e.source_id, e.chunk_id, e.observation_artifact_id,
                                   s.source_type, s.canonical_uri, ar.sha256 AS artifact_sha256
                            FROM evidence e
                            LEFT JOIN sources s ON s.id = e.source_id
                            LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id
                            WHERE e.claim_id IN ({evidence_clause})
                            ORDER BY e.created_at, e.id
                            """
                        ),
                        evidence_params,
                    )
                )
                .mappings()
                .all()
            )
            for row in evidence_rows:
                by_claim.setdefault(str(row.get("claim_id")), []).append(row)

        for row in claim_rows:
            claim_id = str(row["id"])
            head_state = str(row["head_state"])
            item: JsonDict = {
                "id": claim_id,
                "statement": row["statement"],
                "claim_type": row["claim_type"],
                "head_state": head_state,
                "created_in_session": (
                    str(row["created_in_session"]) if row["created_in_session"] else None
                ),
                "created_at": _iso(row["created_at"]),
            }
            item.update(
                assessment_view(
                    claim_type=row["claim_type"],
                    head_state=head_state,
                    epistemic_status=row["epistemic_status"],
                    effective_grade=row["effective_grade"],
                    freshness_status=row["freshness_status"],
                    rules=rules,
                )
            )
            item["verification"] = describe_verification(by_claim.get(claim_id, []))
            # Заголовок строки подтверждения выбирается по уже вычисленному бейджу:
            # «как проверено» звучит только там, где оценка действительно есть.
            badge = item.get("reliability")
            level = badge.get("level") if isinstance(badge, Mapping) else None
            lead_key = "verified" if level == LEVEL_VERIFIED else "unconfirmed"
            item["verification_lead"] = ui_labels.describe("verification_lead", lead_key)["label"]
            item["active"] = head_state == "current"
            if head_state == "current":
                claims.append(item)
            else:
                other_claims.append(item)

    # шаги рассказывают про ТУ работу, которая дала ответ; её нет — про последнюю сессию
    work_session_id: str | None = None
    for item in claims:
        created = item["created_in_session"]
        if isinstance(created, str) and created:
            work_session_id = created
            break
    if work_session_id is None and session_ids:
        work_session_id = session_ids[0]

    events: list[Any] = []
    network_mode: object = None
    if work_session_id is not None:
        events = list(
            (
                await db.execute(
                    text(
                        "SELECT sequence, type, payload FROM audit_events "
                        "WHERE session_id = :s ORDER BY sequence ASC LIMIT :limit"
                    ),
                    {"s": work_session_id, "limit": MAX_EVENTS},
                )
            )
            .mappings()
            .all()
        )
        network_mode = (
            await db.execute(
                text(
                    "SELECT cs.policy -> 'capabilities' ->> 'network' AS network "
                    "FROM sessions s JOIN config_snapshots cs ON cs.id = s.config_snapshot_id "
                    "WHERE s.id = :s"
                ),
                {"s": work_session_id},
            )
        ).scalar_one_or_none()

    groups, failed_actions = executed_actions(events)
    answer_ids = {str(item["id"]) for item in claims}
    steps = build_answer_steps(events)
    result = build_answer_result(
        question_state=question["state"],
        sessions=[dict(row) for row in sessions],
        answer_claim_count=len(claims),
    )
    honesty = build_honesty_notes(
        saw_research_fetch=any(tool == "research.fetch" for tool, _count, _domain in groups),
        network_mode=network_mode,
        evidence_kinds=evidence_kind_counts(
            row for row in evidence_rows if str(row.get("claim_id")) in answer_ids
        ),
        answer_claim_count=len(claims),
        failed_actions=failed_actions,
    )

    work_row = next((row for row in sessions if str(row["id"]) == (work_session_id or "")), None)
    return {
        "question": {
            "id": str(question["id"]),
            "text": question["text"],
            "state": question["state"],
            "state_label": known_label("question_state", question["state"]),
            "state_hint": ui_labels.describe("question_state", question["state"])["hint"],
            "origin": question["origin"],
            "origin_label": known_label("question_origin", question["origin"]),
            "priority": int(question["priority"]),
            "created_at": _iso(question["created_at"]),
            # T7.67a: № по всей таблице вопросов (№1 — первый вопрос узла).
            "number": int(number) if number is not None else None,
        },
        "sessions": [
            {
                "id": str(row["id"]),
                "state": row["state"],
                "state_label": known_label("session_state", row["state"]),
                "stage": ui_labels.session_stage(row["state"]),
                "termination_reason": row["termination_reason"],
                "termination_reason_label": known_label(
                    "termination_reason", row["termination_reason"]
                ),
                "started_at": _iso(row["started_at"]) or _iso(row["created_at"]),
            }
            for row in sessions
        ],
        "result": result,
        "claims": claims,
        "other_claims": other_claims,
        "steps": steps,
        "honesty": honesty,
        "work": {
            "session_id": work_session_id,
            "state_label": known_label("session_state", work_row["state"]) if work_row else "",
            "started_at": (_iso(work_row["started_at"]) or _iso(work_row["created_at"])) if work_row else None,
        },
    }
