"""Scenario: config-v14 на реальном входе веб-узла (T7.71, ADR-0027 §4; закрытие G8/G9).

Тот же путь, что и у «Запустить обработку» на dev-стенде (`build_standalone_app` + `POST
/api/v1/commands {wake_now}`), но со снимком **config-v14**: в нём есть `web.search`, нет
несуществующего `artifact.create`, и пин промпта explorer указал на explorer-v6.

Что проверяется после пробуждения узла (прямые SELECT по scratch-БД):

- активированный снимок = config-v14 (канонический хеш payload'а, список инструментов);
- `web.search` завершился без ошибки и без «research proxy is not configured» (тот же класс дефекта,
  что G1 для research.fetch);
- upstream-запрос записан в журнал узла с формулировкой запроса модели;
- шаги explorer'а шли под пином explorer-v6 (версия и sha256 совпадают со снимком): пин проверяется
  на admission, а не только при активации;
- из одного только поиска не появилось ни доказательств, ни источников.

Внешняя сеть не используется: фейковый SearXNG — локальный origin из allowlist снапшота.
"""

from __future__ import annotations

import asyncio
import copy
import json
import threading
import urllib.parse
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.web.api import build_standalone_app
from hostctl.unit_state import publish_unit_state
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import ActionState, AuditEventType, QuestionOrigin
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from packages.llm_gateway.roles import tool_schema_hash
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

REPO_ROOT = Path(__file__).resolve().parents[2]
V14_PATH = REPO_ROOT / "docs" / "eval" / "config-v14-payload.json"

#: канонический хеш payload'а config-v14 (то, что попадает в config_snapshots.payload_sha256)
V14_CANONICAL = "22903be78602cf7897f0de57b99514b66c58eca83960fa05458fd341e0104df4"
EXPLORER_V6_SHA = "5e4cffd85e2c038d92ef7bf3264183a4eda2ca09896707dcdabebaabbad09952"

PAGE_MARK = "точный факт с прочитанной страницы"


