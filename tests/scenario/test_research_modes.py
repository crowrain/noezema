"""Scenario: research proxy modes end-to-end (T6.2, stage 5, §5.12.1).

The three modes against one scratch DB:

- **sealed** — search reads ONLY the local index (committed claims);
  no egress, no upstream log;
- **curated** — search goes to a fake SearXNG (a local origin served
  through the explicit allowlist): the results come back, EVERY
  upstream request is journaled (upstream log: host + query + status)
  and the rate limit stops further requests;
- **open_lab** — fetch is limited to the closed allowed_domains list
  (an unlisted domain is refused before any socket) and the mode runs
  under the separate open_lab profile.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.service import (
    ResearchProxyError,
    ResearchProxyService,
)
from packages.artifacts.store import FilesystemArtifactStore

pytestmark = pytest.mark.scenario


class _SearxngHandler(BaseHTTPRequestHandler):
    """A fake SearXNG JSON API: /search?format=json&q=..."""

    def do_GET(self) -> None:
        if self.path.startswith("/search"):
            body = json.dumps(
                {
                    "results": [
                        {
                            "url": "https://upstream.example/article",
                            "title": "Внешняя статья",
                            "content": "факт из внешнего источника",
                        },
                        {
                            "url": "not-a-url",
                            "title": "мусор без url",
                        },
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


class FakeSearxng:
    def __init__(self) -> None:
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), _SearxngHandler)
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
def searxng() -> Iterator[FakeSearxng]:
    o = FakeSearxng()
    yield o
    o.stop()


@pytest.fixture()
async def make_factory():
    engines: list[Any] = []

    def _make(url: str) -> Any:
        engine = create_async_engine(url)
        engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    yield _make
    for engine in engines:
        await engine.dispose()


def _section(**overrides: Any) -> dict[str, Any]:
    section = {
        "mode": "sealed",
        "max_response_bytes": 65536,
        "max_redirects": 3,
        "timeout_seconds": 2,
        "user_agent": "noezema-test/1.0",
        "private_allowlist": [],
        "searxng_url": None,
        "allowed_domains": [],
        "rate_limit_max": 20,
        "rate_limit_window_seconds": 3600,
    }
    section.update(overrides)
    return section


async def _set_section(scratch_url: str, section: dict[str, Any]) -> None:
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "UPDATE config_snapshots SET research_proxy = CAST(:v AS jsonb) "
                    "WHERE activation_mode = 'bootstrap'"
                ),
                {"v": json.dumps(section)},
            )
    finally:
        await engine.dispose()


async def _seed_claim(scratch_url: str, statement: str) -> None:
    import uuid as _uuid

    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "INSERT INTO claims (id, statement, claim_type, freshness_status) "
                    "VALUES (:id, :s, 'external_fact', 'fresh')"
                ),
                {"id": str(_uuid.uuid4()), "s": statement},
            )
    finally:
        await engine.dispose()


async def _count(scratch_url: str, sql: str, params: dict[str, Any] | None = None) -> int:
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return int((await conn.execute(text(sql), params or {})).scalar_one())
    finally:
        await engine.dispose()


async def _upstream_log(
    scratch_url: str,
) -> list[tuple[str | None, str | None, str | None]]:
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            rows = (
                (
                    await conn.execute(
                        text(
                            "SELECT payload->>'upstream_host' AS host, "
                            "       payload->>'query' AS query, "
                            "       payload->>'status' AS status "
                            "FROM audit_events "
                            "WHERE type = 'research_upstream_request' "
                            "ORDER BY occurred_at, sequence"
                        )
                    )
                )
                .all()
            )
            return [tuple(r) for r in rows]
    finally:
        await engine.dispose()


async def test_sealed_search_is_local_only(
    migrated_db: tuple[str, Any], make_factory: Any, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(scratch_url, _section())
    await _seed_claim(scratch_url, "стоимость CPU растёт при росте частоты")
    await _seed_claim(scratch_url, "квантовые компьютеры ускоряют факторизацию")

    service = ResearchProxyService(make_factory(scratch_url), store)
    results = await service.search("стоимость CPU частота")

    assert results["mode"] == "sealed"
    assert results["profile"] == "sealed"
    assert len(results["local"]) >= 1
    assert "стоимость CPU" in results["local"][0]["statement"]
    assert results["upstream"] is None  # no upstream in sealed
    assert await _count(
        scratch_url, "SELECT count(*) FROM audit_events WHERE type = 'research_upstream_request'"
    ) == 0
    # no egress at all: a fetch is still refused
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch("http://example.com/")
    assert excinfo.value.status == "rejected"


async def test_curated_search_logs_upstream_and_rate_limits(
    migrated_db: tuple[str, Any], searxng: FakeSearxng, make_factory: Any, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    section = _section(
        mode="curated",
        searxng_url=searxng.base,
        private_allowlist=searxng.allowlist,
        rate_limit_max=2,
        rate_limit_window_seconds=3600,
    )
    await _set_section(scratch_url, section)
    await _seed_claim(scratch_url, "стоимость CPU растёт при росте частоты")

    service = ResearchProxyService(make_factory(scratch_url), store)

    # request 1: local + upstream, the upstream is journaled
    results = await service.search("стоимость CPU")
    assert results["mode"] == "curated"
    assert results["profile"] == "curated"
    assert len(results["local"]) == 1
    assert results["upstream"] is not None
    assert results["upstream"][0]["url"] == "https://upstream.example/article"
    assert "внешнего источника" in results["upstream"][0]["content"]
    # the malformed entry (no http url) is dropped by the parser
    assert all(h["url"].startswith("http") for h in results["upstream"])

    # request 2: still within the limit
    await service.search("квантовая факторизация")
    log = await _upstream_log(scratch_url)
    assert len(log) == 2, f"log={log!r}"
    assert all(h == "127.0.0.1" for h, _q, _s in log), f"log={log!r}"
    assert {q for _h, q, _s in log} == {"стоимость CPU", "квантовая факторизация"}
    assert all(s == "ok" for _h, _q, s in log)

    # request 3: the rate limit — refused, audited, no new upstream hit
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.search("ещё один запрос")
    assert excinfo.value.status == "rate_limited"
    assert len(await _upstream_log(scratch_url)) == 2
    assert await _count(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = 'research_fetch_rejected' "
        "AND payload->>'reason' = 'upstream_rate_limit_exceeded'",
    ) == 1


async def test_open_lab_fetch_is_domain_closed(
    migrated_db: tuple[str, Any], searxng: FakeSearxng, make_factory: Any, tmp_path: Path
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    # the domain list is host-based (like the guard's allowlist): the
    # test-only private host is explicitly listed
    section = _section(
        mode="open_lab",
        allowed_domains=["127.0.0.1"],
        private_allowlist=searxng.allowlist,
    )
    await _set_section(scratch_url, section)

    service = ResearchProxyService(make_factory(scratch_url), store)

    # an unlisted domain is refused BEFORE any socket (no connection
    # is even attempted to a resolvable public host)
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch("http://example.com/")
    assert excinfo.value.status == "rejected"
    assert "open_lab" in excinfo.value.reason

    # the listed endpoint passes the guard AND the domain list
    results = await service.search("любой вопрос")
    assert results["mode"] == "open_lab"
    assert results["profile"] == "open_lab"  # the separate profile
    assert results["upstream"] is None  # open_lab has no search backend
    envelope = await _open_lab_fetch(service, f"{searxng.base}/search?format=json&q=x")
    assert envelope["trust_class"] == "external"


async def _open_lab_fetch(service: ResearchProxyService, url: str) -> dict[str, Any]:
    return await service.fetch(url)
