"""Research proxy modes (T6.2, stage 5, §5.12.1).

Three modes, and they must not all be called "local":

- ``sealed`` — only the local index (the committed knowledge base);
  NO network egress at all;
- ``curated`` — SearXNG through the proxy: the query still goes to
  upstream search engines (and may disclose the research topic), so
  every upstream request is journaled and rate-limited;
- ``open_lab`` — experimental: fetch is restricted to a closed list of
  allowed domains and the sessions run under the separate ``open_lab``
  sandbox profile.

The mode policy is config-driven and fail-closed: a malformed section
is an error, never a wider access.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from apps.research_proxy.ssrf_guard import SSRFError, validate_url


class ResearchMode(StrEnum):
    SEALED = "sealed"
    CURATED = "curated"
    OPEN_LAB = "open_lab"


class ModeError(ValueError):
    """The research_proxy section is unusable (fail-closed)."""


#: the sandbox capability profile each mode runs under (the YAML files
#: in sandbox/policy/; the sandbox itself keeps network: none — egress
#: happens only through this proxy)
_MODE_PROFILES = {
    ResearchMode.SEALED: "sealed",
    ResearchMode.CURATED: "curated",
    ResearchMode.OPEN_LAB: "open_lab",
}


@dataclass(frozen=True)
class ModePolicy:
    """The resolved, validated egress policy for one mode."""

    mode: ResearchMode
    profile_name: str
    egress: bool
    searxng_url: str | None
    searxng_host: str | None
    allowed_domains: tuple[str, ...]
    rate_limit_max: int
    rate_limit_window_seconds: int

    def domain_allowed(self, host: str) -> bool:
        """Closed-list check for open_lab fetches: exact host or a
        subdomain of an allowed domain."""
        return any(host == domain or host.endswith("." + domain) for domain in self.allowed_domains)

    @classmethod
    def from_section(cls, section: Any) -> ModePolicy:
        if not isinstance(section, dict):
            raise ModeError("research_proxy section must be a mapping")

        mode_raw = section.get("mode", ResearchMode.SEALED.value)
        try:
            mode = ResearchMode(mode_raw)
        except ValueError as exc:
            raise ModeError(f"unknown research_proxy mode {mode_raw!r}") from exc

        def _positive_int(key: str, default: int) -> int:
            value = section.get(key, default)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ModeError(f"{key} must be an integer >= 1")
            return value

        rate_limit_max = _positive_int("rate_limit_max", 20)
        window = _positive_int("rate_limit_window_seconds", 3600)
        if window > 86_400:
            raise ModeError("rate_limit_window_seconds must be <= 86400")

        searxng_url: str | None = section.get("searxng_url")
        searxng_host: str | None = None
        if searxng_url is not None:
            if not isinstance(searxng_url, str):
                raise ModeError("searxng_url must be a string or null")
            try:
                host, _port = validate_url(searxng_url)
            except SSRFError as exc:
                raise ModeError(f"searxng_url: {exc}") from exc
            searxng_host = host

        allowed_raw = section.get("allowed_domains", [])
        if not isinstance(allowed_raw, list) or not all(
            isinstance(d, str) and d for d in allowed_raw
        ):
            raise ModeError("allowed_domains must be a list of non-empty strings")
        allowed_domains = tuple(allowed_raw)

        if mode is ResearchMode.SEALED:
            # sealed has no egress: backend options are IGNORED (not
            # errors) — a stale section must not fail-close the whole
            # proxy just because it carries leftovers from another mode
            egress = False
        elif mode is ResearchMode.CURATED:
            if searxng_url is None:
                raise ModeError("curated mode requires searxng_url")
            egress = True
        else:  # OPEN_LAB
            if not allowed_domains:
                raise ModeError("open_lab mode requires a non-empty allowed_domains list")
            egress = True

        return cls(
            mode=mode,
            profile_name=_MODE_PROFILES[mode],
            egress=egress,
            searxng_url=searxng_url if mode is ResearchMode.CURATED else None,
            searxng_host=searxng_host if mode is ResearchMode.CURATED else None,
            allowed_domains=allowed_domains if mode is ResearchMode.OPEN_LAB else (),
            rate_limit_max=rate_limit_max,
            rate_limit_window_seconds=window,
        )
