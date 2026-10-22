"""Scenario: долговечный след попытки сессии, которая не оставила строки `sessions` (T7.132).

Случай стенда 22.10 (`.92`, прогон `929bdcc-v29`, вопрос `38f379e7`): «wake now» 08:37:45 принят
(202), `node_state=session_running`, прокси читает и ищет с 08:38:01 по 08:55:05, а в базе от
попытки НЕ осталось ничего — ни `sessions`, ни `actions`, ни `model_runs`, ни `session_failed`;
в журнале веб-процесса — только HTTP-строки. Причина: фаза 1 держит всё НАПИТОК откатом до
COMMITTING (§5.2.2), и исключение шлюза уносит и строку сессии, и её же `SESSION_FAILED`, и
хронологию вопроса; в памяти процесса осталось только `node.last_error`.

Здесь идёт РЕАЛЬНЫЙ код: оркестратор, база, шлюз поверх подставного сервера ответов FakeLLM (триггер фазовой ошибки —
уровень шлюза LLM, разрешённый задачей). Проверяется, что выход попытки без строки сессии
оставляет след в ОТДЕЛЬНОЙ короткой транзакции (пункт «а»), отмена — `SESSION_CANCELLED`
(пункт «б»), честная неудачная сессия не получает второго следа (пункт «в»), а секретов в следе
нет (пункт «д»).
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from contextlib import suppress
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.failed_attempt import TRACE_MARKER, AttemptTrace
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.uow import transaction
from packages.llm_gateway.client import LLMMiddleware, LLMTransientError
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_orchestrator import TOOL_PYTHON, _scalar, _seed_question

pytestmark = [pytest.mark.scenario]

# синтетическое значение вместо настоящей учётной записи: проверка, что оно не доезжает до журнала
FAKE_CREDENTIAL = "fakeCr" + "edential-never-in-audit"

_EVENT_COLUMNS = "type, session_id, actor, public_summary, payload"


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).mappings().all())
    finally:
        await engine.dispose()


async def _attempt_traces(scratch_url: str) -> list[Any]:
    """Все следы попытки в базе (отличаются от честных терминальных событий маркером)."""
    return await _rows(
        scratch_url,
        f"SELECT {_EVENT_COLUMNS} FROM audit_events "
        f"WHERE type IN ('session_failed', 'session_cancelled') AND payload ->> '{TRACE_MARKER}' = 'true' "
        "ORDER BY occurred_at ASC",
    )


async def _terminal_events(scratch_url: str) -> list[Any]:
    return await _rows(
        scratch_url,
        f"SELECT {_EVENT_COLUMNS} FROM audit_events "
        "WHERE type IN ('session_failed', 'session_cancelled', 'session_committed') "
        "ORDER BY occurred_at ASC",
    )


def _payload(row: Any) -> dict[str, Any]:
    raw = row["payload"]
    return json.loads(raw) if isinstance(raw, str) else dict(raw)


def _make_orchestrator(
    scratch_url: str, fake: FakeLLM, workspace: Path, executor: StubToolExecutor | None = None
) -> tuple[Orchestrator, LLMMiddleware, Any]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=executor if executor is not None else StubToolExecutor(workspace),
    )
    return orch, gateway, engine


class _GateExecutor(StubToolExecutor):
    """Держит сессию ВНУТРИ фазы 1 на шаге инструмента, пока тест её не отменит (тот же event loop)."""

    def __init__(self, workspace_dir: Path) -> None:
        super().__init__(workspace_dir)
        self.reached_gate = asyncio.Event()
        self.release = asyncio.Event()

    async def execute(self, tool, arguments, *, db=None):  # type: ignore[no-untyped-def]
        if tool == "python.execute":
            self.reached_gate.set()
            await self.release.wait()
        return await super().execute(tool, arguments, db=db)


@pytest.mark.asyncio
async def test_gateway_refusal_inside_phase_one_leaves_a_durable_trace(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Пункт «а»: отказ шлюза внутри фазы 1 → след в базе, строки сессии нет, вопрос candidate.

    Сценарий повторяет 22.10 буквально: единственный вызов модели ОТКАЗЫВАЕТ (подставный сервер не отдаёт ни
    одного ответа → HTTP 500 → retries → `LLMTransientError`). Ветка `except LLMError` цикла
    исследователя пишет `SESSION_FAILED` ВНУТРИ той же обречённой транзакции — он откатывается
    вместе с ней; ровно поэтому до T7.132 в базе не оставалось ничего.
    """
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)
    fake_llm.script([])  # ни одного ответа: первая же фаза получает транспортный отказ

    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        with pytest.raises(LLMTransientError):
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    traces = await _attempt_traces(scratch_url)
    assert len(traces) == 1, f"ожидался ровно один след попытки, найдено {len(traces)}"
    trace = traces[0]
    assert trace["type"] == "session_failed"
    payload = _payload(trace)
    assert payload["question_id"] == str(question_id)
    assert payload["error_class"] == "LLMTransientError"
    assert payload["cancelled"] is False
    assert payload["session_row_recorded"] is False
    assert payload["model_calls"] == 1  # вызов, на котором попытка погибла, учтён
    assert payload["tool_calls"] == 0
    assert payload["attempt_started_at"] <= payload["attempt_finished_at"]
    # след — вне сессии: строки сессии нет, поэтому и внешнего ключа нет (session_id остаётся NULL)
    assert trace["session_id"] is None
    assert "without a session row" in str(trace["public_summary"])

    # того, что унесла фаза 1, в базе нет
    assert await _scalar(scratch_url, "SELECT id FROM sessions") is None
    assert await _scalar(scratch_url, "SELECT id FROM actions") is None
    assert await _scalar(scratch_url, "SELECT id FROM model_runs") is None
    # допуск снят сразу (T7.20), а не живёт до истечения аренды
    assert await _scalar(scratch_url, "SELECT session_id FROM session_admissions") is None
    # вопрос НЕ угнан в researching: он по-прежнему ждёт следующую попытку
    question_state = await _scalar(
        scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(question_id)}
    )
    assert question_state is not None and question_state[0] == "candidate"
    # в базе ровно один terminal-event — сам след, без уничтоженного двойника
    assert len(await _terminal_events(scratch_url)) == 1


