"""T7.59(b): web bind comes from env and a public bind without an admin token is refused (§13.1–§13.2).

Pure resolver only — `apps/web/main.py` builds the app (side effects) and is exercised by
the manual stand run described in STATUS.md T7.59(b), not here.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.orchestrator.scheduler import data_root_from_env
from apps.web.bind import (
    DEFAULT_WEB_HOST,
    DEFAULT_WEB_PORT,
    EXIT_CONFIG_ERROR,
    WebBindError,
    is_loopback_host,
    resolve_standalone_workspace,
    resolve_web_bind,
)

TOKEN = "test-admin-token"


@pytest.mark.unit
def test_defaults_are_unchanged_loopback_and_8321() -> None:
    """No env → the historical bind. This is the invariant T7.59 must not break."""
    assert resolve_web_bind(None, None, None) == (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT)
    assert resolve_web_bind("", "", "") == ("127.0.0.1", 8321)
    assert (DEFAULT_WEB_HOST, DEFAULT_WEB_PORT) == ("127.0.0.1", 8321)


@pytest.mark.unit
def test_env_overrides_are_honoured_when_the_token_is_present() -> None:
    assert resolve_web_bind("192.168.1.92", "8321", TOKEN) == ("192.168.1.92", 8321)
    assert resolve_web_bind(" 0.0.0.0 ", " 8443 ", TOKEN) == ("0.0.0.0", 8443)


@pytest.mark.unit
@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost", "127.0.0.53"])
def test_loopback_bind_needs_no_token(host: str) -> None:
    assert resolve_web_bind(host, "8321", "") == (host, 8321)
    assert resolve_web_bind(host, None, None) == (host, DEFAULT_WEB_PORT)


@pytest.mark.unit
def test_public_bind_without_admin_token_is_refused() -> None:
    with pytest.raises(WebBindError) as excinfo:
        resolve_web_bind("192.168.1.92", "8321", "")
    message = str(excinfo.value)
    assert "NOEZEMA_ADMIN_TOKEN" in message
    assert "POST /api/v1/questions" in message and "POST /api/v1/commands" in message


@pytest.mark.unit
def test_whitespace_only_token_does_not_count_as_a_token() -> None:
    with pytest.raises(WebBindError):
        resolve_web_bind("0.0.0.0", "8321", "   ")


@pytest.mark.unit
@pytest.mark.parametrize("raw_port", ["abc", "83.1", "0", "-1", "65536"])
def test_malformed_or_out_of_range_port_is_refused(raw_port: str) -> None:
    with pytest.raises(WebBindError) as excinfo:
        resolve_web_bind("127.0.0.1", raw_port, TOKEN)
    assert "NOEZEMA_WEB_PORT" in str(excinfo.value)


@pytest.mark.unit
def test_host_with_whitespace_is_refused() -> None:
    with pytest.raises(WebBindError) as excinfo:
        resolve_web_bind("127.0.0.1 ; drop tables", "8321", TOKEN)
    assert "NOEZEMA_WEB_HOST" in str(excinfo.value)


@pytest.mark.unit
def test_loopback_classification_is_literal_not_prefix_matching() -> None:
    assert is_loopback_host("127.0.0.1") and is_loopback_host("::1")
    assert not is_loopback_host("0.0.0.0")
    assert not is_loopback_host("192.168.1.92")
    # a hostname that merely starts like a loopback literal is NOT loopback
    assert not is_loopback_host("127.example.com")


@pytest.mark.unit
def test_config_refusal_exit_code_is_the_fail_closed_one() -> None:
    """main.py exits with this code; 78 (EX_CONFIG) is what hostctl uses for config refusals."""
    assert EXIT_CONFIG_ERROR == 78


@pytest.mark.unit
def test_standalone_workspace_default_is_the_historical_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """No env → exactly the path the standalone web used before T7.59(b): behaviour unchanged."""
    monkeypatch.delenv("NOEZEMA_DATA_ROOT", raising=False)
    assert resolve_standalone_workspace(None) == Path("/var/lib/noezema/workspace")
    assert resolve_standalone_workspace("") == Path("/var/lib/noezema/workspace")


@pytest.mark.unit
def test_standalone_workspace_follows_noezema_data_root() -> None:
    """A stand runs as an ordinary user with its own data root; web and wake tick must agree."""
    assert resolve_standalone_workspace("/var/lib/noezema-dev") == Path("/var/lib/noezema-dev/workspace")


@pytest.mark.unit
def test_standalone_workspace_and_tick_use_one_data_root(monkeypatch: pytest.MonkeyPatch) -> None:
    """The resolved value comes from the scheduler's own helper, not from a second default."""
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", "/var/lib/noezema-dev")
    assert resolve_standalone_workspace(data_root_from_env()) == Path("/var/lib/noezema-dev/workspace")


@pytest.mark.unit
def test_relative_data_root_is_refused() -> None:
    with pytest.raises(WebBindError) as excinfo:
        resolve_standalone_workspace("tmp/dev-data")
    assert "NOEZEMA_DATA_ROOT" in str(excinfo.value)
