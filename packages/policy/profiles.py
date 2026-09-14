"""Capability profiles (T2.4, §11.2, §5.6).

Rights come from the profile, never from the content of the context: an
external page, a message or a past artifact can never grant the model a
new tool, path or network capability. The profile is loaded from
sandbox/policy/<name>.yaml (closed schema) and is the *ceiling*; the
config snapshot selects within that ceiling and can never widen it.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packages.sandbox.runtime import SandboxProfile

POLICY_DIR = Path(__file__).resolve().parents[2] / "sandbox" / "policy"


class NetworkMode(StrEnum):
    NONE = "none"
    RESEARCH_PROXY = "research_proxy"  # M6


class ProfileError(ValueError):
    pass


@dataclass(frozen=True)
class CapabilityProfile:
    name: str
    version: str
    tools: frozenset[str]
    writable_paths: tuple[str, ...]
    readable_paths: tuple[str, ...]
    network: NetworkMode
    command_timeout_seconds: int
    workspace_quota_mb: int
    secrets: str  # always "deny"
    metadata_endpoints: str  # always "deny"

    @property
    def policy_version(self) -> str:
        return f"{self.name}-{self.version}"

    def tool_allowed(self, tool: str) -> bool:
        return tool in self.tools


def load_profile(name: str, policy_dir: Path | None = None) -> CapabilityProfile:
    d = policy_dir if policy_dir is not None else POLICY_DIR
    sp = SandboxProfile.from_yaml(d / f"{name}.yaml")
    return CapabilityProfile(
        name=sp.name,
        version=sp.version,
        tools=frozenset(t for t, ok in sp.tools.items() if ok),
        writable_paths=tuple(sp.paths.get("writable", ())),
        readable_paths=tuple(sp.paths.get("readable", ())),
        network=NetworkMode(sp.network),
        command_timeout_seconds=sp.timeout_seconds,
        workspace_quota_mb=sp.max_file_size_mb,
        secrets=sp.secrets,
        metadata_endpoints=sp.metadata_endpoints,
    )


def effective_profile(snapshot_policy: dict[str, Any]) -> CapabilityProfile:
    """The effective profile for a session: the YAML ceiling for the
    snapshot's access profile, intersected with the snapshot's tool list.

    Fail-closed: a snapshot may only NARROW the profile's grants.
    """
    access_profile = str(snapshot_policy.get("access_profile", "sealed"))
    ceiling = load_profile(access_profile)

    caps = snapshot_policy.get("capabilities", {}) or {}
    snapshot_tools = set(caps.get("tools", []))
    if not snapshot_tools <= set(ceiling.tools):
        extra = sorted(snapshot_tools - set(ceiling.tools))
        raise ProfileError(
            f"config snapshot grants tools outside the '{access_profile}' ceiling: {extra}"
        )

    write_paths = tuple(caps.get("write_paths", list(ceiling.writable_paths)))
    for p in write_paths:
        if p not in ceiling.writable_paths:
            raise ProfileError(f"config snapshot grants write path outside ceiling: {p}")

    return CapabilityProfile(
        name=ceiling.name,
        version=ceiling.version,
        tools=frozenset(snapshot_tools),
        writable_paths=tuple(p for p in ceiling.writable_paths if p in write_paths),
        readable_paths=ceiling.readable_paths,
        network=ceiling.network,
        command_timeout_seconds=ceiling.command_timeout_seconds,
        workspace_quota_mb=ceiling.workspace_quota_mb,
        secrets=ceiling.secrets,
        metadata_endpoints=ceiling.metadata_endpoints,
    )
