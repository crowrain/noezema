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
    from sqlalchemy.ext.asyncio import (
        async_sessionmaker,
        create_async_engine,
    )

    from apps.orchestrator.scheduler import artifacts_root_from_env
    from packages.artifacts.store import FilesystemArtifactStore
    from packages.domain.db.engine import DatabaseSettings

    settings = DatabaseSettings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # T7.61(б): the artifact root is derived from NOEZEMA_DATA_ROOT exactly like every other host entry point —
    # `<data root>/artifacts`, the sibling of the `<data root>/workspace` used by wake tick, web bind and the
    # manual orchestrator. It used to be hardcoded to /var/lib/noezema/artifacts, so a node with its own data
    # root (the dev stand runs as an unprivileged user over /var/lib/noezema-dev) wrote artifacts outside that
    # node's data. Unset env → the same historical path as before.
    store = FilesystemArtifactStore(artifacts_root_from_env())
    return create_proxy_app(ResearchProxyService(factory, store))


app = build_standalone_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8322)
