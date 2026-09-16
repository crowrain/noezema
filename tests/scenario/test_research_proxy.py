"""Scenario: the research proxy end-to-end (T6.1, stage 5, §5.12).

A real local origin server (the only sanctioned exception to the
guard, via the explicit ``private_allowlist``) drives the full path:
effective config → SSRF guard → read-only fetch under limits →
original + normalized artifacts + the provenance journal (sources /
artifact_chunks) + the audit event in one transaction. The guard's
negative side is exercised against the real fetch path (metadata
address, loopback, redirect into private space, over-size, over-
redirect).

The proxy keeps no active content: the response envelope carries
hashes and ids only — the bytes are read back from the artifact
store.
"""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.research_proxy.api import create_proxy_app
from apps.research_proxy.service import (
    UNTRUSTED_EXTERNAL,
    ResearchProxyError,
    ResearchProxyService,
)
from packages.artifacts.store import FilesystemArtifactStore
from packages.domain.models.enums import AuditEventType

pytestmark = pytest.mark.scenario


# ---------------------------------------------------------------- the
# origin server

class _OriginHandler(BaseHTTPRequestHandler):
    """The fake upstream. Routes:

    - /page — a small HTML page (the happy path);
    - /big  — 2 MiB of bytes (the size-cap test);
    - /hop1 → /hop2 → /page (a 2-hop redirect chain);
    - /redirect-loop1 → /redirect-loop2 → /redirect-loop1 (a cycle);
    - /to-metadata → 302 http://169.254.169.254/latest/meta-data/;
    - /to-file → 302 file:///etc/passwd;
    - /slow — sleeps past the timeout;
    - /post-only — 405 for GET (non-200).
    """

    server_version = "FakeOrigin/1.0"  # type: ignore[misc]

    def do_GET(self) -> None:
        path = self.path.split("?")[0]
        if path == "/page":
            body = (
                "<html><head><title>fake page</title></head><body>"
                "<h1>Содержимое страницы</h1><p>факт: 42.</p>"
                "</body></html>".encode()
            )
            self._send(200, "text/html; charset=utf-8", body)
        elif path == "/big":
            self._send(200, "application/octet-stream", b"x" * (2 * 1024 * 1024))
        elif path == "/hop1":
            self._redirect("/hop2")
        elif path == "/hop2":
            self._redirect("/page")
        elif path in ("/redirect-loop1", "/redirect-loop2"):
            self._redirect("/redirect-loop1" if path.endswith("1") else "/redirect-loop2")
        elif path == "/to-metadata":
            self._redirect("http://169.254.169.254/latest/meta-data/")
        elif path == "/to-file":
            self._redirect("file:///etc/passwd")
        elif path == "/slow":
            import time

            time.sleep(3)
            self._send(200, "text/plain", b"late")
        elif path == "/post-only":
            self._send(405, "text/plain", b"method not allowed")
        else:
            self._send(404, "text/plain", b"not found")

    def _send(self, status: int, ctype: str, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _redirect(self, location: str) -> None:
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, *args: Any) -> None:  # silence
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


@pytest.fixture()
async def make_factory():
    """Engines the test creates are disposed on teardown, so the
    fixture's DROP DATABASE is not blocked by a live connection."""
    engines: list[Any] = []

    def _make(url: str) -> Any:
        engine = create_async_engine(url)
        engines.append(engine)
        return async_sessionmaker(engine, expire_on_commit=False)

    yield _make
    for engine in engines:
        await engine.dispose()


# ---------------------------------------------------------------- the
# proxy service on the scratch DB

