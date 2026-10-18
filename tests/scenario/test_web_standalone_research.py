"""Scenario: a web-started session actually fetches (T7.68, ADR-0027 §1; G1 regression).

The live stand case (docs/web-access-design.md): «Запустить обработку» / wake_now on the dev
stand started a session whose only egress tool (`research.fetch`) died with "research proxy is
not configured for this host" — the policy allowed it, but `build_standalone_app` attached no
research service. This test walks the STAND's own path end to end:

- env-driven `build_standalone_app()` against the scratch DB (no orchestrator argument);
- an activated curated snapshot (the stand's active mode): research.fetch granted, SSRF
  private_allowlist pointing at a local fake origin — NO internet is touched;
- «wake now» via POST /api/v1/commands, the FakeLLM explorer calls research.fetch on the fake
  origin's URL, and the fetch must go through the wired ResearchProxyService.

Pinned afterwards (direct SELECTs against the scratch DB):
the actions row completed without an error, no "not configured" anywhere in the audit, the
research_content_read journal row exists, the provenance rows link to the artifact directory
under <NOEZEMA_DATA_ROOT>/artifacts (the same sibling the wake tick writes), and evidence landed.
"""

from __future__ import annotations

import asyncio
import copy
import threading
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
from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import (
    ActionState,
    AuditEventType,
    QuestionOrigin,
    SessionState,
)
from packages.domain.models.questions import ORMQuestion
from packages.domain.repositories.questions import QuestionRepository
from tests.conftest import FakeLLM
from tests.scenario.test_online_activation import _run_online

pytestmark = [pytest.mark.scenario]

PAGE = (
    "<html><head><title>Инфляция</title></head>"
    "<body><h1>Инфляция в России за 2025 год</h1>"
    "<p>Номинальный рост потребительских цен составил около 7 процентов.</p>"
    "</body></html>"
)

FETCH_TOOL: JsonDict = {
    "public_rationale": "Найти источник по инфляции",
    "decision": {"kind": "tool", "tool": "research.fetch", "arguments": {"url": "__URL__"}},
}
COMPLETE: JsonDict = {
    "public_rationale": "Данные собраны",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}
CURATOR_OK: JsonDict = {
    "summary": "Одно утверждение об инфляции",
    "claims": [
        {
            "statement": "Номинальный рост потребительских цен в России за 2025 год составил около 7 процентов",
            "claim_type": "external_fact",
            "scope": {"expr": "инфляция в России за 2025"},
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


class _OriginHandler(BaseHTTPRequestHandler):
    """The only "upstream" this test talks to — a local loopback fake (no internet)."""

    def do_GET(self) -> None:
        if self.path == "/page":
            body = PAGE.encode("utf-8")
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


class FakeOrigin:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _OriginHandler)
        self.port = self.server.server_address[1]
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
def origin() -> Iterator[FakeOrigin]:
    o = FakeOrigin()
    yield o
    o.stop()


def _client(app: Any) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def _rows(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params or {})).fetchall())
    finally:
        await engine.dispose()


async def _seed_curated_snapshot(engine: Any, origin: FakeOrigin) -> None:
    """Online-activate the stand's shape: curated mode + the research.fetch grant."""
    payload = copy.deepcopy(BOOTSTRAP_PAYLOAD)
    rp = payload["research_proxy"]
    rp["mode"] = "curated"
    rp["searxng_url"] = origin.base  # never called by fetch; curated mode policy requires it
    rp["private_allowlist"] = origin.allowlist  # the fake origin is loopback: only allowed this way
    rp["rate_limit_max"] = 10
    pol = payload["policy"]
    pol["access_profile"] = "curated"
    pol["capabilities"]["tools"] = [*pol["capabilities"]["tools"], "research.fetch"]
    result = await _run_online(engine, payload)
    assert result.state == "active"


async def _seed_question(scratch_url: str, text_: str) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db, transaction(db):
            await QuestionRepository.create(
                db, ORMQuestion(text=text_, origin=QuestionOrigin.SEEDED.value)
            )
    finally:
        await engine.dispose()


