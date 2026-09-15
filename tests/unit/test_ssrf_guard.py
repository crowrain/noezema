"""Unit: the SSRF guard of the research proxy (T6.1, §5.12).

The guard is the security core of the only egress: every address a
URL can lead to (directly or via DNS) must be a public unicast
address unless the operator explicitly allowlisted the endpoint.
These tests pin the full block list (private/loopback/link-local —
including the cloud metadata address — /reserved/multicast/unspecified,
IPv4 and IPv6, IPv4-mapped) and the fail-closed policy validation.
"""

from __future__ import annotations

import pytest

from apps.research_proxy.ssrf_guard import (
    METADATA_ADDRESS,
    SSRFError,
    SSRFPolicy,
    check_address,
    check_host,
    validate_url,
)

BLOCKED_ADDRESSES = [
    # loopback
    "127.0.0.1",
    "127.8.8.8",
    "::1",
    # RFC1918 private
    "10.0.0.1",
    "172.16.0.1",
    "172.31.255.255",
    "192.168.1.1",
    # link-local (the metadata endpoint is link-local)
    "169.254.169.254",
    "169.254.0.1",
    "fe80::1",
    # the canonical cloud metadata address, named explicitly
    METADATA_ADDRESS,
    # unspecified / broadcast / reserved
    "0.0.0.0",
    "::",
    "255.255.255.255",
    "240.0.0.1",
    # IPv6 ULA (fc00::/7)
    "fc00::1",
    "fd12:3456::1",
    # IPv4-mapped IPv6 — the v4 part decides
    "::ffff:127.0.0.1",
    "::ffff:10.0.0.1",
    # multicast
    "224.0.0.1",
    "ff02::1",
]


@pytest.mark.parametrize("addr", BLOCKED_ADDRESSES)
def test_blocked_addresses(addr: str) -> None:
    with pytest.raises(SSRFError):
        check_address(addr)


@pytest.mark.parametrize(
    "addr",
    [
        "93.184.216.34",
        "8.8.8.8",
        "2606:2800:220:1:248:1893:25c8:1946",
        "::ffff:8.8.8.8",  # mapped to a PUBLIC v4 address is fine
    ],
)
def test_public_addresses_allowed(addr: str) -> None:
    check_address(addr)  # must not raise


def test_unparseable_address_blocked() -> None:
    with pytest.raises(SSRFError):
        check_address("not-an-ip")


def test_validate_url_scheme_and_credentials() -> None:
    with pytest.raises(SSRFError):
        validate_url("ftp://example.com/")
    with pytest.raises(SSRFError):
        validate_url("file:///etc/passwd")
    with pytest.raises(SSRFError):
        validate_url("http://user:pw@example.com/")
    with pytest.raises(SSRFError):
        validate_url("http:///path-only")
    with pytest.raises(SSRFError):
        validate_url("")


def test_validate_url_default_and_explicit_ports() -> None:
    assert validate_url("http://example.com") == ("example.com", 80)
    assert validate_url("https://example.com/x") == ("example.com", 443)
    assert validate_url("https://example.com:8443/x") == ("example.com", 8443)


def test_check_host_requires_resolution() -> None:
    policy = SSRFPolicy.from_section({})
    with pytest.raises(SSRFError):
        check_host("no-such-host.invalid", 80, policy, [])


def test_check_host_blocks_mixed_public_private_answer() -> None:
    """A zone answering with one public and one private record is a
    rebinding vector — blocked wholesale."""
    policy = SSRFPolicy.from_section({})
    with pytest.raises(SSRFError):
        check_host("evil.example", 80, policy, ["93.184.216.34", "127.0.0.1"])


def test_check_host_blocks_private_answer_for_hostname() -> None:
    policy = SSRFPolicy.from_section({})
    with pytest.raises(SSRFError):
        check_host("internal.example", 80, policy, ["10.0.0.5"])


def test_check_host_allows_public_answer() -> None:
    policy = SSRFPolicy.from_section({})
    check_host("ok.example", 443, policy, ["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])


def test_private_allowlist_is_explicit_only() -> None:
    policy = SSRFPolicy.from_section(
        {"private_allowlist": ["127.0.0.1:8888", "searxng.local"]}
    )
    # registered: host:port and bare host
    assert policy.allows_private("127.0.0.1", 8888)
    assert policy.allows_private("searxng.local", 1234)
    # same host, unregistered port — NOT allowed
    assert not policy.allows_private("127.0.0.1", 8080)
    # an unregistered private host — NOT allowed
    assert not policy.allows_private("10.0.0.5", 80)


def test_allowlisted_private_answer_passes() -> None:
    policy = SSRFPolicy.from_section({"private_allowlist": ["searxng.local"]})
    check_host("searxng.local", 8080, policy, ["127.0.0.1"])  # must not raise


@pytest.mark.parametrize(
    "section",
    [
        "not-a-mapping",
        {"max_response_bytes": 0},
        {"max_response_bytes": "big"},
        {"max_redirects": -1},
        {"max_redirects": 11},
        {"timeout_seconds": 0},
        {"timeout_seconds": 500},
        {"user_agent": ""},
        {"private_allowlist": [3]},
        {"private_allowlist": "127.0.0.1"},
    ],
)
def test_policy_validation_fail_closed(section: object) -> None:
    with pytest.raises(SSRFError):
        SSRFPolicy.from_section(section)  # type: ignore[arg-type]


def test_policy_defaults() -> None:
    policy = SSRFPolicy.from_section({})
    assert policy.max_response_bytes == 1_048_576
    assert policy.max_redirects == 3
    assert policy.timeout_seconds == 10.0
    assert policy.user_agent == "noezema-research-proxy/1.0"
    assert policy.private_allowlist == ()
