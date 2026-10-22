"""След попытки сессии, которая не оставила строки `sessions` (T7.132, §5.2.2, §6.7).

Случай стенда 22.10 2026 (прогон `929bdcc-v29`, вопрос `38f379e7`): «wake now» в 08:37:45 принят,
`node_state=session_running`, прокси читает и ищет с 08:38 до 08:55 (события `research_*` с
`session_id NULL`), а в базе от попытки НЕ осталось ничего — ни строки `sessions`, ни `actions`,
ни `model_runs`, ни `session_failed`. Причина механическая: фаза 1 держит строку сессии, её события
и операционные строки НЕЗАФИКСИРОВАННЫМИ до COMMITTING (T7.20, §5.2.2), поэтому любое исключение,
вылетевшее из фазы 1, уносит и их, и событие `SESSION_FAILED`, записанное тем же вызовом
(`orchestrator.py`: ветка `except LLMError` цикла исследователя пишет аудит внутри той же обречённой
транзакции). Исключение задачи осело только в памяти веб-процесса (`node.last_error`) и в журнал не
попало: оператор видел честный возврат узла в `idle` и ни одной записи о попытке.

Здесь — чистая (не обращающаяся к базе) часть такого выхода: снимок попытки и его полезная нагрузка.
Сама запись следа — отдельная КОРОТКАЯ транзакция в `orchestrator.py`: она не может откатиться
вместе с фазой 1, потому что не является её транзакцией.

Тип события — существующий: `SESSION_FAILED` для исключения и `SESSION_CANCELLED` для отмены
(отмена — не отказ узла, а прерванная попытка: §6.7 «abort отбрасывает staging и завершает
cancelled»). Нового `AuditEventType` нет. Отличить след от честного терминального события сессии
можно ключом `unrecorded_attempt`: у честных событий его нет.

Секреты: текст исключения попадает в неизменяемый журнал, поэтому он обрезается и прогоняется через
закрытый список замалчивания (заголовки авторизации, именованные значения `api_key`/`token`/
`secret`/`password`, ключи вида `sk-...`). NUL-маскировка границы аудита (T7.46a) применяется самой
`AuditService`.
"""

from __future__ import annotations

import re
import traceback
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import AuditEventType

#: Ключ, по которому след попытки отличается от честного терминального события сессии.
TRACE_MARKER = "unrecorded_attempt"

#: Потолок текста исключения в payload — тот же порядок, что у `{str(exc)[:500]}` в оркестраторе.
ERROR_TEXT_MAX_CHARS = 500

#: Чем заменяется значение секрета; сам ключ остаётся видимым («api_key=…»), чтобы запись читалась.
REDACTED_PLACEHOLDER = "<скрыто>"

#: Закрытый список узоров секретов в тексте исключения.
_SECRET_PATTERNS: tuple[re.Pattern[str], ...] = (
    # заголовок или параметр авторизации: "Bearer <token>", "token: <value>", "Basic <credentials>"
    re.compile(r"(?i)\b(bearer|basic|token)([=:\s]+)([^\s,;\"']+)"),
    # именованные секреты: api_key=..., access_token: ..., password=..., secret=...
    re.compile(
        r"(?i)\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|password|passwd|secret)"
        r"([=:\s]+)([^\s,;\"']+)"
    ),
    # ключи провайдеров
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}"),
)


def redact_error_text(text: str, *, limit: int = ERROR_TEXT_MAX_CHARS) -> str:
    """Обрезка и замалчивание текста исключения перед записью в неизменяемый журнал.

    Имя параметра и разделитель сохраняются, значение заменяется меткой: оператор видит, что текст
    был и где именно стоял секрет, а сам секрет в журнал не попадает.
    """
    cleaned = " ".join(text.split())

    def _replace(match: re.Match[str]) -> str:
        if match.groups():
            return f"{match.group(1)}{match.group(2)}{REDACTED_PLACEHOLDER}"
        return REDACTED_PLACEHOLDER

    for pattern in _SECRET_PATTERNS:
        cleaned = pattern.sub(_replace, cleaned)
    return cleaned[:limit]


