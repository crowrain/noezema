"""Host entry point wires the research proxy (T7.7, EVAL-3).

``build_orchestrator`` is the shared session pipeline for the wake tick
and ``eval-run``. Before EVAL-3 it was called without a research
service, so ``research.fetch`` failed closed with "research proxy is not
configured for this host" even on curated-profile hosts. The wiring is
inert where the profile has no egress: the tool is profile-gated and the
proxy fails closed in sealed mode.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.main import build_orchestrator
from apps.research_proxy.service import ResearchProxyService


@pytest.mark.unit
async def test_build_orchestrator_wires_research_service(tmp_path: Path) -> None:
    engine = create_async_engine(
        "postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:1/noezema"
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    orchestrator, gateway = build_orchestrator(factory, tmp_path / "workspace")
    try:
        assert isinstance(orchestrator.research_service, ResearchProxyService)
        store_root = orchestrator.research_service.store.root
        assert store_root == (tmp_path / "artifacts").resolve()
        assert store_root.is_dir()
    finally:
        await gateway.close()
        await engine.dispose()
