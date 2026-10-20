"""T7.127 (часть 3, тест 5б): отказ по лимиту поиска узла — понятный текст и прежний журнал.

Настоящий `ResearchProxyService` против фиктивного SearXNG на 127.0.0.1 (внешних запросов тест не
делает и эталонный `noezema-searxng` не трогает). Лимит снимка — 1 запрос за окно: второй поиск
узел не пропускает и объясняет исследователю, что лимит исчерпан, сколько осталось ждать и что
делать вместо новых поисков.

Журнальная часть не меняется: то же событие `research_fetch_rejected` с той же причиной, строка
пишется БЕЗ session_id (поэтому отчёт честности и коррелирует отказ по интервалу сессии),
`research_upstream_request` на отказе НЕ появляется — узел не спрашивал движок.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.service import ResearchProxyError, ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.enums import AuditEventType

pytestmark = pytest.mark.scenario


class FakeSearxngOrigin:
    """Фиктивный SearXNG JSON API: один хит на любой запрос и счётчик обращений к движку."""

    def __init__(self) -> None:
        self.hits = 0
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                owner.hits += 1
                body = json.dumps(
                    {
                        "results": [
                            {
                                "url": "https://upstream.example/article",
                                "title": "Внешняя статья",
                                "content": "факт из внешнего источника",
                            }
                        ]
                    }
                ).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args: Any) -> None:
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
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
def searxng_origin() -> Iterator[FakeSearxngOrigin]:
    origin = FakeSearxngOrigin()
    yield origin
    origin.stop()


def _section(origin: FakeSearxngOrigin, rate_limit_max: int) -> dict[str, Any]:
    return {
        "mode": "curated",
        "max_response_bytes": 65536,
        "max_redirects": 3,
        "timeout_seconds": 2,
        "user_agent": "noezema-test/1.0",
        "private_allowlist": origin.allowlist,
        "searxng_url": origin.base,
        "allowed_domains": [],
        "rate_limit_max": rate_limit_max,
        "rate_limit_window_seconds": 3600,
    }


async def _set_section(scratch_url: str, section: dict[str, Any]) -> None:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("UPDATE config_snapshots SET research_proxy = CAST(:v AS jsonb) "
                     "WHERE activation_mode = 'bootstrap'"),
                {"v": json.dumps(section)},
            )
    finally:
        await engine.dispose()


async def _rows(scratch_url: str, sql: str, params: dict[str, Any]) -> list[Any]:
    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return list((await conn.execute(text(sql), params)).fetchall())
    finally:
        await engine.dispose()


async def test_rate_limit_refusal_is_explained_and_journal_stays_honest(
    migrated_db: tuple[str, Any], searxng_origin: FakeSearxngOrigin, tmp_path: Path
) -> None:
    """Лимит 1: второй поиск отклонён с русским объяснением; журнал — прежние типы событий."""
    scratch_url, _fixture_engine = migrated_db
    await _set_section(scratch_url, _section(searxng_origin, rate_limit_max=1))

    engine = create_async_engine(scratch_url)
    try:
        factory = async_sessionmaker(engine, expire_on_commit=False)
        service = ResearchProxyService(factory, FilesystemArtifactStore(tmp_path / "artifacts"))

        first = await service.search("стоимость владения сервером 2026")
        assert first["upstream"], "первый запрос обязан дойти до фиктивного движка"

        with pytest.raises(ResearchProxyError) as excinfo:
            await service.search("цена стойки в дата-центре 2026")

        error = excinfo.value
        assert error.status == "rate_limited"  # машинный код не менялся (ADR-0028 §7)
        message = str(error)
        assert "лимит поиска узла исчерпан" in message
        assert "1 из 1 запрос" in message
        assert "3600 с" in message
        assert "мин" in message
        assert "rate_limited" in message
        assert "research.fetch" in message
        assert len(message) <= 500, "текст влезает в срезку наблюдения оркестратора"
    finally:
        await engine.dispose()

    refused = await _rows(
        scratch_url,
        "SELECT payload->>'reason', payload->>'mode', payload->>'window_seconds', session_id "
        "FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_FETCH_REJECTED.value},
    )
    assert [(r[0], r[1], r[2], r[3]) for r in refused] == [
        ("upstream_rate_limit_exceeded", "curated", "3600", None)
    ]

    journaled = await _rows(
        scratch_url,
        "SELECT payload->>'query' FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_UPSTREAM_REQUEST.value},
    )
    assert [r[0] for r in journaled] == ["стоимость владения сервером 2026"]
    assert searxng_origin.hits == 1, "отказ по лимиту не должен был дойти до поискового движка"
