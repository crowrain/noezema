"""T7.59(в): every host session entry point puts its workspace in the node's data root.

`python -m apps.orchestrator` (the manual entry) used to hardcode `/var/lib/noezema/workspace`. On a
dev-stand VM, where the units run as an ordinary user with `NOEZEMA_DATA_ROOT=/var/lib/noezema-dev`,
that is a PermissionError before a session even starts (and it pinned the artifacts next to a
production path). The wake tick already took its root from the env; the manual entry now takes the
same one. Unset env → the historical path, behaviour unchanged.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid
from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.main import _run, build_orchestrator
from apps.orchestrator.scheduler import WORKSPACE_SUBDIR, workspace_root_from_env
from packages.domain.models.enums import SessionState

pytestmark = [pytest.mark.unit]

#: A URL that is never connected to: these tests never touch a database.
NO_CONNECT_URL = "postgresql+asyncpg://noezema:noezema_dev@127.0.0.1/never_connected"


@pytest.mark.unit
def test_default_data_root_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("NOEZEMA_DATA_ROOT", raising=False)
    assert workspace_root_from_env() == Path("/var/lib/noezema/workspace")


@pytest.mark.unit
def test_workspace_follows_noezema_data_root(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path))
    assert workspace_root_from_env() == tmp_path / WORKSPACE_SUBDIR


@dataclasses.dataclass(frozen=True)
class _Outcome:
    session_id: uuid.UUID
    final_state: SessionState
    steps: int
    evidence_count: int
    termination_reason: str | None


class _FakeOrchestrator:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root

    async def run_session(self) -> _Outcome:
        return _Outcome(
            session_id=uuid.uuid4(),
            final_state=SessionState.SUCCEEDED,
            steps=1,
            evidence_count=0,
            termination_reason="goal_reached",
        )


class _FakeGateway:
    async def close(self) -> None:
        return None


class _FakeGuard:
    """Stand-in for the T7.61(а) node session lane.

    `NodeSessionGuard.acquire` opens a real DB connection, and these tests deliberately never connect to a
    database (NO_CONNECT_URL): they are about the workspace/artifacts path only. Replacing the collaborator
    keeps that focus; the guard's own behaviour is tested in `tests/scenario/test_node_session_exclusion.py`
    against a scratch DB.
    """

    def __init__(self, target: object, node_owner: str) -> None:
        self.node_owner = node_owner

    async def acquire(self) -> bool:
        return True

    async def release(self) -> None:
        return None


def _capture_build(monkeypatch: pytest.MonkeyPatch, captured: dict[str, Path]) -> None:
    def fake_build(session_factory: object, workspace_root: Path) -> tuple[object, _FakeGateway]:
        captured["workspace_root"] = workspace_root
        return _FakeOrchestrator(workspace_root), _FakeGateway()

    monkeypatch.setattr("apps.orchestrator.main.build_orchestrator", fake_build)
    monkeypatch.setattr("apps.orchestrator.main.NodeSessionGuard", _FakeGuard)


@pytest.mark.unit
def test_manual_entry_uses_the_env_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression itself: `_run` builds the orchestrator on `<NOEZEMA_DATA_ROOT>/workspace`."""
    captured: dict[str, Path] = {}
    _capture_build(monkeypatch, captured)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", NO_CONNECT_URL)

    assert asyncio.run(_run()) == 0
    assert captured["workspace_root"] == tmp_path / WORKSPACE_SUBDIR


@pytest.mark.unit
def test_manual_entry_default_is_the_historical_workspace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """No env → the path used before T7.59(в): existing runs and tests are untouched."""
    captured: dict[str, Path] = {}
    _capture_build(monkeypatch, captured)
    monkeypatch.delenv("NOEZEMA_DATA_ROOT", raising=False)
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", NO_CONNECT_URL)

    assert asyncio.run(_run()) == 0
    assert captured["workspace_root"] == Path("/var/lib/noezema/workspace")


@pytest.mark.unit
def test_workspace_and_artifacts_stay_in_one_data_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """`build_orchestrator` keeps the artifact store next to the workspace root, so moving the data
    root moves both — nothing is written under a production path by accident."""
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(tmp_path))

    async def _check() -> tuple[Path, Path]:
        engine = create_async_engine(NO_CONNECT_URL)  # never connected to
        try:
            factory = async_sessionmaker(engine, expire_on_commit=False)
            orchestrator, gateway = build_orchestrator(factory, workspace_root_from_env())
            workspace = Path(orchestrator.executor.workspace_dir)  # type: ignore[attr-defined]
            artifacts = Path(orchestrator.research_service.store.root)  # type: ignore[attr-defined]
            await gateway.close()
            return workspace, artifacts
        finally:
            await engine.dispose()

    workspace, artifacts = asyncio.run(_check())
    assert workspace == tmp_path / WORKSPACE_SUBDIR
    assert artifacts == tmp_path / "artifacts"
