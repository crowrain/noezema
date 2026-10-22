"""Unit: снимок и полезная нагрузка следа попытки без строки `sessions` (T7.132, часть 2).

Случай стенда 22.10 (прогон `929bdcc-v29`, вопрос `38f379e7`): «wake now» принят, узел работал
17 минут, в базе — НИЧЕГО. Здесь проверяется чистая часть выхода такой попытки: что именно
оператор увидит в следе, если строка `sessions` не зафиксирована.

Контракт полезной нагрузки (его же читает `/api/v1/status`):
* вопрос попытки, класс и текст исключения, границы попытки по ЧАСАМ ПРОЦЕССА;
* число вызовов модели и инструментов — включая тот вызов, на котором попытка погибла;
* признак `unrecorded_attempt` и `session_row_recorded: false` — по ним след отличается от честного
  терминального события сессии (пункт «в»: второго следа у такой сессии быть не должно);
* секретов в тексте нет (пункт «д»), текст обрезан.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.orchestrator.failed_attempt import (
    ERROR_TEXT_MAX_CHARS,
    TRACE_MARKER,
    AttemptTrace,
    build_attempt_trace_payload,
    format_exception_redacted,
    redact_error_text,
    trace_event_type,
    trace_public_summary,
)
from packages.domain.models.enums import AuditEventType
from packages.llm_gateway.client import LLMTransientError, LLMTruncatedResponseError

pytestmark = [pytest.mark.unit]

# значение, которое ни при каких условиях не должно попасть ни в payload, ни в журнал
SECRET = "sk-abcdef0123456789abcdef0123456789"


def _trace(**overrides: Any) -> AttemptTrace:
    params: dict[str, Any] = {
        "session_id": uuid.uuid4(),
        "started_at": datetime(2026, 10, 22, 8, 37, 45, tzinfo=UTC),
        "node_owner": "node-92",
    }
    params.update(overrides)
    return AttemptTrace(**params)


def test_payload_names_the_question_the_class_and_the_boundaries() -> None:
    """Вопрос, класс исключения и границы попытки — то, чего не хватало в 08:55 22.10."""
    trace = _trace()
    question_id = uuid.uuid4()
    trace.note_question(question_id)
    started = trace.started_at
    payload = build_attempt_trace_payload(
        trace,
        error=LLMTransientError("gateway refused after retries"),
        cancelled=False,
        finished_at=started + timedelta(seconds=1020),  # ровно случай 22.10: 17 минут
    )

    assert payload["question_id"] == str(question_id)
    assert payload["error_class"] == "LLMTransientError"
    assert "gateway refused after retries" in str(payload["error"])
    assert payload["attempt_started_at"] == started.isoformat()
    assert payload["attempt_finished_at"] == (started + timedelta(seconds=1020)).isoformat()
    assert payload["attempt_duration_seconds"] == 1020.0
    assert payload["attempt_session_id"] == str(trace.session_id)
    assert payload["node_owner"] == "node-92"


def test_payload_says_the_session_row_was_not_recorded() -> None:
    """Признак «строка сессии не зафиксирована» и маркер, по которому след ищут."""
    payload = build_attempt_trace_payload(_trace(), error=RuntimeError("boom"), cancelled=False)

    assert payload[TRACE_MARKER] is True
    assert payload["session_row_recorded"] is False
    assert payload["cancelled"] is False
    # маркер — договорённость с `/api/v1/status`: имя ключа зафиксировано тестом
    assert TRACE_MARKER == "unrecorded_attempt"


def test_cancellation_is_a_cancellation_not_a_failure() -> None:
    """Отмена задачи — `SESSION_CANCELLED` (§6.7), а не отказ узла."""
    payload = build_attempt_trace_payload(
        _trace(), error=asyncio.CancelledError(), cancelled=True
    )

    assert payload["cancelled"] is True
    assert payload["error_class"] == "CancelledError"
    assert "cancelled" in trace_public_summary(payload)
    assert trace_event_type(cancelled=True) is AuditEventType.SESSION_CANCELLED
    assert trace_event_type(cancelled=False) is AuditEventType.SESSION_FAILED


def test_counts_include_the_call_the_attempt_died_on() -> None:
    """Счётчики ведутся ДО вызова: «21 вызов модели, смертельный — 22-й» — видимое число."""
    trace = _trace()
    for _ in range(21):
        trace.note_model_call()
    for _ in range(13):
        trace.note_tool_call()
    trace.note_model_call()  # этот вызов и не вернулся

    payload = build_attempt_trace_payload(trace, error=LLMTransientError("timeout"), cancelled=False)

    assert payload["model_calls"] == 22
    assert payload["tool_calls"] == 13


def test_question_absent_until_selected() -> None:
    """Пока вопрос не выбран, его в следе нет — след не выдумывает привязку."""
    payload = build_attempt_trace_payload(_trace(), error=RuntimeError("x"), cancelled=False)

    assert "question_id" not in payload


def test_error_text_is_truncated_to_the_audit_limit() -> None:
    huge = LLMTruncatedResponseError("e" * (ERROR_TEXT_MAX_CHARS * 4))

    payload = build_attempt_trace_payload(_trace(), error=huge, cancelled=False)

    assert len(str(payload["error"])) <= ERROR_TEXT_MAX_CHARS


@pytest.mark.parametrize(
    ("raw", "leaked"),
    [
        (f"Client error '401' for url: Authorization: Bearer {SECRET}", SECRET),
        (f"POST /v1/chat/completions?api_key={SECRET} failed", SECRET),
        (f"header rejected: token: {SECRET}", SECRET),
        (f"password={SECRET} in dsn", SECRET),
        (f"unauthorized: {SECRET}", SECRET),
    ],
)
def test_secrets_never_reach_the_immutable_audit_payload(raw: str, leaked: str) -> None:
    """Пункт «д»: текст обрезан и прогнан через замалчивание — ключ остаётся видно, значение нет."""
    payload = build_attempt_trace_payload(_trace(), error=RuntimeError(raw), cancelled=False)

    serialized = json.dumps(payload, ensure_ascii=False)
    assert leaked not in serialized
    assert "<скрыто>" in str(payload["error"])
    # след остаётся полезным: имя параметра и класс ошибки читаются
    assert payload["error_class"] == "RuntimeError"


def test_journal_text_is_redacted_as_well() -> None:
    """Журнал процесса печатает traceback, но не заголовок авторизации шлюза."""
    try:
        raise LLMTransientError(f"Authorization: Bearer {SECRET}")
    except LLMTransientError as exc:
        journal = format_exception_redacted(exc)

    assert SECRET not in journal
    assert "LLMTransientError" in journal
    assert "test_journal_text_is_redacted_as_well" in journal  # фреймы остаются читаемыми


def test_plain_text_survives_redaction() -> None:
    """Замалчивание не съедает обычный текст ошибки: URL и коды остаются."""
    raw = "HTTP error 'Connection reset by peer' while reading http://192.168.1.141:8080/v1 after 3 attempts"

    assert redact_error_text(raw) == raw
