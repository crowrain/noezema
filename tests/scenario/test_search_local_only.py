"""Scenario: поиск без внешнего движка — модель получает честную формулировку (T7.71).

Профиль open_lab потолок `web.search` выдаёт, но режим `research_proxy` для поиска внешнего движка не
использует (`apps/research_proxy/modes.py`: searxng_url живёт только у curated; у open_lab egress —
`fetch` по закрытому списку доменов). Значит вызов `web.search` на таком узле обязан:

1. выполниться (инструмент выдан) и не сделать ни одного исходящего запроса — даже когда секция
   снапшота содержит адрес движка «на всякий случай»;
2. вернуть модели честное наблюдение: внешний поиск не выполнялся, а локальные совпадения — это память
   узла, а не подтверждение внешней темы заново;
3. не породить ни доказательств, ни источников.

Интернета нет: фейковый движок — локальный origin из allowlist; он обязан остаться неиспользованным.
"""

from __future__ import annotations

import copy
import json
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, ClassVar

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.search_view import BEGIN_MARKER, UPSTREAM_NOTE
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.enums import SessionState
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = pytest.mark.scenario


class _Handler(BaseHTTPRequestHandler):
    base: str = ""
    requests: ClassVar[list[str]] = []  # подменяется списком конкретного движка

    def do_GET(self) -> None:
        type(self).requests.append(self.path)
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/search":
            body = json.dumps(
                {"results": [{"url": f"{self.base}/page", "title": "hit", "content": "фрагмент"}]}
            ).encode()
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
    """Движок поднят и отвечает, но в open_lab режиме поиск обязан к нему не обращаться."""

    def __init__(self) -> None:
        self.requests: list[str] = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = type(
            "BoundHandler", (_Handler,), {"base": f"http://127.0.0.1:{self.port}", "requests": self.requests}
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
def engine_origin() -> Iterator[FakeSearchEngine]:
    o = FakeSearchEngine()
    yield o
    o.stop()


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    eng = create_async_engine(scratch_url)
    try:
        async with eng.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).fetchall())
    finally:
        await eng.dispose()


async def _activate_open_lab(fixture_engine: Any, origin: FakeSearchEngine) -> None:
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "open_lab"
    # закрытый список доменов egress для fetch; адрес движка оставлен — он НЕ должен быть использован
    rp["allowed_domains"] = ["127.0.0.1"]
    rp["searxng_url"] = origin.base
    rp["private_allowlist"] = origin.allowlist
    pol = payload["policy"]
    pol["access_profile"] = "open_lab"
    pol["capabilities"]["tools"] = ["workspace.read", "memory.search", "research.fetch", "web.search"]
    result = await _run_online(fixture_engine, payload)
    assert result.state == "active"


async def _seed_question(scratch_url: str, text_: str) -> Any:
    eng = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as db, transaction(db):
            q = await QuestionRepository.create(db, ORMQuestion(text=text_, origin="seeded", priority=1))
            return q.id
    finally:
        await eng.dispose()


async def test_open_lab_search_runs_without_any_egress_and_says_so(
    migrated_db: tuple[str, Any],
    engine_origin: FakeSearchEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
) -> None:
    scratch_url, fixture_engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _activate_open_lab(fixture_engine, engine_origin)
    question_id = await _seed_question(scratch_url, "Что известно о стоимости владения сервером?")

    gateway = LLMMiddleware(
        LLMGatewayConfig(base_url=fake_llm.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )
    factory = async_sessionmaker(fixture_engine, expire_on_commit=False)
    orch = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=ModelProfile(model_alias="fake-thinker"),
        executor=StubToolExecutor(tmp_path / "ws"),
        research_service=ResearchProxyService(factory, store),
    )
    fake_llm.script(
        [
            {
                "content": {
                    "public_rationale": "Посмотреть, что есть в памяти узла",
                    "decision": {
                        "kind": "tool",
                        "tool": "web.search",
                        "arguments": {"query": "стоимость владения сервером"},
                    },
                }
            },
            {
                "content": {
                    "public_rationale": "Внешних данных нет — завершаю без утверждений",
                    "decision": {"kind": "complete", "reason": "no_progress"},
                }
            },
            {
                "content": {
                    "summary": "Подтверждать нечем",
                    "claims": [],
                    "evidence_links": [],
                    "new_questions": [],
                }
            },
        ]
    )
    try:
        outcome = await orch.run_session(question_id)
    finally:
        await gateway.close()

    # no_progress — честное завершение без внешних данных: сессия partial, не отказ
    assert outcome.final_state is SessionState.SUCCEEDED_PARTIAL, outcome.final_state
    assert outcome.termination_reason == "no_progress"

    # 1) действие выполнено (инструмент выдан профилем open_lab), отказа политики нет
    actions = await _rows(scratch_url, "SELECT state, error_code FROM actions WHERE tool = :t", {"t": "web.search"})
    assert len(actions) == 1 and actions[0][0] == "completed" and actions[0][1] is None

    # 2) ни одного исходящего запроса: ни в журнале узла, ни на самом фейковом движке
    upstream = await _rows(
        scratch_url, "SELECT count(*) FROM audit_events WHERE type = :t", {"t": "research_upstream_request"}
    )
    assert int(upstream[0][0]) == 0
    assert engine_origin.requests == [], f"open_lab не имеет права обращаться к движку: {engine_origin.requests}"

    # 3) модель получила честную формулировку: внешний поиск не выполнялся, выдумывать нечего
    contexts = [r["last_user"] for r in fake_llm.requests()]
    search_ctx = next((c for c in contexts if "стоимость владения сервером" in c and "web.search" in c), "")
    assert search_ctx, "наблюдение поиска не дошло до модели"
    assert BEGIN_MARKER not in search_ctx, "без внешних результатов fence не открывается"
    assert "внешний поиск не выполнялся" in search_ctx
    for line in UPSTREAM_NOTE.splitlines():
        assert line not in search_ctx, "пояснение про внешнюю выдачу показано там, где её не было"

    # 4) и знания из этого не выросло
    assert int((await _rows(scratch_url, "SELECT count(*) FROM evidence"))[0][0]) == 0
    assert int((await _rows(scratch_url, "SELECT count(*) FROM sources"))[0][0]) == 0
