"""Unit: research proxy modes (T6.2, stage 5, §5.12.1).

The mode policy is fail-closed: a malformed section is an error, never
a wider access. The three modes are distinct — sealed has no egress,
curated requires the SearXNG endpoint, open_lab requires the closed
domain list — and each runs under its own sandbox profile.
"""

from __future__ import annotations

import pytest

from apps.research_proxy.modes import (
    ModeError,
    ModePolicy,
    ResearchMode,
)


def test_sealed_defaults_and_no_egress() -> None:
    policy = ModePolicy.from_section({"mode": "sealed"})
    assert policy.mode is ResearchMode.SEALED
    assert policy.egress is False
    assert policy.profile_name == "sealed"
    assert policy.searxng_url is None
    assert policy.allowed_domains == ()


def test_curated_requires_searxng_url() -> None:
    with pytest.raises(ModeError):
        ModePolicy.from_section({"mode": "curated"})
    policy = ModePolicy.from_section(
        {"mode": "curated", "searxng_url": "http://searxng.local:8888"}
    )
    assert policy.mode is ResearchMode.CURATED
    assert policy.egress is True
    assert policy.profile_name == "curated"
    assert policy.searxng_url == "http://searxng.local:8888"
    assert policy.searxng_host == "searxng.local"


def test_open_lab_requires_allowed_domains() -> None:
    with pytest.raises(ModeError):
        ModePolicy.from_section({"mode": "open_lab"})
    policy = ModePolicy.from_section(
        {"mode": "open_lab", "allowed_domains": ["docs.python.org"]}
    )
    assert policy.mode is ResearchMode.OPEN_LAB
    assert policy.egress is True
    assert policy.profile_name == "open_lab"
    assert policy.allowed_domains == ("docs.python.org",)


def test_profiles_are_separate() -> None:
    sections = {
        "sealed": {"mode": "sealed"},
        "curated": {"mode": "curated", "searxng_url": "http://searxng.local:8888"},
        "open_lab": {"mode": "open_lab", "allowed_domains": ["example.com"]},
    }
    names = {name: ModePolicy.from_section(sec).profile_name for name, sec in sections.items()}
    assert names == {"sealed": "sealed", "curated": "curated", "open_lab": "open_lab"}


def test_domain_allowlist_is_closed() -> None:
    policy = ModePolicy.from_section(
        {"mode": "open_lab", "allowed_domains": ["example.com", "docs.py.org"]}
    )
    assert policy.domain_allowed("example.com")
    assert policy.domain_allowed("wiki.example.com")  # subdomain
    assert not policy.domain_allowed("evil-example.com")  # no suffix trick
    assert not policy.domain_allowed("example.com.evil.net")
    assert not policy.domain_allowed("py.org")


def test_rate_limit_bounds_fail_closed() -> None:
    for bad in (0, -1, "20", True):
        with pytest.raises(ModeError):
            ModePolicy.from_section({"mode": "sealed", "rate_limit_max": bad})  # type: ignore[dict-item]
    with pytest.raises(ModeError):
        ModePolicy.from_section({"mode": "sealed", "rate_limit_window_seconds": 0})
    with pytest.raises(ModeError):
        ModePolicy.from_section({"mode": "sealed", "rate_limit_window_seconds": 100_000})


def test_unknown_mode_fail_closed() -> None:
    with pytest.raises(ModeError):
        ModePolicy.from_section({"mode": "yolo"})
    with pytest.raises(ModeError):
        ModePolicy.from_section(None)  # type: ignore[arg-type]


def test_sealed_ignores_backend_options() -> None:
    """Stale backend options do not widen access: sealed stays
    egress-less, the options are simply not used."""
    policy = ModePolicy.from_section(
        {"mode": "sealed", "searxng_url": "http://x:8888",
         "allowed_domains": ["example.com"]}
    )
    assert policy.egress is False
    assert policy.searxng_url is None
    assert policy.allowed_domains == ()


def test_bad_searxng_url_fail_closed() -> None:
    with pytest.raises(ModeError):
        ModePolicy.from_section(
            {"mode": "curated", "searxng_url": "ftp://searxng.local"}
        )
    with pytest.raises(ModeError):
        ModePolicy.from_section(
            {"mode": "curated", "searxng_url": "http://user:pw@x/"}
        )
