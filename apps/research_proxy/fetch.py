"""The controlled egress fetch (T6.1, stage 5, §5.12).

Read-only: GET (and the GET-redirects it follows) and nothing else.
Every hop — the initial URL and each redirect target — is re-validated
by the SSRF guard, including the addresses the hostname resolves to
(pinned, so a rebinding answer cannot slip in mid-flight).

Limits, all from the snapshot's ``research_proxy`` section:
response size (the stream is aborted and the connection closed once
the cap is exceeded), redirect count, and the total time budget.

Active-content hygiene: the fetched bytes exist only in memory for the
duration of the call; the caller stores them into the content-addressed
artifact store (original + normalized + hash) and the working copy is
discarded — the proxy keeps no live cache of fetched content.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

import httpx

from apps.research_proxy.backend import SSRFSafeAsyncBackend
from apps.research_proxy.ssrf_guard import SSRFError, SSRFPolicy, validate_url


class FetchError(RuntimeError):
    """A fetch failed a policy limit (size/redirects/timeout/egress)."""


@dataclass
class FetchResult:
    """A successfully fetched page. ``data`` is the working copy the
    caller must persist (artifact store) and then drop."""

    url: str
    final_url: str
    content_type: str | None
    data: bytes
    sha256: str
    redirects: int
    elapsed_ms: int


class FetchClient:
    """A read-only HTTP client whose every connection passes the guard."""

    def __init__(self, policy: SSRFPolicy) -> None:
        self._policy = policy
        # httpx 0.28 does not expose the network backend on
        # AsyncHTTPTransport, so the SSRF-safe pool is built on httpcore
        # directly (the stock anyio backend, wrapped by the guard)
        import httpcore

        transport = httpx.AsyncHTTPTransport()
        transport._pool = httpcore.AsyncConnectionPool(
            network_backend=SSRFSafeAsyncBackend(policy),
            http1=True,
            http2=False,
        )
        self._client = httpx.AsyncClient(
            transport=transport,
            follow_redirects=False,
            timeout=httpx.Timeout(policy.timeout_seconds),
            headers={"User-Agent": policy.user_agent, "Accept": "*/*"},
            http2=False,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def fetch(self, url: str) -> FetchResult:
        """GET ``url`` under the policy. Raises SSRFError (policy
        violation) or FetchError (transport/limit failure)."""
        start = time.monotonic()
        deadline = start + self._policy.timeout_seconds
        current = url
        redirects = 0
        while True:
            host, port = validate_url(current)
            if not self._policy.allows_private(host, port):
                # the connection path re-checks the resolved
                # addresses; this pre-check keeps error messages
                # precise for obvious IP literals
                _precheck_ip_literal(host)
            request = self._client.build_request(
                "GET", current, headers={"User-Agent": self._policy.user_agent}
            )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise FetchError("time budget exhausted")
            try:
                response = await self._client.send(request, stream=True)
            except (SSRFError, httpx.HTTPError) as exc:
                # the guard raises SSRFError from the connect path;
                # httpx wraps it in a ConnectError — unwrap so the
                # caller sees the policy violation, not a transport one
                cause = exc.__cause__ or exc.__context__
                while cause is not None and not isinstance(cause, SSRFError):
                    cause = cause.__cause__ or cause.__context__
                if cause is not None:
                    raise cause from exc
                if isinstance(exc, httpx.TimeoutException):
                    raise FetchError(f"timeout: {exc}") from exc
                raise FetchError(f"transport error: {exc}") from exc

            if 300 <= response.status_code < 400:
                await response.aclose()
                location = response.headers.get("location")
                if not location:
                    raise FetchError("redirect without location")
                redirects += 1
                if redirects > self._policy.max_redirects:
                    raise SSRFError(
                        f"too many redirects ({redirects} > {self._policy.max_redirects})"
                    )
                current = _resolve_location(current, location)
                continue

            if response.status_code != 200:
                await response.aclose()
                raise FetchError(f"non-200 status: {response.status_code}")

            content_type = response.headers.get("content-type")
            chunks: list[bytes] = []
            total = 0
            try:
                async for chunk in response.aiter_bytes(65536):
                    total += len(chunk)
                    if total > self._policy.max_response_bytes:
                        raise FetchError(
                            f"response exceeds max_response_bytes "
                            f"({self._policy.max_response_bytes})"
                        )
                    chunks.append(chunk)
            finally:
                await response.aclose()
            data = b"".join(chunks)
            return FetchResult(
                url=url,
                final_url=str(response.url),
                content_type=content_type,
                data=data,
                sha256=hashlib.sha256(data).hexdigest(),
                redirects=redirects,
                elapsed_ms=int((time.monotonic() - start) * 1000),
            )


def _precheck_ip_literal(host: str) -> None:
    """IP literals are checked directly (no resolution to hide behind);
    hostnames are checked at connect time against every A/AAAA record."""
    from ipaddress import ip_address

    from apps.research_proxy.ssrf_guard import check_address

    try:
        ip_address(host)
    except ValueError:
        return  # a hostname — the backend validates the resolved set
    check_address(host)


def _resolve_location(current: str, location: str) -> str:
    """Resolve a possibly-relative Location header; the result is
    re-validated by the guard on the next loop iteration."""
    from urllib.parse import urljoin, urlsplit

    absolute = urljoin(current, location)
    parts = urlsplit(absolute)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"redirect to a blocked scheme: {parts.scheme!r}")
    return absolute