async def test_web_wake_now_fetches_through_the_wired_proxy(
    migrated_db: tuple[str, Any],
    origin: FakeOrigin,
    fake_llm: FakeLLM,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scratch_url, fixture_engine = migrated_db
    data_root = tmp_path / "var-lib-noezema-dev"
    host_lib = data_root / "host"
    unit_state = host_lib / "unit-state.json"
    host_lib.mkdir(parents=True)

    await _seed_curated_snapshot(fixture_engine, origin)
    await _seed_question(scratch_url, "Какая инфляция в России за 2025 год?")

    # The stand shape: everything the standalone entry reads comes from the environment.
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", scratch_url)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_HOST_LIB", str(host_lib))
    monkeypatch.setenv("NOEZEMA_UNIT_STATE", str(unit_state))
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", fake_llm.base_url)
    monkeypatch.setenv("NOEZEMA_LLM_MODEL", "fake-thinker")
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")  # no containers in a scenario test
    monkeypatch.delenv("NOEZEMA_ADMIN_TOKEN", raising=False)

    fetch_call = copy.deepcopy(FETCH_TOOL)
    fetch_call["decision"]["arguments"]["url"] = f"{origin.base}/page"
    fake_llm.script([{"content": fetch_call}, {"content": COMPLETE}, {"content": CURATOR_OK}])
    publish_unit_state(unit_state, units={"noezema-runtime.target": "active"})

    app = build_standalone_app()
    try:
        async with app.router.lifespan_context(app):
            orchestrator = app.state.orchestrator
            assert orchestrator is not None

            async with _client(app) as client:
                r = await client.post(
                    "/api/v1/commands", json={"type": "wake_now", "idempotency_key": "w-research"}
                )
                assert r.status_code == 202, r.text
                body = r.json()
                assert body["state"] == "completed", body

                data: JsonDict = {}
                for _ in range(250):
                    data = (await client.get("/api/v1/status")).json()
                    if data["node_state"] == "idle" and data["session"] is None:
                        break
                    await asyncio.sleep(0.1)
                else:
                    pytest.fail(f"research-сессия через standalone-вход не завершилась: {data}")

        # ── the G1 defect must NOT reproduce: research.fetch completed, no "not configured" ──
        actions = await _rows(
            scratch_url,
            "SELECT state, error_code FROM actions WHERE tool = 'research.fetch'",
        )
        assert len(actions) == 1, f"expected exactly one research.fetch action, got {actions}"
        state, error_code = actions[0]
        assert state == ActionState.COMPLETED.value, (
            f"research.fetch must COMPLETE on the web entry, got state={state} error={error_code}"
        )
        assert error_code is None

        not_configured = await _rows(
            scratch_url,
            "SELECT count(*) FROM audit_events WHERE payload->>'error' LIKE '%not configured%'",
        )
        assert int(not_configured[0][0]) == 0, (
            "the T7.68 defect text must not appear in the audit of a web-started session"
        )

        # the host-side read happened through the wired service (mode comes from the snapshot)
        reads = await _rows(
            scratch_url,
            "SELECT payload->>'mode', payload->>'normalized_sha256' FROM audit_events "
            "WHERE type = :t",
            {"t": AuditEventType.RESEARCH_CONTENT_READ.value},
        )
        assert len(reads) == 1 and reads[0][0] == "curated" and len(str(reads[0][1])) == 64

        # provenance rows exist and the fetched page really links to them
        src = await _rows(
            scratch_url,
            "SELECT canonical_uri FROM sources WHERE canonical_uri LIKE :u",
            {"u": f"%{origin.base}/page%"},
        )
        assert len(src) == 1

        chunks = await _rows(
            scratch_url,
            "SELECT count(*), bool_or(origin_kind = 'research_proxy') FROM artifact_chunks "
            "WHERE source_uri = :u",
            {"u": f"{origin.base}/page"},
        )
        assert int(chunks[0][0]) >= 1 and chunks[0][1] is True

        # evidence landed from the fetch observation (the point of the whole path):
        # research.fetch produces a source_assertion record (apps/orchestrator/evidence.py)
        evidence = await _rows(
            scratch_url,
            "SELECT count(*), bool_or(source_id IS NOT NULL) FROM evidence "
            "WHERE evidence_kind = 'source_assertion'",
        )
        assert int(evidence[0][0]) >= 1 and evidence[0][1] is True

        # the artifact directory is the web workspace's sibling under the same data root —
        # exactly where the wake tick writes (T7.68 parity; test_research_wiring pins the roots)
        artifacts_root = data_root / "artifacts"
        stored_files = [p for p in artifacts_root.rglob("*") if p.is_file()]
        assert stored_files, f"no research artifacts under {artifacts_root}"

        # terminal session committed through the web entry
        states = await _rows(scratch_url, "SELECT state FROM sessions")
        assert len(states) == 1 and states[0][0] == SessionState.SUCCEEDED.value

        # the fetched text entered the web-owned session context FENCED (G1 end-to-end)
        explorer_ctxs = [
            r["last_user"] for r in fake_llm.requests() if "<<<UNTRUSTED DATA BEGIN>>>" in r["last_user"]
        ]
        assert explorer_ctxs, "the fetched content never reached the model context"
        assert "Номинальный рост потребительских цен составил около 7 процентов." in max(
            explorer_ctxs, key=len
        )
    finally:
        await app.state.orchestrator.gateway.close()
        await app.state.engine.dispose()
