"""Build the production runtime from validated host-owned configuration."""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from typing import cast

from sqlalchemy import Engine
from sqlalchemy.orm import Session, sessionmaker

from apps.orchestrator import AutonomousSessionRunner, AutonomousSupervisor
from apps.runtime.config import RuntimeConfig
from packages.cognition import PromptBundle, PromptKind, PromptSnapshot
from packages.domain import sealed_mvp_capability_policy
from packages.llm_gateway import LLMGateway, OpenAICompatibleTransport
from packages.persistence import create_session_factory
from packages.tool_broker import ToolBroker


@dataclass(slots=True)
class RuntimeComponents:
    engine: Engine
    transport: OpenAICompatibleTransport
    supervisor: AutonomousSupervisor

    def close(self) -> None:
        self.transport.close()
        self.engine.dispose()

    def __enter__(self) -> RuntimeComponents:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


@dataclass(frozen=True, slots=True)
class _ResourceProbe:
    transport: OpenAICompatibleTransport
    disk_path: str
    min_free_disk_bytes: int

    def __call__(self) -> bool:
        try:
            free_bytes = shutil.disk_usage(self.disk_path).free
        except OSError:
            return False
        return free_bytes >= self.min_free_disk_bytes and self.transport.is_ready()


def build_runtime(config: RuntimeConfig, *, owner: str) -> RuntimeComponents:
    workspace_root = config.workspace_root.resolve(strict=True)
    if not workspace_root.is_dir():
        raise ValueError("NOEZEMA_WORKSPACE_ROOT must be a directory")
    artifact_store_root = config.artifact_store_root.resolve(strict=False)
    if artifact_store_root.is_relative_to(workspace_root):
        raise ValueError("artifact store must be outside the model-visible workspace")
    artifact_store_root.mkdir(parents=True, exist_ok=True)

    engine, raw_factory = create_session_factory(config.database_url)
    session_factory = cast(sessionmaker[Session], raw_factory)
    transport = OpenAICompatibleTransport(
        profile=config.model_profile,
        config=config.transport_config,
    )
    try:
        gateway = LLMGateway(profile=config.model_profile, transport=transport)
        identity = PromptSnapshot.load(
            config.prompt_root / "identity.md",
            kind=PromptKind.IDENTITY,
            version="identity/v1",
            expected_sha256=config.identity_prompt_sha256,
        )
        explorer_prompts = PromptBundle(
            identity=identity,
            role=PromptSnapshot.load(
                config.prompt_root / "explorer.md",
                kind=PromptKind.EXPLORER,
                version="explorer/v1",
                expected_sha256=config.explorer_prompt_sha256,
            ),
        )
        curator_prompts = PromptBundle(
            identity=identity,
            role=PromptSnapshot.load(
                config.prompt_root / "curator.md",
                kind=PromptKind.CURATOR,
                version="curator/v1",
                expected_sha256=config.curator_prompt_sha256,
            ),
        )
        broker = ToolBroker(
            session_factory=session_factory,
            workspace_root=workspace_root,
            artifact_store_root=artifact_store_root,
            policy=sealed_mvp_capability_policy(),
            lease_owner=owner,
        )
        runner = AutonomousSessionRunner(
            session_factory=session_factory,
            gateway=gateway,
            tool_broker=broker,
            explorer_prompts=explorer_prompts,
            curator_prompts=curator_prompts,
            lease_owner=owner,
            limits=config.runner_limits,
        )
        supervisor = AutonomousSupervisor(
            session_factory=session_factory,
            runner=runner,
            owner=owner,
            policy=config.supervisor_policy,
            resources_ready=_ResourceProbe(
                transport=transport,
                disk_path=str(artifact_store_root),
                min_free_disk_bytes=config.min_free_disk_bytes,
            ),
        )
    except Exception:
        transport.close()
        engine.dispose()
        raise
    return RuntimeComponents(engine=engine, transport=transport, supervisor=supervisor)
