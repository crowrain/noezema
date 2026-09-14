"""Security tests for the sandbox (T2.23): network off, no privilege
escalation, injection via tool arguments stays inside the container.

Skipped when no docker engine is available.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from packages.broker import SandboxToolBroker
from tests.conftest import _runtime, _sealed

pytestmark = [pytest.mark.scenario]

NO_NETWORK_PY = """
import socket
s = socket.socket()
s.settimeout(3)
s.connect(("1.1.1.1", 443))
print("CONNECTED")
"""

INJECTION_CMD = r'echo "a\"b; touch /tmp/INJECTED" && true'


@pytest.mark.asyncio
async def test_no_network_from_container(sandbox_image: str, tmp_path: Path) -> None:
    """The container runs with network: none — any outbound connection
    must fail (no proxy, no DNS, nothing)."""
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    base = tmp_path / "base"
    base.mkdir()

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)
        obs = await broker.execute("python.execute", {"code": NO_NETWORK_PY})
        assert not obs.ok
        assert "CONNECTED" not in obs.data.get("stdout", "")
    finally:
        await rt.destroy(handle)


@pytest.mark.asyncio
async def test_no_privilege_escalation(sandbox_image: str, tmp_path: Path) -> None:
    """non-root user, all capabilities dropped: setuid and cap
    operations must fail."""
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    base = tmp_path / "base"
    base.mkdir()

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)

        obs = await broker.execute("shell.execute", {"command": "id"})
        assert obs.ok
        # not root
        assert "uid=0" not in obs.data["stdout"]

        # all effective capabilities are dropped (cap-drop ALL)
        obs = await broker.execute(
            "shell.execute",
            {"command": "grep CapEff /proc/self/status"},
        )
        assert obs.ok
        cap_line = obs.data["stdout"].strip()
        assert cap_line.split()[-1] == "0000000000000000"

        # no-new-privileges: a setuid root binary cannot be gained from
        # the container user (the file is owned by the container user, so
        # the setuid bit is inert — what matters is CapEff == 0 above)
        obs = await broker.execute(
            "shell.execute",
            {"command": "su root -c id 2>&1 || true"},
        )
        assert "uid=0" not in obs.data["stdout"]
    finally:
        await rt.destroy(handle)


@pytest.mark.asyncio
async def test_injection_stays_inside_container(sandbox_image: str, tmp_path: Path) -> None:
    """Shell metacharacters in tool arguments execute ONLY inside the
    disposable container: nothing touches the host."""
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    base = tmp_path / "base"
    base.mkdir()

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)
        # the command runs in-container; the injection target is in the
        # container's own /tmp (the container is disposable)
        obs = await broker.execute("shell.execute", {"command": INJECTION_CMD})
        assert obs.ok, obs.error
        assert "a\"b" in obs.data["stdout"]  # the echo itself succeeded

        # the host is untouched
        assert not Path("/tmp/INJECTED").exists()

        # workspace writes with traversal-like names are rejected
        obs = await broker.execute(
            "workspace.write", {"path": "../../../etc/passwd", "content": "x"}
        )
        assert not obs.ok
        obs = await broker.execute(
            "workspace.write", {"path": "/workspace/../../outside.txt", "content": "x"}
        )
        assert not obs.ok
    finally:
        await rt.destroy(handle)


@pytest.mark.asyncio
async def test_read_only_rootfs_writes_fail(sandbox_image: str, tmp_path: Path) -> None:
    """The rootfs is read-only: writing outside /workspace and /tmp must
    fail inside the container."""
    rt = _runtime(tmp_path, sandbox_image)
    profile = _sealed()
    base = tmp_path / "base"
    base.mkdir()

    handle = await rt.start(str(uuid.uuid4()), base, profile)
    try:
        broker = SandboxToolBroker(handle, profile, rt)
        obs = await broker.execute("shell.execute", {"command": "touch /etc/pwned"})
        assert not obs.ok
        obs = await broker.execute("shell.execute", {"command": "touch /usr/bin/pwned"})
        assert not obs.ok
    finally:
        await rt.destroy(handle)
