"""Entry point — run one session."""

from __future__ import annotations

import asyncio
from datetime import datetime

from packages.llm_gateway.config import LLMGatewayConfig
from apps.orchestrator.orchestrator import Orchestrator


async def main():
    config = LLMGatewayConfig(
        base_url="http://localhost:8080/v1",
        model="qwen3.6-35b",
    )

    orch = Orchestrator(llm_config=config)

    # Seed initial question
    await orch.selector.create_seeded(
        "Какой минимальный набор инструментов нужен автономному агенту для продуктивного исследования?"
    )

    # Run one session
    session_id = await orch.run_session()
    print(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] Session completed: {session_id}")

    await orch.close()


if __name__ == "__main__":
    asyncio.run(main())
