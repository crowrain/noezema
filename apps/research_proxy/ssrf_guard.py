"""SSRF guard for the research proxy (T6.1, stage 5, §5.12).

The research proxy is the ONLY egress to the network; the sandbox
stays without direct network access. The guard makes two decisions,
both fail-closed:

1. URL validation: only http/https, a resolvable host, a sane port;
2. address validation: every IP a hostname resolves to must be a
   public unicast address — private, loopback, link-local (including
   the cloud metadata address 169.254.169.254), reserved, multicast
   and unspecified ranges are blocked, for IPv4 and IPv6 alike
   (ULA fc00::/7, IPv4-mapped IPv6).

DNS rebinding is handled by pinning: the fetch connects to a
RESOLVED-and-VALIDATED address, never re-resolving the hostname
mid-connection (see ``SSRFSafeAsyncBackend``).

The ``private_allowlist`` is the single sanctioned exception: an
explicit list of ``host`` or ``host:port`` entries the operator may
register (e.g. a locally deployed SearXNG in Curated mode). Nothing
on the allowlist is implied — the default list is empty and a
hostname not on it must resolve to public addresses only.
"""

from __future__ import annotations

import ipaddress
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

#: the cloud metadata endpoint (AWS/GCP/Azure) — link-local, blocked
#: by the range check, named here because it is the canonical SSRF
#: target
METADATA_ADDRESS = "169.254.169.254"


class SSRFError(ValueError):
    """The URL or a resolved address violates the egress policy."""


@dataclass(frozen=True)
class SSRFPolicy:
    """The egress policy, validated fail-closed from the snapshot's
    ``research_proxy`` section (a NULL/absent section is "sealed" —
    see ``fetch`` — and never reaches the guard)."""

    max_response_bytes: int
    max_redirects: int
    timeout_seconds: float
    user_agent: str
    private_allowlist: tuple[str, ...] = ()

    @classmethod
    def from_section(cls, section: dict[str, Any] | None) -> SSRFPolicy:
        if not isinstance(section, dict):
            raise SSRFError(
                f"research_proxy section must be a mapping, got {type(section).__name__}"
            )
        max_bytes = section.get("max_response_bytes", 1_048_576)
        max_redirects = section.get("max_redirects", 3)
        timeout = section.get("timeout_seconds", 10)
        user_agent = section.get("user_agent", "noezema-research-proxy/1.0")
        allowlist = section.get("private_allowlist", [])
        if not isinstance(max_bytes, int) or isinstance(max_bytes, bool) or max_bytes < 1:
            raise SSRFError("max_response_bytes must be an int >= 1")
        if not isinstance(max_redirects, int) or isinstance(max_redirects, bool) or not 0 <= max_redirects <= 10:
            raise SSRFError("max_redirects must be an int in [0, 10]")
        if not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or not 0 < timeout <= 120:
            raise SSRFError("timeout_seconds must be a number in (0, 120]")
        if not isinstance(user_agent, str) or not 1 <= len(user_agent) <= 200:
            raise SSRFError("user_agent must be a string (1..200 chars)")
        if not isinstance(allowlist, list) or any(
            not isinstance(e, str) or not e for e in allowlist
        ):
            raise SSRFError("private_allowlist must be a list of non-empty strings")
        return cls(
            max_response_bytes=max_bytes,
            max_redirects=max_redirects,
            timeout_seconds=float(timeout),
            user_agent=user_agent,
            private_allowlist=tuple(allowlist),
        )

    def allows_private(self, host: str, port: int) -> bool:
        """The operator-registered exception (explicit only)."""
        candidates = {host}
        if any(":" in e for e in self.private_allowlist):
            candidates.add(f"{host}:{port}")
        return any(c in self.private_allowlist for c in candidates)


def validate_url(url: str) -> tuple[str, int]:
    """Parse + validate the URL. Returns (host, port).

    Fail-closed: only http/https; a non-empty host; no credentials in
    the URL; a port in the TCP range (default 80/443 per scheme)."""
    if not isinstance(url, str) or not url:
        raise SSRFError("url must be a non-empty string")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https"):
        raise SSRFError(f"blocked scheme: {parts.scheme!r} (only http/https)")
    if parts.username is not None or parts.password is not None:
        raise SSRFError("credentials in the URL are not allowed")
    host = parts.hostname
    if not host:
        raise SSRFError("missing host")
    port = parts.port
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    if not 1 <= port <= 65535:
        raise SSRFError(f"blocked port: {port}")
    return host, port


def check_address(ip_text: str) -> None:
    """Validate ONE resolved address. Raises SSRFError for anything
    that is not a public unicast address."""
    try:
        ip = ipaddress.ip_address(ip_text)
    except ValueError as exc:
        raise SSRFError(f"unparseable address {ip_text!r}") from exc
    # IPv4-mapped/compatible IPv6 (::ffff:10.0.0.1) — judge the v4 part
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if ip.is_private:
        raise SSRFError(f"blocked private address: {ip}")
    if ip.is_loopback:
        raise SSRFError(f"blocked loopback address: {ip}")
    if ip.is_link_local:
        raise SSRFError(f"blocked link-local address (incl. metadata): {ip}")
    if ip.is_reserved:
        raise SSRFError(f"blocked reserved address: {ip}")
    if ip.is_multicast:
        raise SSRFError(f"blocked multicast address: {ip}")
    if ip.is_unspecified:
        raise SSRFError(f"blocked unspecified address: {ip}")
    # IPv4 "site-local" (172.16/12 etc.) is already private; keep the
    # explicit check for clarity with older ipaddress semantics
    if isinstance(ip, ipaddress.IPv4Address) and ip.packed[:1] in (b"\x7f",):
        raise SSRFError(f"blocked loopback address: {ip}")


def check_host(host: str, port: int, policy: SSRFPolicy, resolved_ips: list[str]) -> None:
    """Validate a hostname + its RESOLVED addresses (all of them — a
    zone with one public and one private A record is a rebinding
    vector and is blocked wholesale). An explicitly allowlisted
    private endpoint skips the IP checks (the operator's choice)."""
    if not resolved_ips:
        raise SSRFError(f"hostname {host!r} did not resolve to any address")
    if policy.allows_private(host, port):
        return
    for ip_text in resolved_ips:
        check_address(ip_text)
