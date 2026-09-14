"""Orchestrator entry point: run one cognitive session (M1).

Usage: python -m apps.orchestrator
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.orchestrator import Orchestrator
from packages.domain.db.engine import DatabaseSettings
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile


async def _run() -> int:
    settings = DatabaseSettings()
    llm_config = LLMGatewayConfig()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    profile = ModelProfile(model_alias=llm_config.model, backend_name="local")
    gateway = LLMMiddleware(llm_config)
    orchestrator = Orchestrator(
        session_factory=factory,
        gateway=gateway,
        profile=profile,
        executor=StubToolExecutor(Path("/var/lib/noezema/workspace")),
    )
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
