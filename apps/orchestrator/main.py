"""Orchestrator entry point: run one cognitive session (M1).

Usage: python -m apps.orchestrator
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from apps.orchestrator.node_guard import NodeSessionGuard
from apps.orchestrator.orchestrator import Orchestrator
from apps.orchestrator.scheduler import (
    REASON_SESSION_IN_PROGRESS,
    node_owner_from_env,
    workspace_root_from_env,
)
from apps.orchestrator.session_assembly import build_session_orchestrator
from packages.domain.db.engine import DatabaseSettings
from packages.llm_gateway.client import LLMMiddleware


def build_orchestrator(
    session_factory: async_sessionmaker[AsyncSession], workspace_root: Path
) -> tuple[Orchestrator, LLMMiddleware]:
    """Assemble gateway + profile + executor + orchestrator (T3.29).

    Shared by the manual entry point and the wake tick so both run the
    exact same session pipeline. The caller closes the gateway.

    T7.7 (EVAL-3): the research proxy is wired at every host entry point
    (wake tick, eval run). The ``research.fetch`` tool is profile-gated
    (curated/open_lab only) and the proxy fails closed in sealed mode,
    so the wiring is inert where the profile has no egress. It stays
    HOST-SIDE in both executor modes (T7.58): a sandboxed session fetches
    through the proxy, never through the container's own network.

    T7.58 (ADR-0023): the tool executor is selected by ``NOEZEMA_TOOL_EXECUTOR``
    — "stub" (default, unchanged: the DEV ONLY in-process stand-in) or
    "sandbox" (the one-shot container per session, opened/closed by
    ``Orchestrator.run_session``). An unknown value fails closed. The artifact
    store stays host-side in both modes: it sits next to the workspace root
    (``<data root>/artifacts``), so whoever picks the data root moves artifacts
    with it (T7.59(в) — the manual entry no longer pins ``/var/lib/noezema``).

    T7.68 (ADR-0027 §1): the assembly itself lives in
    ``apps.orchestrator.session_assembly.build_session_orchestrator`` — one pure
    builder shared with the standalone web factory, so no entry point can omit
    the research service or move the artifact store to another directory. The
    semantics pinned above are unchanged; this function is now a thin call.
    """
    return build_session_orchestrator(session_factory, workspace_root)


async def _run() -> int:
    settings = DatabaseSettings()
    engine = create_async_engine(settings.database_url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    # T7.59(в): the workspace/artifacts root comes from NOEZEMA_DATA_ROOT exactly like the wake tick
    # does (`hostctl wake-tick` builds `data_root_from_env() / "workspace"`), instead of a hardcoded
    # production path. Unset env → the historical /var/lib/noezema/workspace (behaviour unchanged);
    # a stand running as an ordinary user gets `<its data root>/workspace` + `/artifacts` and no
    # PermissionError at startup. In sandbox mode the stub workspace is not used at all.
    orchestrator, gateway = build_orchestrator(factory, workspace_root_from_env())
    # T7.61(а) (§5.2.1): the manual entry is a host session entry point like wake-tick, eval-run and the
    # web's «wake now», so it takes the node's session lane BEFORE starting anything: without it a manual
    # run could sit next to a scheduled tick's session on the same node (the T7.61(а) race). A busy lane is
    # a skip, not a failure — exit 0, no session row, no failure accounting touched.
    guard = NodeSessionGuard(engine, node_owner_from_env())
    try:
        if not await guard.acquire():
            print(f"session not started: {REASON_SESSION_IN_PROGRESS} — another session owns this node")
            return 0
        outcome = await orchestrator.run_session()
    finally:
        await gateway.close()
        await guard.release()
        await engine.dispose()
    print(
        f"session {outcome.session_id} -> {outcome.final_state.value} "
        f"steps={outcome.steps} evidence={outcome.evidence_count} "
        f"reason={outcome.termination_reason}"
    )
    return 0 if outcome.final_state.value in ("succeeded", "succeeded_partial") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_run()))
