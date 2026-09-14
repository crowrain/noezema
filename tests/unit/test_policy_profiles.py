"""Tests for capability profiles (T2.4)."""

from __future__ import annotations

import pytest

from packages.policy.profiles import (
    NetworkMode,
    ProfileError,
    effective_profile,
    load_profile,
)


@pytest.mark.unit
@pytest.mark.parametrize("name", ["sealed", "curated", "open_lab"])
def test_load_profile(name: str) -> None:
    p = load_profile(name)
    assert p.name == name
    assert p.version == "v1"
    assert p.policy_version == f"{name}-v1"
    assert p.network is NetworkMode.NONE
    assert p.secrets == "deny"
    assert p.metadata_endpoints == "deny"
    assert "/workspace" in p.writable_paths
    assert len(p.tools) > 0


@pytest.mark.unit
def test_sealed_ceiling() -> None:
    p = load_profile("sealed")
    assert p.tool_allowed("python.execute")
    assert p.tool_allowed("shell.execute")
    assert not p.tool_allowed("web.fetch")
    assert p.command_timeout_seconds == 60


@pytest.mark.unit
def test_effective_profile_narrows() -> None:
    snapshot_policy = {
        "access_profile": "sealed",
        "capabilities": {
            "tools": ["workspace.read", "python.execute"],
            "network": "none",
            "write_paths": ["/workspace"],
        },
    }
    p = effective_profile(snapshot_policy)
    assert p.tool_allowed("python.execute")
    assert not p.tool_allowed("shell.execute")  # narrowed by the snapshot
    assert p.writable_paths == ("/workspace",)


@pytest.mark.unit
def test_effective_profile_cannot_widen_tools() -> None:
    snapshot_policy = {
        "access_profile": "sealed",
        "capabilities": {"tools": ["web.fetch"]},
    }
    with pytest.raises(ProfileError, match="outside the"):
        effective_profile(snapshot_policy)


@pytest.mark.unit
def test_effective_profile_cannot_widen_paths() -> None:
    snapshot_policy = {
        "access_profile": "sealed",
        "capabilities": {"tools": ["workspace.write"], "write_paths": ["/etc"]},
    }
    with pytest.raises(ProfileError, match="write path"):
        effective_profile(snapshot_policy)


@pytest.mark.unit
def test_unknown_profile_fails_closed() -> None:
    with pytest.raises((ProfileError, FileNotFoundError)):
        effective_profile({"access_profile": "does-not-exist"})
