"""SSRF-safe network backend for the research proxy (T6.1, §5.12).

The stock anyio TCP backend of httpx/httpcore, wrapped so the SSRF
address check sits in the connection path:

- a hostname is resolved ONCE per connection, via the system
  resolver;
- EVERY resolved address is validated by the guard (a mixed
  public/private answer is blocked — DNS rebinding defense);
- the socket connects to a VALIDATED address (pinned), while TLS
  SNI + certificate verification keep using the original hostname
  (httpcore passes ``server_hostname`` separately from the TCP
  target).

IP literals are not re-resolved — they are checked directly by the
same guard, so ``http://169.254.169.254/`` is blocked at the address
level, not by any special-casing. Unix sockets are not an egress path.
"""

from __future__ import annotations

import socket
from collections.abc import Iterable

import anyio
from httpcore._backends.anyio import AnyIOBackend
from httpcore._backends.base import (
    SOCKET_OPTION,
    AsyncNetworkBackend,
    AsyncNetworkStream,
)
from httpcore._exceptions import ConnectError

from apps.research_proxy.ssrf_guard import SSRFError, SSRFPolicy, check_address


class SSRFSafeAsyncBackend(AsyncNetworkBackend):
    """The egress backend: resolve → validate → pin → connect."""

    def __init__(self, policy: SSRFPolicy) -> None:
        self._policy = policy
        self._inner = AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        if socket_options is None:
            socket_options = []
        # IP literal: validate it directly (no resolution to hide behind)
        from ipaddress import ip_address

        try:
            ip_address(host)
            target_ips = [host]
        except ValueError:
            # hostname: resolve once, validate EVERY record
            try:
                infos = await anyio.getaddrinfo(
                    host, port, type=socket.SOCK_STREAM
                )
            except OSError as exc:
                raise SSRFError(f"hostname {host!r} did not resolve: {exc}") from exc
            target_ips = [info[4][0] for info in infos]
        for ip_text in target_ips:
            if not self._policy.allows_private(host, port):
                check_address(ip_text)
        # pin the first validated address
        pinned = target_ips[0]
        try:
            return await self._inner.connect_tcp(
                pinned,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        except OSError as exc:
            raise ConnectError(str(exc)) from exc

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> AsyncNetworkStream:
        raise SSRFError("unix sockets are not an egress path")