def _proxy_section(
    origin_base: str = "http://127.0.0.1:0", **overrides: Any
) -> dict[str, Any]:
    section = {
        "mode": "curated",
        "max_response_bytes": 65536,
        "max_redirects": 3,
        "timeout_seconds": 2,
        "user_agent": "noezema-test/1.0",
        "private_allowlist": [],
        "searxng_url": origin_base,
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


async def _scalar_sync(
    scratch_url: str, sql: str, params: dict[str, Any] | None = None
) -> Any:
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            return (await conn.execute(text(sql), params or {})).scalar()
    finally:
        await engine.dispose()


async def _row_sync(
    scratch_url: str, sql: str, params: dict[str, Any] | None = None
) -> tuple[Any, ...] | None:
    """The first row as a tuple (multi-column selects)."""
    from sqlalchemy import text

    engine = create_async_engine(scratch_url)
    try:
        async with engine.connect() as conn:
            row = (await conn.execute(text(sql), params or {})).mappings().first()
            return tuple(row.values()) if row is not None else None
    finally:
        await engine.dispose()


# ---------------------------------------------------------------- tests

async def test_fetch_persists_provenance_and_marks_untrusted(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist, max_response_bytes=65536),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    envelope = await service.fetch(f"{origin.base}/page")

    # the envelope: hashes and ids, NEVER the content
    assert "data" not in json.dumps(envelope)
    assert envelope["trust_class"] == UNTRUSTED_EXTERNAL
    assert "недоверенный" in envelope["note"]
    expected_sha = hashlib.sha256(
        (
            "<html><head><title>fake page</title></head><body>"
            "<h1>Содержимое страницы</h1><p>факт: 42.</p>"
            "</body></html>"
        ).encode()
    ).hexdigest()
    assert envelope["original_sha256"] == expected_sha
    assert store.exists(expected_sha)
    # read back: the original is intact in the content-addressed store
    assert "факт: 42.".encode() in store.get(expected_sha)
    # normalized text stored too
    assert envelope["normalized_sha256"] is not None
    normalized_text = store.get(envelope["normalized_sha256"]).decode("utf-8")
    assert "Содержимое страницы" in normalized_text
    assert "<h1>" not in normalized_text

    # the provenance journal: a source row + a chunk row, both
    # untrusted_external, in the same transaction as the audit
    source_id = await _scalar_sync(
        scratch_url,
        "SELECT id FROM sources WHERE canonical_uri = :u",
        {"u": f"{origin.base}/page"},
    )
    assert str(source_id) == envelope["source_id"]
    chunk = await _row_sync(
        scratch_url,
        "SELECT c.trust_class, c.origin_kind, c.content_hash, "
        "       c.transform_chain::text, c.parser_fingerprint "
        "FROM artifact_chunks c "
        "WHERE c.source_uri = :u AND c.chunk_id = 'chunk-0' "
        "ORDER BY c.obtained_at DESC LIMIT 1",
        {"u": f"{origin.base}/page"},
    )
    assert chunk is not None
    trust_class, chunk_kind, content_hash, chain, fingerprint = chunk
    assert trust_class == UNTRUSTED_EXTERNAL
    assert chunk_kind == "research_proxy"
    assert content_hash == expected_sha
    assert "normalize:html-text" in chain
    assert fingerprint == "noezema-normalize-v1"

    audit = await _row_sync(
        scratch_url,
        "SELECT payload->>'sha256', payload->>'mode', payload->>'normalized_sha256' "
        "FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_FETCH_COMPLETED.value},
    )
    assert audit is not None
    assert audit[0] == expected_sha and audit[1] == "curated"

    # the artifacts row is marked untrusted_external with the proxy origin
    arow = await _row_sync(
        scratch_url,
        "SELECT origin, trust_class, mime FROM artifacts WHERE sha256 = :s",
        {"s": expected_sha},
    )
    assert arow == ("research_proxy", "external", "text/html; charset=utf-8")


async def test_refetch_is_idempotent_reuses_source_and_chunk(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    """T7.11 (EVAL-3b P.4): refetching already-seen content (same content
    hash) must NOT 500 — the old blind INSERT INTO artifact_chunks hit
    UNIQUE(artifact_id, chunk_id) — and must return the EXISTING source +
    chunk, not create duplicates (EVAL-3b: 500 on the 2nd fetch of the
    same URL, sess3 ×8 europa.eu / sess7 ×3 / sess2 python.org)."""
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist, max_response_bytes=65536),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    url = f"{origin.base}/page"
    envelope1 = await service.fetch(url)
    source_id1 = envelope1["source_id"]
    # the second fetch of the SAME content (same hash) must succeed,
    # not 500
    envelope2 = await service.fetch(url)

    # idempotent: the SAME source is returned, not a new one
    assert envelope2["source_id"] == source_id1
    assert envelope2["original_sha256"] == envelope1["original_sha256"]
    assert envelope2["normalized_sha256"] == envelope1["normalized_sha256"]

    # exactly ONE source + ONE chunk for this content (no duplicates)
    source_count = await _scalar_sync(
        scratch_url,
        "SELECT count(*) FROM sources WHERE content_hash = :s",
        {"s": envelope1["original_sha256"]},
    )
    assert source_count == 1
    chunk_count = await _scalar_sync(
        scratch_url,
        "SELECT count(*) FROM artifact_chunks c "
        "JOIN artifacts a ON a.id = c.artifact_id "
        "WHERE a.sha256 = :s AND c.chunk_id = 'chunk-0'",
        {"s": envelope1["original_sha256"]},
    )
    assert chunk_count == 1

    # the refetch is audited as idempotent (exactly one such event)
    idem_count = await _scalar_sync(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t "
        "AND payload->>'idempotent' = 'true'",
        {"t": AuditEventType.RESEARCH_FETCH_COMPLETED.value},
    )
    assert idem_count == 1


async def test_sealed_mode_refuses_every_fetch(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, mode="sealed", private_allowlist=origin.allowlist),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/page")
    assert excinfo.value.status == "rejected"
    assert "sealed" in excinfo.value.reason

    # audited; nothing fetched, no source row
    assert await _scalar_sync(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t AND payload->>'reason' = 'sealed_mode_no_egress'",
        {"t": AuditEventType.RESEARCH_FETCH_REJECTED.value},
    ) == 1
    assert await _scalar_sync(scratch_url, "SELECT count(*) FROM sources") == 0
    assert store.exists(hashlib.sha256(b"x").hexdigest()) is False


async def test_guard_blocks_metadata_and_private_targets(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    # the metadata address: blocked before any socket
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch("http://169.254.169.254/latest/meta-data/")
    assert excinfo.value.status == "rejected"

    # a redirect INTO the metadata address: the redirect target is
    # re-validated by the guard
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/to-metadata")
    assert excinfo.value.status == "rejected"
    assert "169.254.169.254" in excinfo.value.reason

    # a redirect to a non-http scheme: blocked
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/to-file")
    assert excinfo.value.status == "rejected"
    assert "scheme" in excinfo.value.reason

    # every refusal is audited
    rejected = await _scalar_sync(
        scratch_url,
        "SELECT count(*) FROM audit_events WHERE type = :t",
        {"t": AuditEventType.RESEARCH_FETCH_REJECTED.value},
    )
    assert rejected == 3


async def test_size_and_redirect_limits(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist, max_response_bytes=1024),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    # over-size: the stream is aborted, nothing stored
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/big")
    assert excinfo.value.status == "failed"
    assert "max_response_bytes" in excinfo.value.reason
    big_sha = hashlib.sha256(b"x" * (2 * 1024 * 1024)).hexdigest()
    assert not store.exists(big_sha)

    # over-redirect: a 2-cycle exceeds max_redirects=3
    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/redirect-loop1")
    assert excinfo.value.status == "rejected"
    assert "too many redirects" in excinfo.value.reason


async def test_redirect_chain_within_limit_follows(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    envelope = await service.fetch(f"{origin.base}/hop1")
    assert envelope["redirects"] == 2
    assert envelope["final_url"] == f"{origin.base}/page"


async def test_timeout_and_non200(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist, timeout_seconds=1),
    )
    factory = make_factory(scratch_url)
    service = ResearchProxyService(factory, store)

    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/slow")
    assert excinfo.value.status == "failed"

    with pytest.raises(ResearchProxyError) as excinfo:
        await service.fetch(f"{origin.base}/post-only")
    assert excinfo.value.status == "failed"
    assert "405" in excinfo.value.reason


async def test_http_api_envelope_and_sealed_status(
    migrated_db: tuple[str, Any], origin: FakeOrigin, tmp_path: Path, make_factory: Any
) -> None:
    scratch_url, _engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    await _set_section(
        scratch_url,
        _proxy_section(origin.base, private_allowlist=origin.allowlist),
    )
    factory = make_factory(scratch_url)
    app: FastAPI = create_proxy_app(ResearchProxyService(factory, store))

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://proxy.test") as client:
        r = await client.get("/healthz")
        assert r.status_code == 200

        r = await client.post("/fetch", json={"url": f"{origin.base}/page"})
        assert r.status_code == 200
        body = r.json()
        assert body["trust_class"] == UNTRUSTED_EXTERNAL
        assert body["original_sha256"]
        assert "Содержимое" not in json.dumps(body, ensure_ascii=False)

        # sealed mode → 403 with the exact reason
        await _set_section(
            scratch_url,
            _proxy_section(origin.base, mode="sealed", private_allowlist=origin.allowlist),
        )
        r = await client.post("/fetch", json={"url": f"{origin.base}/page"})
        assert r.status_code == 403
        assert r.json()["detail"]["reason"] == "sealed mode: no network egress"

        # a guard refusal → 403
        await _set_section(scratch_url, _proxy_section(origin.base, private_allowlist=origin.allowlist))
        r = await client.post(
            "/fetch", json={"url": "http://169.254.169.254/latest/meta-data/"}
        )
        assert r.status_code == 403
