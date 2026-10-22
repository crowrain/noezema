"""Scenario: `/api/v1/status` показывает последнюю неудачную попытку из БД (T7.132, часть 3).

Случай 22.10: «wake now» принят, узел работал 17 минут, попытка исчезла — оператор видел в
`/api/v1/status` только `node_state` да `last_error` ИЗ ПАМЯТИ этого процесса, а после рестарта
веб-сервиса не видел и их. Теперь статус отдаёт `last_failed_attempt` — строку журнала аудита,
поэтому она переживает рестарт и написана не тем процессом, который читает статус.

Проверяется end-to-end путь оператора: `POST /api/v1/commands wake_now` → сессия гибнет внутри
фазы 1 (отказ шлюза на уровне подставного сервера ответов LLM) → статус называет попытку → НОВЫЙ экземпляр приложения
на той же базе видит её же (след в базе, а не в памяти).
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.scheduler import WakeScheduler
from apps.web.api import create_app
from packages.domain.db.uow import transaction
from packages.domain.models.enums import QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM

pytestmark = [pytest.mark.scenario]


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


@asynccontextmanager
async def _running(app: Any) -> AsyncIterator[Any]:
    async with app.router.lifespan_context(app), _client(app) as client:
        yield client


async def _seed_question(factory: async_sessionmaker) -> uuid.UUID:
    async with factory() as db, transaction(db):
        question = await QuestionRepository.create(
            db, ORMQuestion(text="Сколько будет 6*7?", origin=QuestionOrigin.SEEDED.value)
        )
        return question.id


def _make_app(
    scratch_url: str, fake: FakeLLM, workspace: Path
) -> tuple[Any, AsyncEngine, async_sessionmaker, Orchestrator, LLMMiddleware]:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    gateway = LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(workspace),
    )
    app = create_app(engine=engine, factory=factory, orchestrator=orchestrator)
    return app, engine, factory, orchestrator, gateway


def _announce_session_end(orchestrator: Orchestrator, finished: asyncio.Event) -> None:
    """Инстанс-обёртка `run_session` (тот же приём, что в test_web_node_state_db_truth): конец
    задачи сессии — событие, а не опрос по времени."""
    orig_run = orchestrator.run_session

    async def _run_and_signal(*args: Any, **kwargs: Any) -> Any:
        try:
            return await orig_run(*args, **kwargs)
        finally:
            finished.set()

    orchestrator.run_session = _run_and_signal  # type: ignore[method-assign]


def _announce_outcome_recorded(monkeypatch: pytest.MonkeyPatch, done: asyncio.Event) -> None:
    """`record_session_result` — последняя запись учёта исхода (§5.2.1): после неё узел снова idle."""
    orig = WakeScheduler.record_session_result

    async def _wrapped(self: WakeScheduler, *, final_state: str, now: Any) -> Any:
        result = await orig(self, final_state=final_state, now=now)
        done.set()
        return result

    monkeypatch.setattr(WakeScheduler, "record_session_result", _wrapped)


@pytest.mark.asyncio
async def test_status_reports_the_last_failed_attempt_from_the_db(
    migrated_db: tuple[str, Any],
    fake_llm: FakeLLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Пункт «г»: статус называет последнюю неудачную попытку, и она видна после рестарта веба."""
    scratch_url, _engine = migrated_db
    # ни одного ответа шлюза: первая же фаза получает транспортный отказ внутри фазы 1
    fake_llm.script([])
    app, engine, factory, orchestrator, gateway = _make_app(scratch_url, fake_llm, tmp_path / "ws")
    question_id = await _seed_question(factory)
    finished = asyncio.Event()
    recorded = asyncio.Event()
    _announce_session_end(orchestrator, finished)
    _announce_outcome_recorded(monkeypatch, recorded)

    try:
        async with _running(app) as client:
            wake = await client.post(
                "/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-t7132"}
            )
            assert wake.status_code in (200, 202), wake.text
            assert wake.json()["state"] == "completed"

            await asyncio.wait_for(finished.wait(), timeout=120.0)
            await asyncio.wait_for(recorded.wait(), timeout=120.0)

            status = (await client.get("/api/v1/status")).json()
            # узел честно вернулся в idle (поведение T3.29 не менялось)
            assert status["node_state"] == "idle"
            attempt = status["last_failed_attempt"]
            assert attempt is not None, "статус не показал последнюю неудачную попытку"
            assert attempt["event_type"] == "session_failed"
            assert attempt["error_class"] == "LLMTransientError"
            assert attempt["question_id"] == str(question_id)
            assert attempt["cancelled"] is False
            assert attempt["attempt_session_id"]
            assert attempt["attempt_started_at"] <= attempt["attempt_finished_at"]
            # память этого процесса — по-прежнему отдельный ключ (его поведение не менялось)
            assert status["last_error"]

        # ── «рестарт веб-сервиса»: новое приложение на той же базе, память пуста ──────────────
        second = create_app(engine=engine, factory=factory)
        async with _running(second) as client2:
            restarted = (await client2.get("/api/v1/status")).json()
            assert restarted["last_error"] is None
            assert restarted["node_state"] == "idle"
            assert restarted["last_failed_attempt"] is not None
            assert restarted["last_failed_attempt"]["error_class"] == "LLMTransientError"
            assert restarted["last_failed_attempt"]["question_id"] == str(question_id)
            assert restarted["last_failed_attempt"]["occurred_at"] == attempt["occurred_at"]
    finally:
        await gateway.close()
        await engine.dispose()


@pytest.mark.asyncio
async def test_status_of_a_node_without_failures_has_no_invented_attempt(
    migrated_db: tuple[str, Any], fake_llm: FakeLLM, tmp_path: Path
) -> None:
    """Чистый узел: ключ есть, но он пуст — статус ничего не выдумывает."""
    scratch_url, _engine = migrated_db
    fake_llm.script([])
    app, engine, _factory, _orchestrator, gateway = _make_app(scratch_url, fake_llm, tmp_path / "ws")
    try:
        async with _running(app) as client:
            status = (await client.get("/api/v1/status")).json()
            assert "last_failed_attempt" in status
            assert status["last_failed_attempt"] is None
    finally:
        await gateway.close()
        await engine.dispose()
