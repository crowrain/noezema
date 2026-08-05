"""Noezema Orchestrator — CLI entry point.

Usage:
    python -m apps.orchestrator.main [--config path] [--once]

Runs the orchestrator loop: pick unresolved question → explore → assess → commit.
With --once: run a single session and exit.
Without: continuous loop with heartbeat.
"""

import argparse
import asyncio
import logging
import signal
import sys

from packages.domain.db_config import Database, DatabaseConfig
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.client import LLMMiddleware
from packages.cognition.question_selector import FIFOQuestionSelector
from packages.tool_broker.sandbox import SandboxExecutor
from packages.memory.rules_engine import RulesEngine
from packages.domain.services.curator_service import CuratorService
from apps.orchestrator.orchestrator import Orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("noezema.orchestrator")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Noezema Orchestrator")
    parser.add_argument(
        "--config",
        default=None,
        help="Path to YAML config (db.url, llm.base_url, etc.)",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run a single session then exit",
    )
    parser.add_argument(
        "--db-url",
        default="postgresql+asyncpg://noezema:noezema_dev@localhost:5432/noezema",
        help="Database URL",
    )
    parser.add_argument(
        "--llm-base-url",
        default="http://localhost:8080/v1",
        help="LLM OpenAI-compatible base URL",
    )
    parser.add_argument(
        "--llm-model",
        default="qwen3.6-27b-q6",
        help="Model name",
    )
    parser.add_argument(
        "--loop-interval",
        type=int,
        default=30,
        help="Seconds between loops (default: 30)",
    )
    return parser.parse_args()


async def run_once(orchestrator: Orchestrator) -> None:
    """Run a single session and exit."""
    session_id = await orchestrator.run_session()
    log.info("Session completed: %s", session_id)


async def run_loop(orchestrator: Orchestrator, interval: int) -> None:
    """Continuous loop with graceful shutdown."""
    stop_event = asyncio.Event()

    def handle_signal():
        log.info("Signal received, finishing current session...")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_signal)

    while not stop_event.is_set():
        try:
            session_id = await orchestrator.run_session()
            log.info("Session completed: %s — sleeping %ds", session_id, interval)
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
        except Exception as e:
            log.error("Session failed: %s — retrying in %ds", e, interval, exc_info=True)
            await asyncio.wait_for(stop_event.wait(), timeout=interval)

    log.info("Orchestrator stopped.")


async def main() -> None:
    args = parse_args()

    # Init DB from env or CLI args
    db_config = DatabaseConfig(url=args.db_url)
    Database.init(db_config)
    log.info("Database initialized: %s", args.db_url.replace("noezema_dev", "***"))

    # Init LLM from env or CLI args (CLI overrides env)
    llm_config = LLMGatewayConfig.from_env()
    if args.llm_base_url != "http://localhost:8080/v1":
        llm_config.base_url = args.llm_base_url
    if args.llm_model != "qwen3.6-27b-q6":
        llm_config.model = args.llm_model

    log.info("LLM: %s @ %s", llm_config.model, llm_config.base_url)

    orchestrator = Orchestrator(llm_config)

    try:
        if args.once:
            await run_once(orchestrator)
        else:
            await run_loop(orchestrator, args.loop_interval)
    finally:
        await orchestrator.close()


if __name__ == "__main__":
    asyncio.run(main())
