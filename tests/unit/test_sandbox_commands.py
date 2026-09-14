"""Tests for the sandbox command builders (T2.3) — the security flags."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.sandbox.runtime import ContainerSandboxRuntime, SandboxProfile


@pytest.mark.unit
def _profile() -> SandboxProfile:
    return SandboxProfile(
        name="sealed",
        network="none",
        read_only_rootfs=True,
        no_new_privileges=True,
        resource_limits={
            "cpu": "1.0",
            "memory_mb": 512,
            "max_processes": 32,
            "timeout_seconds": 60,
            "max_file_size_mb": 50,
        },
    )


@pytest.mark.unit
def test_create_command_has_all_security_flags() -> None:
    base = Path("/tmp/base")
    work = Path("/tmp/work")
    cmd = ContainerSandboxRuntime.create_command("noezema-sb-x", "img", _profile(), base, work)

    assert cmd[0] == "create"
    assert "--name" in cmd and "noezema-sb-x" in cmd
    # network isolation
    assert cmd[cmd.index("--network") + 1] == "none"
    # read-only rootfs
    assert "--read-only" in cmd
    # capabilities
    assert cmd[cmd.index("--cap-drop") + 1] == "ALL"
    # no-new-privileges
    assert cmd[cmd.index("--security-opt") + 1] == "no-new-privileges"
    # resource limits
    assert cmd[cmd.index("--memory") + 1] == "512m"
    assert cmd[cmd.index("--cpus") + 1] == "1.0"
    assert cmd[cmd.index("--pids-limit") + 1] == "32"
    # scratch /tmp so the read-only rootfs is usable
    tmpfs = cmd[cmd.index("--tmpfs") + 1]
    assert tmpfs.startswith("/tmp:rw,size=") and "mode=1777" in tmpfs
    # mounts: rw workspace bind + ro base bind
    mounts = [cmd[i + 1] for i, x in enumerate(cmd) if x == "--mount"]
    assert f"type=bind,src={work},dst=/workspace" in mounts
    assert f"type=bind,src={base},dst=/base,ro" in mounts
    # init copies base -> workspace, then idles
    assert cmd[-1].startswith("cp -a /base/. /workspace/")
    assert "sleep infinity" in cmd[-1]


@pytest.mark.unit
def test_create_command_optional_flags() -> None:
    base = Path("/tmp/base")
    permissive = _profile().model_copy(update={"read_only_rootfs": False, "no_new_privileges": False})
    cmd = ContainerSandboxRuntime.create_command("n", "img", permissive, base, Path("/tmp/work"))
    assert "--read-only" not in cmd
    assert "--security-opt" not in cmd


@pytest.mark.unit
def test_exec_command_wraps_timeout() -> None:
    cmd = ContainerSandboxRuntime.exec_command("n", ["bash", "-c", "echo hi"], 30.0)
    assert cmd[:2] == ["exec", "n"]
    assert cmd[2] == "timeout"
    assert "30.0" in cmd
    assert cmd[-1] == "echo hi"


@pytest.mark.unit
def test_destroy_command() -> None:
    assert ContainerSandboxRuntime.destroy_command("n") == ["rm", "-f", "n"]
