"""T7.61(б): the standalone research proxy puts its artifacts in the node's data root.

`python -m apps.research_proxy.main` used to hardcode `/var/lib/noezema/artifacts`. On a node with its own
data root (the dev stand runs as an unprivileged user over `/var/lib/noezema-dev`) that is both a
PermissionError and — worse — a write into the production contour path. The entry now derives the store from
`NOEZEMA_DATA_ROOT`, exactly like the wake tick, the web bind and the manual orchestrator entry: artifacts
stay the sibling of `<data root>/workspace`. Unset env → the historical path, behaviour unchanged.

The pure resolver is tested directly; `build_standalone_app()` is imported INSIDE the tests that set the data
root — importing the entry module builds the app (side effects), so it must never happen while the data root
still points at `/var/lib/noezema`.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.scheduler import ARTIFACTS_SUBDIR, WORKSPACE_SUBDIR, artifacts_root_from_env

pytestmark = [pytest.mark.unit]

#: A URL that is never connected to: these tests never touch a database.
NO_CONNECT_URL = "postgresql+asyncpg://noezema:noezema_dev@127.0.0.1/never_connected"


@pytest.mark.unit
def test_default_artifacts_root_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOEZEMA_DATA_ROOT", raising=False)
    assert artifacts_root_from_env() == Path("/var/lib/noezema/artifacts")


@pytest.mark.unit
def test_artifacts_root_follows_noezema_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path))
    assert artifacts_root_from_env() == tmp_path / ARTIFACTS_SUBDIR


def _build_app(monkeypatch: pytest.MonkeyPatch, data_root: Path) -> Any:
    """Env-driven entry build, the way `python -m apps.research_proxy.main` does it."""
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", NO_CONNECT_URL)

    from apps.research_proxy.main import build_standalone_app

    return build_standalone_app()


@pytest.mark.unit
def test_standalone_proxy_store_follows_the_env_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression itself: the proxy's artifact store is `<NOEZEMA_DATA_ROOT>/artifacts`."""
    app = _build_app(monkeypatch, tmp_path)

    service: Any = app.state.service
    assert Path(service.store.root) == tmp_path / ARTIFACTS_SUBDIR


@pytest.mark.unit
def test_standalone_proxy_artifacts_are_sibling_of_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """One data root per node: workspace and artifacts of the same stand sit side by side."""
    app = _build_app(monkeypatch, tmp_path)

    service: Any = app.state.service
    assert Path(service.store.root).parent == tmp_path
    assert (tmp_path / WORKSPACE_SUBDIR).parent == Path(service.store.root).parent


@pytest.mark.unit
def test_standalone_proxy_never_writes_into_the_production_path(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A stand data root under the operator's home must not drag artifacts into /var/lib/noezema."""
    app = _build_app(monkeypatch, tmp_path)

    service: Any = app.state.service
    root = Path(service.store.root)
    assert root != Path("/var/lib/noezema/artifacts")
    assert root.is_relative_to(tmp_path)
