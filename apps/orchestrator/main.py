"""Orchestrator entry point: run one cognitive session (M1).

Usage: python -m apps.orchestrator
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.engine import DatabaseSettings
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile


def build_orchestrator(
    session_factory: async_sessionmaker[AsyncSession], workspace_root: Path
) -> tuple[Orchestrator, LLMMiddleware]:
    """Assemble gateway + profile + executor + orchestrator (T3.29).

    Shared by the manual entry point and the wake tick so both run the
    exact same session pipeline. The caller closes the gateway.
    """
    llm_config = LLMGatewayConfig()
    profile = ModelProfile(model_alias=llm_config.model, backend_name="local")
    gateway = LLMMiddleware(llm_config)
    orchestrator = Orchestrator(
        session_factory=session_factory,
        gateway=gateway,
        profile=profile,
        executor=StubToolExecutor(workspace_root),
    )
    return orchestrator, gateway


async def _run() -> int:
    settings = DatabaseSettings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orchestrator, gateway = build_orchestrator(factory, Path("/var/lib/noezema/workspace"))
    try:
        outcome = await orchestrator.run_session()
    finally:
        await gateway.close()
        await engine.dispose()
    print(
        f"session {outcome.session_id} -> {outcome.final_state.value} "
        f"steps={outcome.steps} evidence={outcome.evidence_count} "
        f"reason={outcome.termination_reason}"
    )
    return 0 if outcome.final_state.value in ("succeeded", "succeeded_partial") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
