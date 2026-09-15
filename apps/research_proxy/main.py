"""Research proxy entry point (T6.1, stage 5, §5.12).

Usage: python -m apps.research_proxy.main   (or: uvicorn
apps.research_proxy.main:app)

The proxy reads the EFFECTIVE config from the domain DB (fail-closed)
on every request; the bootstrap default is sealed mode — the process
runs but refuses every fetch until the operator changes the
``research_proxy`` section to curated/open_lab (T6.2).
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI

from apps.research_proxy.api import create_proxy_app
from apps.research_proxy.service import ResearchProxyService


def build_standalone_app() -> FastAPI:
    """Entry helper: build the app from env config (DB URL, artifact
    root)."""
    from pathlib import Path

    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    from packages.artifacts.store import FilesystemArtifactStore
    from packages.domain.db.engine import DatabaseSettings

    settings = DatabaseSettings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store = FilesystemArtifactStore(Path("/var/lib/noezema/artifacts"))
    return create_proxy_app(ResearchProxyService(factory, store))


app = build_standalone_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8322)
