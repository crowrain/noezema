"""Tests for sandbox capability profiles (T2.1/T2.2)."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from packages.sandbox.runtime import BASE_PATH, WORKSPACE_PATH, SandboxError, SandboxProfile

POLICY_DIR = Path(__file__).resolve().parents[2] / "sandbox" / "policy"


@pytest.mark.unit
@pytest.mark.parametrize("name", ["sealed", "curated", "open_lab"])
def test_profiles_load(name: str) -> None:
    profile = SandboxProfile.from_yaml(POLICY_DIR / f"{name}.yaml")
    assert profile.name == name
    assert profile.network == "none"
    assert profile.read_only_rootfs is True
    assert profile.no_new_privileges is True
    assert profile.capabilities == []
    assert profile.secrets == "deny"
    assert profile.metadata_endpoints == "deny"
    assert WORKSPACE_PATH in profile.paths["writable"]
    assert BASE_PATH in profile.paths["readable"]


@pytest.mark.unit
def test_sealed_tools() -> None:
    profile = SandboxProfile.from_yaml(POLICY_DIR / "sealed.yaml")
    for tool in ("workspace.read", "workspace.list", "workspace.write", "python.execute", "shell.execute"):
        assert profile.tool_allowed(tool), tool
    assert not profile.tool_allowed("memory.search")
    assert not profile.tool_allowed("net.fetch")  # unknown tool -> denied by default


@pytest.mark.unit
def test_sealed_limits() -> None:
    profile = SandboxProfile.from_yaml(POLICY_DIR / "sealed.yaml")
    assert profile.cpu == "1.0"
    assert profile.memory_mb == 512
    assert profile.max_processes == 32
    assert profile.timeout_seconds == 60
    assert profile.tmpfs_size_mb >= 64


@pytest.mark.unit
def test_unknown_key_rejected(tmp_path: Path) -> None:
    p = tmp_path / "bad.yaml"
    p.write_text(yaml.safe_dump({"name": "bad", "network": "none", "sudo": True}))
    with pytest.raises(SandboxError, match="unknown keys"):
        SandboxProfile.from_yaml(p)


@pytest.mark.unit
def test_secrets_must_be_deny(tmp_path: Path) -> None:
    p = tmp_path / "secrets.yaml"
    p.write_text(yaml.safe_dump({"name": "sneaky", "network": "none", "secrets": "allow"}))
    with pytest.raises(SandboxError, match="secrets"):
        SandboxProfile.from_yaml(p)


@pytest.mark.unit
def test_default_profile_is_deny_everything() -> None:
    profile = SandboxProfile(name="empty")
    assert not profile.tool_allowed("anything.at.all")
    assert profile.network == "none"
