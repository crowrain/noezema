"""Scenario: sandbox runtime against a real container engine (T2.3, T2.23).

Skipped when no docker/podman engine is available (e.g. in CI runners
without a socket). The engine probe and the image build are session-scoped.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from packages.sandbox.runtime import SandboxError, sandbox_available
from tests.conftest import _runtime, _sealed

pytestmark = [pytest.mark.scenario]


@pytest.mark.asyncio
async def test_health_check(sandbox_image: str, tmp_path: Path) -> None:
    rt = _runtime(tmp_path, sandbox_image)
    await rt.health_check()  # raises on failure


@pytest.mark.asyncio
async def test_full_lifecycle_isolated(sandbox_image: str, tmp_path: Path) -> None:
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()

    base = tmp_path / "base"
    base.mkdir()
    (base / "corpus.txt").write_text("data")

    handle = await rt.start("11111111-2222-3333-4444-555555555555", base, profile)
    try:
        # python executes in the container
        res = await rt.exec(handle, ["bash", "-c", "python3 -c 'print(6*7)'"])
        assert res.exit_code == 0 and res.stdout.strip() == "42"

        # the base is readable
        res = await rt.exec(handle, ["bash", "-c", "cat /base/corpus.txt"])
        assert res.stdout.strip() == "data"

        # the base is read-only
        res = await rt.exec(handle, ["bash", "-c", "echo x > /base/evil"])
        assert res.exit_code != 0

        # network is unavailable
        res = await rt.exec(
            handle,
            ["bash", "-c", "python3 -c \"import socket; socket.create_connection(('1.1.1.1', 53), 3)\""],
            timeout=15,
        )
        assert res.exit_code != 0

        # writes land in the COW workspace only
        res = await rt.exec(handle, ["bash", "-c", "echo result > /workspace/out.txt && cat /workspace/corpus.txt"])
        assert res.exit_code == 0 and res.stdout.strip() == "data"

        # collected workspace is on the host
        collected = await rt.collect_workspace(handle)
        assert (collected / "out.txt").read_text().strip() == "result"
        assert (collected / "corpus.txt").read_text().strip() == "data"
    finally:
        await rt.destroy(handle)

    # the container is really gone
    rc = subprocess.run(
        ["docker", "inspect", handle.container_name], capture_output=True, timeout=30
    ).returncode
    assert rc != 0


@pytest.mark.asyncio
async def test_exec_timeout(sandbox_image: str, tmp_path: Path) -> None:
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    handle = await rt.start("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee", None, profile)
    try:
        res = await rt.exec(handle, ["bash", "-c", "sleep 30"], timeout=3)
        assert res.timed_out
        assert res.exit_code == 124
    finally:
        await rt.destroy(handle)


@pytest.mark.asyncio
async def test_start_failure_raises(tmp_path: Path) -> None:
    rt = _runtime(tmp_path, "noezema-sandbox:does-not-exist")
    with pytest.raises(SandboxError):
        await rt.start("ffffffff-0000-1111-2222-333333333333", None, _sealed())


def test_sandbox_available_is_callable() -> None:
    # just ensure the probe does not raise
    assert isinstance(sandbox_available("definitely-not-an-engine"), bool)
    assert shutil.which("definitely-not-an-engine") is None