@pytest.mark.asyncio
async def test_cancelling_the_session_task_leaves_a_cancelled_trace(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Пункт «б»: отмена задачи (остановка процесса) → `session_cancelled` со следом.

    `asyncio.CancelledError` — наследник `BaseException`, поэтому прежняя ветка
    `except Exception` его не видела: ни следа, ни снятия допуска.
    """
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)
    fake_llm.script([{"content": TOOL_PYTHON}])

    gate = _GateExecutor(tmp_path / "ws")
    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws", executor=gate)
    try:
        task = asyncio.create_task(orch.run_session(question_id))
        await asyncio.wait_for(gate.reached_gate.wait(), timeout=120.0)
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task

        traces = await _attempt_traces(scratch_url)
        assert len(traces) == 1
        assert traces[0]["type"] == "session_cancelled"
        payload = _payload(traces[0])
        assert payload["cancelled"] is True
        assert payload["error_class"] == "CancelledError"
        assert payload["session_row_recorded"] is False
        assert payload["question_id"] == str(question_id)
        assert payload["model_calls"] == 1
        assert payload["tool_calls"] == 1
        assert await _scalar(scratch_url, "SELECT id FROM sessions") is None
        # отмена больше не оставляет допуск висеть до истечения аренды
        assert await _scalar(scratch_url, "SELECT session_id FROM session_admissions") is None
        question_state = await _scalar(
            scratch_url, "SELECT state FROM questions WHERE id = :id", {"id": str(question_id)}
        )
        assert question_state is not None and question_state[0] == "candidate"
    finally:
        gate.release.set()
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_session_that_records_its_own_failure_gets_no_second_trace(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Пункт «в»: сессия, честно записавшая `SESSION_FAILED`, не получает второго следа.

    Пустая очередь → `no_question` → FAILED фиксируется своим же коммитом фазы 1: строка сессии
    ЕСТЬ, событие есть, маркера попытки нет.
    """
    scratch_url, _engine = migrated_db
    fake_llm.script([])

    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
    try:
        outcome = await orch.run_session()
    finally:
        await gateway.close()
        await engine.dispose()

    assert await _scalar(scratch_url, "SELECT state FROM sessions") is not None
    events = await _terminal_events(scratch_url)
    assert [e["type"] for e in events] == ["session_failed"]
    assert TRACE_MARKER not in _payload(events[0])
    assert "no_question" in str(events[0]["public_summary"])
    assert str(events[0]["session_id"]) == str(outcome.session_id)
    assert await _attempt_traces(scratch_url) == []


@pytest.mark.asyncio
async def test_committed_session_row_is_never_reported_as_an_unrecorded_attempt(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Неизвестный исход COMMIT (§3, §5.2.2) не превращается в ложный failure.

    Если исключение прилетело УЖЕ ПОСЛЕ фиксации строки (ответ COMMIT потерян), след ставить
    нельзя: исход решает реконсиляция. Проверяется прямой вызов записи следа при существующей
    строке сессии — тот самый случай «COMMIT прошёл, а мы об этом не знаем».
    """
    scratch_url, _engine = migrated_db
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    session_id = uuid.uuid4()
    try:
        async with factory() as db, transaction(db):
            snapshot_id = (
                await db.execute(
                    text("SELECT id FROM config_snapshots WHERE activation_mode = 'bootstrap'")
                )
            ).scalar_one()
            await db.execute(
                text("INSERT INTO sessions (id, state, config_snapshot_id) VALUES (:id, :s, :c)"),
                {"id": str(session_id), "s": "committing", "c": str(snapshot_id)},
            )

        orch, gateway, orch_engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")
        try:
            trace = AttemptTrace(session_id=session_id, started_at=AttemptTrace.now())
            with caplog.at_level(logging.INFO, logger="apps.orchestrator.orchestrator"):
                await orch._record_unrecorded_attempt(trace, RuntimeError("commit response lost"))
        finally:
            await gateway.close()
            await orch_engine.dispose()

        assert await _attempt_traces(scratch_url) == []
        assert any("реконсиляция" in record.message for record in caplog.records)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_credentials_of_the_gateway_never_reach_the_audit_or_the_journal(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Пункт «д»: обрезанный и замалченный текст — ни секрета в payload, ни секрета в журнале."""
    scratch_url, _engine = migrated_db
    question_id = await _seed_question(scratch_url)

    orch, gateway, engine = _make_orchestrator(scratch_url, fake_llm, tmp_path / "ws")

    async def _refusing_chat(*args: Any, **kwargs: Any) -> Any:
        raise LLMTransientError(
            "Client error '401 Unauthorized' for url "
            "'http://192.168.1.141:8080/v1/chat/completions' "
            f"for more information check: https://developer.mozilla.org/en-US/docs/Web/HTTP/Status/401. "
            f"request headers: Authorization: Bearer {FAKE_CREDENTIAL}; "
            f"retry with api_key={FAKE_CREDENTIAL} " + ("x" * 900)
        )

    gateway.chat = _refusing_chat  # тестовый двойник на уровне шлюза LLM (разрешённый триггер)
    try:
        with caplog.at_level(logging.INFO, logger="apps.orchestrator.orchestrator"), pytest.raises(
            LLMTransientError
        ):
            await orch.run_session(question_id)
    finally:
        await gateway.close()
        await engine.dispose()

    traces = await _attempt_traces(scratch_url)
    assert len(traces) == 1
    payload = _payload(traces[0])
    assert payload["error_class"] == "LLMTransientError"
    assert payload["question_id"] == str(question_id)
    assert len(str(payload["error"])) <= 500
    assert FAKE_CREDENTIAL not in json.dumps(payload, ensure_ascii=False)
    assert "Bearer" in str(payload["error"])  # видно, ЧТО был заголовок, не видно значения

    # ни одного секрета во всей таблице журнала
    all_payloads = await _rows(scratch_url, "SELECT payload FROM audit_events")
    assert FAKE_CREDENTIAL not in json.dumps([_payload(r) for r in all_payloads], ensure_ascii=False)
    assert FAKE_CREDENTIAL not in caplog.text
