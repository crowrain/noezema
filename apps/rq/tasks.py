"""RQ tasks — background jobs for Noezema orchestrator."""

import asyncio
import logging

from packages.domain.db_config import Database, DatabaseConfig, get_db_settings
from packages.llm_gateway.config import LLMGatewayConfig
from apps.orchestrator.orchestrator import Orchestrator

logger = logging.getLogger("noezema.rq")


def run_session_task(question_id: str | None = None) -> dict:
    """Run a single orchestrator session as an RQ background job.

    This is a sync wrapper around async orchestrator, because RQ jobs are synchronous.
    """
    import uuid as _uuid

    # Init DB
    db_settings = get_db_settings()
    Database.init(db_settings)

    # Init LLM
    llm_config = LLMGatewayConfig.from_env()
    orchestrator = Orchestrator(llm_config)

    try:
        async def _run():
            qid = _uuid.UUID(question_id) if question_id else None
            session_id = await orchestrator.run_session(question_id=qid)
            return str(session_id)

        session_id = asyncio.run(_run())
        logger.info("RQ session completed: %s", session_id)
        return {"status": "completed", "session_id": session_id}
    except Exception as e:
        logger.error("RQ session failed: %s", e, exc_info=True)
        return {"status": "failed", "error": str(e)}
    finally:
        asyncio.run(orchestrator.close())
        asyncio.run(Database.close())


def run_loop_forever() -> None:
    """Run orchestrator in a continuous loop (for systemd or standalone).

    Picks questions from queue, runs sessions, sleeps between loops.
    """
    import os
    import time

    # Init DB
    db_settings = get_db_settings()
    Database.init(db_settings)

    # Init LLM
    llm_config = LLMGatewayConfig.from_env()
    orchestrator = Orchestrator(llm_config)

    interval = int(os.environ.get("LOOP_INTERVAL", "30"))
    logger.info("RQ loop started — interval %ds", interval)

    try:
        while True:
            try:
                async def _run():
                    return await orchestrator.run_session()

                session_id = asyncio.run(_run())
                logger.info("Loop session: %s — sleeping %ds", session_id, interval)
                time.sleep(interval)
            except Exception as e:
                logger.error("Loop error: %s — retrying in %ds", e, interval, exc_info=True)
                time.sleep(interval)
    except (KeyboardInterrupt, SystemExit):
        logger.info("Loop stopped")
    finally:
        asyncio.run(orchestrator.close())
        asyncio.run(Database.close())