class _Handler(BaseHTTPRequestHandler):
    base: str = ""  # подставляется при создании класса-обработчика

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/search":
            body = json.dumps(
                {
                    "results": [
                        {
                            "url": f"{self.base}/page",
                            "title": "Стоимость владения сервером",
                            "content": "навигационный фрагмент из поисковой выдачи",
                        }
                    ]
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        elif parsed.path == "/page":
            body = (
                "<html><head><title>Стоимость владения</title></head><body>"
                f"<p>{PAGE_MARK}: 1200 евро за стойку в год.</p></body></html>"
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
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
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.server.server_address[1]
        self.server.RequestHandlerClass = type(
            "BoundHandler", (_Handler,), {"base": f"http://127.0.0.1:{self.port}"}
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


async def _seed_question(scratch_url: str, text_: str) -> None:
    eng = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(eng, expire_on_commit=False)() as db, transaction(db):
            await QuestionRepository.create(db, ORMQuestion(text=text_, origin=QuestionOrigin.SEEDED.value))
    finally:
        await eng.dispose()


def _v14(origin: FakeSearchEngine) -> dict[str, Any]:
    """Настоящий payload config-v14 с одной подменой: адрес движка на локальный фейк."""
    payload = json.loads(V14_PATH.read_text(encoding="utf-8"))
    rp = payload["research_proxy"]
    assert rp["mode"] == "curated"  # как на стенде
    rp["searxng_url"] = origin.base
    rp["private_allowlist"] = origin.allowlist
    return payload


async def test_config_v14_activation_records_the_search_grant_and_the_explorer_pin(
    migrated_db: tuple[str, Any],
) -> None:
    """Активация config-v14 как есть (без подмен): снимок в БД = payload'у файла."""
    from packages.domain.canonical import canonical_sha256

    scratch_url, fixture_engine = migrated_db
    payload = json.loads(V14_PATH.read_text(encoding="utf-8"))
    result = await _run_online(fixture_engine, copy.deepcopy(payload))
    assert result.state == "active"

    row = await _rows(
        scratch_url,
        "SELECT payload_sha256, policy->'capabilities'->>'tools', policy->>'access_profile', "
        "prompts->'explorer'->>'version' "
        "FROM config_snapshots WHERE activation_state = 'active' AND activation_mode = 'online'",
    )
    assert len(row) == 1
    snapshot_hash, tools_json, access_profile, explorer_version = row[0]
    assert snapshot_hash == canonical_sha256(payload) == V14_CANONICAL
    assert access_profile == "curated"
    assert explorer_version == "explorer-v6"

    tools = json.loads(tools_json) if isinstance(tools_json, str) else tools_json
    assert "web.search" in tools
    assert "artifact.create" not in tools  # «unknown tool: artifact.create» больше не воспроизведётся


async def test_web_wake_now_searches_through_the_wired_proxy_under_explorer_v6(
    migrated_db: tuple[str, Any],
    search_engine: FakeSearchEngine,
    fake_llm: FakeLLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_url, fixture_engine = migrated_db
    data_root = tmp_path / "var-lib-noezema-dev"
    host_lib = data_root / "host"
    unit_state = host_lib / "unit-state.json"
    host_lib.mkdir(parents=True)

    result = await _run_online(fixture_engine, _v14(search_engine))
    assert result.state == "active"
    await _seed_question(scratch_url, "Сколько стоит владение сервером в 2026 году?")

    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_HOST_LIB", str(host_lib))
    monkeypatch.setenv("NOEZEMA_UNIT_STATE", str(unit_state))
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("NOEZEMA_LLM_MODEL", "fake-thinker")
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")
    monkeypatch.delenv("NOEZEMA_ADMIN_TOKEN", raising=False)

    search_call: JsonDict = {
        "public_rationale": "Найти внешние источники",
        "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": "стоимость владения сервером 2026"}},
    }
    fetch_call: JsonDict = {
        "public_rationale": "Прочитать выбранный источник",
        "decision": {
            "kind": "tool",
            "tool": "research.fetch",
            "arguments": {"url": f"{search_engine.base}/page"},
        },
    }
    complete: JsonDict = {
        "public_rationale": "Данных достаточно",
        "decision": {"kind": "complete", "reason": "goal_reached"},
    }
    curator: JsonDict = {
        "summary": "Стоимость владения сервером",
        "claims": [
            {
                "statement": PAGE_MARK + ": 1200 евро за стойку в год",
                "claim_type": "external_fact",
                "scope": {"expr": "стоимость владения сервером"},
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }
    fake_llm.script([{"content": c} for c in (search_call, fetch_call, complete, curator)])
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})

    app = build_standalone_app()
    try:
        async with app.router.lifespan_context(app):
            assert app.state.orchestrator is not None
            async with _client(app) as client:
                r = await client.post(
                    "/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-search-v14"}
                )
                assert r.status_code == 202, r.text
                assert r.json()["state"] == "completed", r.json()

                data: JsonDict = {}
                for _ in range(250):
                    data = (await client.get("/api/v1/status")).json()
                    if data["node_state"] == "idle" and data["session"] is None:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"поисковая сессия через standalone-вход не завершилась: {data}")

        # 1) поиск выполнился на веб-входе: ни «not configured», ни отказа политиками
        actions = await _rows(
            scratch_url,
            "SELECT state, error_code, idempotency_class FROM actions WHERE tool = 'web.search'",
        )
        assert len(actions) == 1, f"ожидался ровно один web.search, получено {actions}"
        state, error_code, idem = actions[0]
        assert state == ActionState.COMPLETED.value, f"state={state} error={error_code}"
        assert error_code is None and idem == "observation"

        not_configured = await _rows(
            scratch_url,
            "SELECT count(*) FROM audit_events WHERE payload->>'error' LIKE '%not configured%'",
        )
        assert int(not_configured[0][0]) == 0

        # 2) upstream-запрос журнала узла хранит формулировку запроса модели
        upstream = await _rows(
            scratch_url,
            "SELECT payload->>'query', payload->>'status' FROM audit_events WHERE type = :t",
            {"t": AuditEventType.RESEARCH_UPSTREAM_REQUEST.value},
        )
        assert len(upstream) == 1 and upstream[0][1] == "ok"
        assert "стоимость владения сервером 2026" in upstream[0][0]

        # 3) пин explorer-v6 действительно использовался в шагах (ADR-0019)
        pins = await _rows(
            scratch_url,
            "SELECT DISTINCT prompt_version, prompt_sha256 FROM model_runs "
            "WHERE prompt_version LIKE 'explorer%'",
        )
        assert pins and all(p[0] == "explorer-v6" and p[1] == EXPLORER_V6_SHA for p in pins), pins

        # 4) offered-список шагов = возможности v14 без message.reply (пустой inbox, T7.13)
        hashes = await _rows(
            scratch_url,
            "SELECT DISTINCT tool_schema_hash FROM model_runs WHERE prompt_version LIKE 'explorer%'",
        )
        payload = _v14(search_engine)
        offered = sorted(t for t in payload["policy"]["capabilities"]["tools"] if t != "message.reply")
        assert [h[0] for h in hashes] == [tool_schema_hash(offered)]

        # 5) из поиска не выросло знание: единственное доказательство — из прочитанной страницы
        evidence = await _rows(
            scratch_url,
            "SELECT evidence_kind, count(*) FROM evidence GROUP BY evidence_kind",
        )
        assert {row[0]: int(row[1]) for row in evidence} == {"source_assertion": 1}
        sources = [row[0] for row in await _rows(scratch_url, "SELECT canonical_uri FROM sources")]
        assert sources == [f"{search_engine.base}/page"]

        # артефакты легли в sibling-каталог workspace под тем же data root (путь стенда)
        artifacts_dir = data_root / "artifacts"
        assert artifacts_dir.is_dir() and any(artifacts_dir.rglob("*"))
    finally:
        await app.state.orchestrator.gateway.close()
        await app.state.engine.dispose()


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")
