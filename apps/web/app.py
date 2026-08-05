"""FastAPI app — status page, timeline API, message inbox, operator commands.

DB-aware: connects to PostgreSQL via SQLAlchemy on startup.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from apps.web.db import init_db, create_tables, shutdown_db
from apps.web.routes import router

STATIC_DIR = Path(__file__).parent / "static"

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

# Mount static files (CSS, JS, images)
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# SPA entry point at /ui
@app.get("/ui")
async def spa_index():
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return FileResponse(str(index_path))
    return {"status": "ok", "message": "No UI — API only"}

# Mount API routes (including / for status JSON)
app.include_router(router, prefix="")
