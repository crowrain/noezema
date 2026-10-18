"""Single assembly point of a session orchestrator (T7.68, ADR-0027 §1).

Every host entry point that runs ``Orchestrator.run_session`` — the wake tick
(``hostctl wake-tick``), ``eval-run``/smoke series, the manual
``python -m apps.orchestrator`` and the standalone web (``build_standalone_app``,
the only path a real dev stand uses for «wake now») — assembles its session
through THIS module. Until T7.68 the web factory built the orchestrator itself
and omitted ``research_service``, so every web-started session answered
``research.fetch`` with "research proxy is not configured for this host" even
though the curated profile granted the tool (docs/web-access-design.md G1).

The pure-function shape is the AGENTS §4 pattern: the factories
(``apps.orchestrator.main.build_orchestrator``, ``apps.web.api.build_standalone_app``)
keep their public contracts and only feed their own inputs here. Nothing else
constructs an ``Orchestrator`` in production code — tests keep their doubles.

What is identical everywhere (this is the invariant the tests pin):

- gateway: ``LLMGatewayConfig()`` from the process environment, one middleware
  per orchestrator (the caller closes it — that was already true for the tick
  and manual entry; web keeps its gateway for the lifetime of its process, as
  before T7.68);
- model profile: ``ModelProfile(model_alias=llm_config.model, backend_name="local")``;
- tool executor: ``build_tool_executor`` behind the single ``NOEZEMA_TOOL_EXECUTOR``
  switch (T7.58, ADR-0023) — default stub, behaviour unchanged;
- research service: ``ResearchProxyService`` over a ``FilesystemArtifactStore``
  rooted at the sibling of the workspace root the entry point was given —
  `<data root>/artifacts` for every normal layout (T7.61(б): the standalone
  research proxy already resolves exactly that; wake tick and eval-run already
  wrote there because ``hostctl`` passes ``<data root>/workspace``).

The artifact root is derived from the workspace root argument, not re-read from
the environment: each entry point keeps its own data-root resolution (web via
``apps.web.bind.resolve_standalone_workspace``, fail-closed on a relative path;
``hostctl wake-tick`` honours its ``--data-root`` CLI override), and the store
always lands beside the workspace that entry actually uses. For one node — where
all entries read the same ``NOEZEMA_DATA_ROOT`` — tick and web therefore write
one and the same artifact directory (pinned by tests/unit/test_research_wiring.py).
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.scheduler import ARTIFACTS_SUBDIR
from apps.orchestrator.tool_executors import build_tool_executor
from apps.research_proxy.service import ResearchProxyService
from packages.artifacts.store import FilesystemArtifactStore
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile


def artifacts_root_for(workspace_root: Path) -> Path:
    """The artifact store sibling of a session workspace root.

    Same formula every host entry has always used (``workspace_root.parent /
    "artifacts"``), expressed through the scheduler's ``ARTIFACTS_SUBDIR`` so
    the literal cannot drift from `<data root>/artifacts` (T7.61(б)).
    """
    return workspace_root.parent / ARTIFACTS_SUBDIR


def research_service_for(
    session_factory: async_sessionmaker[AsyncSession], workspace_root: Path
) -> ResearchProxyService:
    """Build the host-side research proxy for one entry point (T7.68).

    The service itself fails closed when the effective snapshot has no egress
    (sealed mode), so wiring it in unconditionally — as `build_orchestrator`
    has done since EVAL-3 — is inert on sealed hosts and inert in sandbox
    executor mode too: ``research.fetch`` stays host-side in BOTH modes
    (ADR-0023; the session container never gets a network).
    """
    return ResearchProxyService(
        session_factory, FilesystemArtifactStore(artifacts_root_for(workspace_root))
    )


def build_session_orchestrator(
    session_factory: async_sessionmaker[AsyncSession], workspace_root: Path
) -> tuple[Orchestrator, LLMMiddleware]:
    """Assemble gateway + profile + executor + research proxy + orchestrator.

    The single source of truth for session assembly (T7.68). The caller owns
    the returned gateway; the research service lives on the orchestrator
    (``orchestrator.research_service.store`` is its artifact directory).
    """
    llm_config = LLMGatewayConfig()
    profile = ModelProfile(model_alias=llm_config.model, backend_name="local")
    gateway = LLMMiddleware(llm_config)
    orchestrator = Orchestrator(
        session_factory=session_factory,
        gateway=gateway,
        profile=profile,
        executor=build_tool_executor(workspace_root),
        research_service=research_service_for(session_factory, workspace_root),
    )
    return orchestrator, gateway
