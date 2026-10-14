"""Scenario (T7.111 часть 1): почти-повтор поискового запроса отклоняется в живом цикле сессии.

`web.search`, множество слов которого совпадает (Жаккар >= 0,6) с уже ВЫПОЛНЕННЫМ запросом этой
сессии, отклоняется как T7.12 (ACTION_FAILED c `denied: True`, без строки действия) и стоит один
шаг. Тексты запросов — из замера менеджера (сессия b357a12b, фикстура
`fixtures-t7111/step-budget-2026-10-14.txt`): повтор-копия, перестановка слов и расширение
формулировки. Смена угла (назвать акторов вместо перефразы) выполняется.

Поисковый движок — локальный фейк SearXNG; внешних запросов тест не делает.
"""

from __future__ import annotations

import copy
import json
import threading
import urllib.parse
import uuid
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType, SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

# тексты из фикстуры замера (сессия b357a12b) — фигуранты пункта 1 решения
SALARY_FIRST = "независимые оценки реальных зарплат в России 2026 hh.ru SuperJob аналитики банков"
SALARY_COPY = SALARY_FIRST
SALARY_REORDERED = "независимые оценки реальных зарплат Россия 2026 SuperJob hh.ru аналитики банков"
SALARY_EXTENDED = "независимые оценки реальных зарплат в России 2026 SuperJob hh.ru аналитики банков прогноз"
ROSSTAT_SALARY_1 = "Росстат реальные заработные платы 2026 год"
ROSSTAT_SALARY_2 = "Росстат реальные заработные платы 2026"
# законная смена угла (та же фикстура): назвать конкретных акторов вместо перефразы
SALARIED_ANALYSTS = "аналитики банков прогноз реальных зарплат Россия 2026 SberCIB Альфа ВТБ"


class _SearchHandler(BaseHTTPRequestHandler):
    """Fake SearXNG: `/search?format=json&q=…` отвечает один и тот же набор хитов."""

    base: str = ""

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/search":
            body = json.dumps(
                {
                    "results": [
                        {
                            "url": f"{self.base}/page",
                            "title": "Индекс рынка труда",
                            "content": "навигационный фрагмент из поисковой выдачи",
                        }
                    ]
                }
            ).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.send_header("Content-Length", "0")
            self.end_headers()

    def log_message(self, *args: Any) -> None:
        pass


class FakeSearchEngine:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SearchHandler)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = type(
            "BoundSearchHandler", (_SearchHandler,), {"base": f"http://127.0.0.1:{self.port}"}
        )
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def allowlist(self) -> list[str]:
        return [f"127.0.0.1:{self.port}"]

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture()
def search_engine() -> Iterator[FakeSearchEngine]:
    engine = FakeSearchEngine()
    yield engine
    engine.stop()


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).all())
    finally:
        await engine.dispose()


def _curated_payload(tools: list[str], origin_base: str, allowlist: list[str]) -> dict[str, Any]:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = origin_base
    rp["private_allowlist"] = allowlist
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], *tools]
    return payload


def _search_call(query: str) -> dict[str, Any]:
    return {
        "content": {
            "public_rationale": f"поиск: {query[:40]}",
            "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": query}},
        }
    }


_COMPLETE = {"content": {"public_rationale": "готово", "decision": {"kind": "complete", "reason": "goal_reached"}}}
_CURATOR = {"content": {"summary": "Данные собраны", "claims": [], "evidence_links": [], "new_questions": []}}


def _gateway(fake_llm: FakeLLM) -> LLMMiddleware:
    return LLMMiddleware(
        LLMGatewayConfig(
            base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01
        )
    )


async def _seed_question(scratch_url: str, text_: str) -> uuid.UUID:
    engine = create_async_engine(scratch_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db, transaction(db):
            question = await QuestionRepository.create(db, ORMQuestion(text=text_, origin="seeded", priority=1))
            return question.id
    finally:
        await engine.dispose()


async def test_near_duplicate_search_is_denied_like_t712(
    migrated_db: tuple[str, Any],
    search_engine: FakeSearchEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    """Повтор-копия и две перефразы выполненного запроса отклоняются с названием прежнего запроса
    и его шага; смена угла (назвать акторов) выполняется. Шаги считаются как прежде (T7.76):
    каждая попытка — шаг, отказанная тоже."""
    scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    result = await _run_online(
        engine,
        _curated_payload(["research.fetch", "web.search"], search_engine.base, search_engine.allowlist),
    )
    assert result.state == "active"
    question_id = await _seed_question(
        scratch_url, "Как изменились реальные заработные платы в России в 2026 году?"
    )

    gateway = _gateway(fake_llm)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=ResearchProxyService(factory, store),
    )
    fake_llm.script(
        [
            _search_call(SALARY_FIRST),  # шаг 1: выполняется (раньше такого запроса нет)
            _search_call(SALARY_COPY),  # шаг 2: копия -> отказ (1.0 от шага 1)
            _search_call(ROSSTAT_SALARY_1),  # шаг 3: другой показатель -> выполняется
            _search_call(ROSSTAT_SALARY_2),  # шаг 4: та же формулировка без слова «год» -> отказ (0.833 от шага 3)
            _search_call(SALARY_REORDERED),  # шаг 5: перестановка слов -> отказ (0.769 от шага 1)
            _search_call(SALARY_EXTENDED),  # шаг 6: та же формулировка + «прогноз» -> отказ (0.923 от шага 1)
            _search_call(SALARIED_ANALYSTS),  # шаг 7: назвать акторов -> выполняется (законная смена угла)
            copy.deepcopy(_COMPLETE),  # шаг 8
            copy.deepcopy(_CURATOR),
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()
    assert outcome.final_state is SessionState.SUCCEEDED

    denials = await _rows(
        scratch_url,
        "SELECT payload->>'similar_step', payload->>'similarity', payload->>'similar_query' "
        "FROM audit_events WHERE type = :t AND payload->>'reason' = 'search_query_near_duplicate' "
        "ORDER BY sequence",
        {"t": AuditEventType.ACTION_FAILED.value},
    )
    assert len(denials) == 4, "копия, повтор без слова и две перефразы обязаны быть отклонены"
    assert [(row[0], row[1]) for row in denials] == [("1", "1.0"), ("3", "0.833"), ("1", "0.769"), ("1", "0.923")]
    assert denials[0][2] == SALARY_FIRST and SALARY_FIRST[:40] in denials[2][2]

    # отказ записан как T7.12: denied-аудит без строки действия — выполненных поисков ровно 3
    executed = await _rows(scratch_url, "SELECT count(*) FROM actions WHERE tool = 'web.search'")
    assert executed[0][0] == 3

    # модель видит отказ по-русски, с прежним запросом и легальной заменой; наблюдения накапливаются,
    # поэтому текст отказа присутствует во всех запросах после первого отказа (шаги 3..8)
    denied_ctx = [r["last_user"] for r in fake_llm.requests() if "web.search ОТКЛОНЕНО" in r["last_user"]]
    assert len(denied_ctx) == 6
    assert any("почти повтор запроса шага 1" in ctx for ctx in denied_ctx)
    assert any("почти повтор запроса шага 3" in ctx for ctx in denied_ctx)
    assert any("прочитай найденное (research.fetch) или смени формулировку по существу" in ctx for ctx in denied_ctx)
    assert all("Отклонённое действие тоже стоит один шаг" in ctx for ctx in denied_ctx)

    # шаг за отклонённое действие платится по-прежнему: 8 шагов = 7 попыток поиска + complete
    assert outcome.steps == 8
    assert fake_llm.state()["queue_left"] == 0
