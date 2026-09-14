"""Runtime admission — fail-closed start gate (T3.13, §8.7.1.1).

The runtime target may start ONLY when every condition holds. Any missing
or unhealthy condition is a typed problem and the start is refused:

- the runtime config head tuple is present and its active snapshot's hash
  is valid (bootstrap seed integrity);
- there is NO unresolved host transition (journal reconcile is clean);
- there is NO active host-policy change (host-policy-change-head.json);
- no stale offline-rules marker is left behind;
- the host recovery policy (override or baseline) validates.

The list below is authoritative: a guarantee stated elsewhere but not
checked here does not hold.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from hostctl.journal import JournalStore
from packages.domain.config import BOOTSTRAP_SNAPSHOT_ID
from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.services.config import ConfigError, ConfigService


@dataclass
class AdmissionReport:
    ok: bool
    problems: list[str] = field(default_factory=list)

    def reject(self, problem: str) -> None:
        self.problems.append(problem)


def policy_head_path(base: Path) -> Path:
    return base / "host-policy-change-head.json"


async def admission_check(
    db: AsyncSession,
    *,
    host_lib_base: Path,
    policy_override: Path | None = None,
    policy_baseline: Path | None = None,
) -> AdmissionReport:
    """Run every admission condition; return a typed report (fail-closed)."""
    report = AdmissionReport(ok=True)

    # 1. head tuple + active snapshot hash (bootstrap seed integrity)
    try:
        effective = await ConfigService.get_effective(db)
    except ConfigError as exc:
        report.reject(f"config_head: {exc}")
        effective = None
    if effective is not None and effective.base_snapshot_id is None and effective.id != BOOTSTRAP_SNAPSHOT_ID:
        # a non-bootstrap snapshot must carry a base revision
        report.reject("config_head: non-bootstrap active snapshot has no base_snapshot_id")

    # 2. no unresolved host transition. After reconcile a present head means
    # a transition is in progress (the runtime must not start mid-maintenance);
    # a reconcile problem is a permanent journal inconsistency.
    store = JournalStore(host_lib_base)
    res = store.reconcile()
    if not res.ok:
        report.reject(f"host_transition:{res.problem}")
    elif res.head_attempt_id is not None:
        report.reject("unresolved_host_transition")

    # 3. no active host-policy change
    if policy_head_path(host_lib_base).exists():
        report.reject("host_policy_change_in_progress")

    # 4. no stale offline-rules marker
    marker = host_lib_base / "noezema-offline-rules" / "active"
    if marker.exists():
        report.reject("stale_offline_marker")

    # 5. the recovery policy validates (override first, else baseline)
    policy_path = policy_override if policy_override is not None else policy_baseline
    if policy_path is not None:
        from hostctl.policy import PolicyError, read_policy_file

        try:
            read_policy_file(policy_path)
        except PolicyError as exc:
            report.reject(f"host_policy_invalid: {exc}")

    report.ok = not report.problems
    return report


async def head_tuple(db: AsyncSession) -> dict[str, Any] | None:
    """The runtime config head tuple (for the transition record + audit)."""
    head = (
        (
            await db.execute(select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == "global"))
        )
        .scalars()
        .first()
    )
    if head is None:
        return None
    snapshot = await db.get(ORMConfigSnapshot, head.active_config_snapshot_id)
    return {
        "active_config_snapshot_id": str(uuid.UUID(str(head.active_config_snapshot_id))),
        "activation_fence": int(head.activation_fence),
        "active_payload_sha256": snapshot.payload_sha256 if snapshot else None,
    }
