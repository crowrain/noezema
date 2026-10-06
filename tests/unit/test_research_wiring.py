"""Host entry points wire the research proxy (T7.7 EVAL-3; T7.68 ADR-0027 §1).

``build_orchestrator`` is the shared session pipeline for the wake tick and
``eval-run``. Before EVAL-3 it was called without a research service, so
``research.fetch`` failed closed with "research proxy is not configured for
this host" even on curated-profile hosts. The wiring is inert where the
profile has no egress: the tool is profile-gated and the proxy fails closed
in sealed mode.

T7.68 extends the guarantee to the STANDALONE WEB factory (`build_standalone_app`) —
the entry every dev stand uses for «wake now», which used to build its own
orchestrator WITHOUT a research service (docs/web-access-design.md G1, session
099ddccb… on .92). Both entries now share one builder
(``apps.orchestrator.session_assembly``), and for one ``NOEZEMA_DATA_ROOT`` they
resolve the same artifact directory — no engine/LLM connection is involved in
these unit tests (unreachable DB port, dummy LLM URL; nothing is ever fetched).
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from apps.orchestrator.executor import StubToolExecutor
from apps.orchestrator.main import build_orchestrator
from apps.orchestrator.scheduler import WORKSPACE_SUBDIR, artifacts_root_from_env
from apps.research_proxy.service import ResearchProxyService
from apps.web.api import build_standalone_app

# A DB URL that never connects (the entry points must not need a live DB to be
# assembled) and an LLM URL that is never called — same pattern as the original
# EVAL-3 wiring test.
UNREACHABLE_DB_URL = "postgresql+asyncpg://noezema:noezema_dev@127.0.0.1:1/noezema"
DUMMY_LLM_URL = "http://127.0.0.1:9/v1"


@pytest.mark.unit
async def test_build_orchestrator_wires_research_service(tmp_path: Path) -> None:
    engine = create_async_engine(UNREACHABLE_DB_URL)
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


def _standalone_env(monkeypatch: pytest.MonkeyPatch, data_root: Path) -> None:
    """The env shape a dev stand unit provides (NOEZEMA_* from EnvironmentFile)."""
    monkeypatch.setenv("NOEZEMA_DATABASE_URL", UNREACHABLE_DB_URL)
    monkeypatch.setenv("NOEZEMA_DATA_ROOT", str(data_root))
    monkeypatch.setenv("NOEZEMA_TOOL_EXECUTOR", "stub")  # no containers in a unit test
    monkeypatch.setenv("NOEZEMA_LLM_BASE_URL", DUMMY_LLM_URL)


@pytest.mark.unit
async def test_web_factory_wires_research_service(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7.68 G1 regression: the standalone web orchestrator HAS a research service.

    Before T7.68 `build_standalone_app` assembled `Orchestrator(...)` itself and
    omitted `research_service`, so every «wake now» session failed every
    `research.fetch` with "research proxy is not configured for this host" while
    the wake tick / eval-run / manual entry had the service. The store must be
    rooted at `<NOEZEMA_DATA_ROOT>/artifacts` — next to the web workspace.
    """
    data_root = tmp_path / "var-lib-noezema-dev"
    _standalone_env(monkeypatch, data_root)

    app = build_standalone_app()
    try:
        orchestrator = app.state.orchestrator
        assert orchestrator is not None
        assert isinstance(orchestrator.research_service, ResearchProxyService), (
            "the standalone web factory must wire the research proxy (T7.68)"
        )
        store_root = orchestrator.research_service.store.root
        assert store_root == data_root / "artifacts"
        assert store_root.is_dir()
        # The executor switch and its default are unchanged by T7.68.
        assert isinstance(orchestrator.executor, StubToolExecutor)
        assert orchestrator.executor.workspace_dir == data_root / "workspace"
    finally:
        await orchestrator.gateway.close()
        await app.state.engine.dispose()


@pytest.mark.unit
async def test_web_and_wake_tick_resolve_one_artifacts_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T7.68 equivalence: on one data root the web entry and the wake-tick call of
    ``build_orchestrator`` (exactly how hostctl calls it: factory + <root>/workspace)
    produce the same artifact directory and the same executor/service types.

    This is the invariant «web и тик на одном хосте пишут в одно хранилище»; if a
    future entry point re-introduces its own formula, this test goes red.
    """
    data_root = tmp_path / "var-lib-noezema-dev"
    _standalone_env(monkeypatch, data_root)

    app = build_standalone_app()  # the web entry, real factory from env
    try:
        web_orchestrator = app.state.orchestrator
        # hostctl wake-tick calls: build_orchestrator(factory, data_root / WORKSPACE_SUBDIR)
        tick_orchestrator, tick_gateway = build_orchestrator(
            app.state.factory, data_root / WORKSPACE_SUBDIR
        )
        try:
            assert isinstance(web_orchestrator.research_service, ResearchProxyService)
            assert isinstance(tick_orchestrator.research_service, ResearchProxyService)
            # one directory, not two stores pointing at different places
            web_store = web_orchestrator.research_service.store
            tick_store = tick_orchestrator.research_service.store
            assert web_store.root == data_root / "artifacts"
            assert tick_store.root == data_root / "artifacts"
            # the standalone research proxy entry (apps/research_proxy/main.py) and
            # hostctl blind-sample resolve artifacts the same way for this env
            assert artifacts_root_from_env() == data_root / "artifacts"
            # equal assembly, not one shared object: every entry owns its service
            assert web_orchestrator.research_service is not tick_orchestrator.research_service
            assert type(web_orchestrator.executor) is StubToolExecutor
            assert type(tick_orchestrator.executor) is StubToolExecutor
        finally:
            await tick_gateway.close()
    finally:
        await web_orchestrator.gateway.close()
        await app.state.engine.dispose()