#: Потолок текста в ЖУРНАЛЕ процесса: там нужен и traceback, а не только первая строка.
JOURNAL_TEXT_MAX_CHARS = 4000


def format_exception_redacted(exc: BaseException, *, limit: int = JOURNAL_TEXT_MAX_CHARS) -> str:
    """Traceback исключения для журнала — с ЗАМАЛЧИВАНИЕМ исходного текста.

    `logger.error(..., exc_info=exc)` печатает последнюю строку `module.Class: <текст>` как есть, а
    в тексте исключения живут заголовки авторизации шлюза. Журнал — тоже долговечный след, поэтому
    он печатает ту же вычищенную строку, что и payload (и остаётся читаемым: фреймы, файлы, номера
    строк не трогаются).
    """
    return redact_error_text(
        "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)), limit=limit
    )


@dataclass
class AttemptTrace:
    """Снимок попытки, который ведётся ПРЯМО ФАЗЫ 1 и переживает её откат.

    Живёт в памяти процесса, поэтому не зависит от судьбы транзакции фазы 1. Счётчики увеличиваются
    ДО вызова: погибший вызов тоже должен быть виден («21 вызов модели, смертельный — 22-й»).
    Времена — ЧАСЫ ПРОЦЕССА (`datetime.now(UTC)`), а не `now()` базы: у всех записей одной фазы 1
    `created_at` равен старту транзакции (AGENTS §7), строить по ним хронологию нельзя.
    """

    session_id: UUID
    started_at: datetime
    node_owner: str = ""
    question_id: UUID | None = None
    model_calls: int = 0
    tool_calls: int = 0

    def note_question(self, question_id: UUID) -> None:
        self.question_id = question_id

    def note_model_call(self) -> None:
        self.model_calls += 1

    def note_tool_call(self) -> None:
        self.tool_calls += 1

    @staticmethod
    def now() -> datetime:
        return datetime.now(UTC)


def trace_event_type(*, cancelled: bool) -> AuditEventType:
    """Отменённая попытка — `SESSION_CANCELLED`, сорванная исключением — `SESSION_FAILED` (§6.7)."""
    return AuditEventType.SESSION_CANCELLED if cancelled else AuditEventType.SESSION_FAILED


def build_attempt_trace_payload(
    trace: AttemptTrace,
    *,
    error: BaseException | None,
    cancelled: bool,
    finished_at: datetime | None = None,
) -> JsonDict:
    """Полезная нагрузка следа: вопрос, класс и текст исключения, границы попытки по часам процесса,
    число вызовов модели и инструментов, признак «строка сессии не зафиксирована».

    Отмена важнее исключения: если передано и то и другое, класс пишется `CancelledError` (текст
    исключения-следствия при этом остаётся, если он не пуст).
    """
    end = finished_at or AttemptTrace.now()
    error_class = "CancelledError" if cancelled else (type(error).__name__ if error is not None else "Unknown")
    error_text = "" if error is None else redact_error_text(str(error))
    duration = (end - trace.started_at).total_seconds()
    payload: JsonDict = {
        TRACE_MARKER: True,
        # Строка `sessions` не зафиксирована: фаза 1 откатилась вместе с ней и со своим аудитом.
        "session_row_recorded": False,
        "attempt_session_id": str(trace.session_id),
        "cancelled": bool(cancelled),
        "error_class": error_class,
        "error": error_text,
        "attempt_started_at": trace.started_at.isoformat(),
        "attempt_finished_at": end.isoformat(),
        "attempt_duration_seconds": round(duration, 3),
        "model_calls": trace.model_calls,
        "tool_calls": trace.tool_calls,
    }
    if trace.node_owner:
        payload["node_owner"] = trace.node_owner
    if trace.question_id is not None:
        payload["question_id"] = str(trace.question_id)
    return payload


def trace_public_summary(payload: JsonDict) -> str:
    """Одна строка для журнала: что видно оператору без разбора payload."""
    kind = str(payload.get("error_class") or "error")
    verb = "cancelled" if payload.get("cancelled") else "failed"
    return f"session attempt {kind} without a session row ({verb})"
