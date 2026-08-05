"""FastAPI app — status page, timeline API, message inbox, operator commands.

DB-aware: connects to PostgreSQL via SQLAlchemy on startup.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from apps.web.db import init_db, create_tables, shutdown_db
from apps.web.routes import router

logger = logging.getLogger("noezema.web")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """App lifecycle: init DB on startup, dispose on shutdown."""
    logger.info("Web API starting — initializing DB")
    await init_db()

    # Auto-create tables in dev (use Alembic in prod)
    import os
    if os.environ.get("NOEZEMA_ENV", "dev") == "dev":
        await create_tables()
        logger.info("Tables created/verified")

    yield

    await shutdown_db()
    logger.info("Web API shutdown — DB disposed")


app = FastAPI(
    title="NOEZEMA",
    version="0.1.0-dev",
    description="Autonomous local thinker — Web API",
    lifespan=lifespan,
)

# Mount API routes
app.include_router(router, prefix="")
