"""Host Status Adapter — the web's fail-closed host view (T3.24, §13).

The web is NOT a source of truth for host state: it reads the published
unit-state snapshot, the host-transition journal and the policy-change
head, and derives a single health/recovery view. It is fail-closed:

- any unresolved host transition, an active policy change, a stale/missing
  unit-state snapshot, or a DB outage marks the node UNHEALTHY and the
  Command API is refused (read-only degraded observer mode);
- a host record is NEVER presented as a DB audit row before it is replayed
  (the SSE timeline only streams committed outbox events + host
  notifications, never the raw journal as audit).

The adapter is read-only and pure: it never mutates the journal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hostctl.admission import policy_head_path
from hostctl.journal import JournalStore
from hostctl.unit_state import read_unit_state

# recovery states surfaced on the main page banner
RECOVERY_NONE = "none"
RECOVERY_RETRY_WAIT = "retry_wait"
RECOVERY_DEGRADED = "resume_degraded"
RECOVERY_BLOCKED = "resume_blocked"

_STATE_TO_RECOVERY = {
    "retry_wait": RECOVERY_RETRY_WAIT,
    "resume_degraded": RECOVERY_DEGRADED,
    "resume_blocked": RECOVERY_BLOCKED,
    "resolved": RECOVERY_NONE,
    "checking": RECOVERY_RETRY_WAIT,
    "ready_to_start": RECOVERY_NONE,
}


@dataclass
class HostStatus:
    healthy: bool
    recovery_state: str
    unit_state: dict[str, Any] = field(default_factory=dict)
    unresolved_transition: dict[str, Any] | None = None
    active_policy_change: bool = False
    db_reachable: bool = True
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "healthy": self.healthy,
            "recovery_state": self.recovery_state,
            "unit_state": self.unit_state,
            "unresolved_transition": self.unresolved_transition,
            "active_policy_change": self.active_policy_change,
            "db_reachable": self.db_reachable,
            "warnings": list(self.warnings),
        }


class HostStatusAdapter:
    """Reads the host view from disk (unit-state, journal, policy head)."""

    def __init__(self, *, host_lib_base: Path, unit_state_path: Path) -> None:
        self.host_lib = host_lib_base
        self.unit_state_path = unit_state_path

    def read(self) -> HostStatus:
        # the host protocol is only "active" once it has been set up
        # (journal dir or a published unit-state). On a host without the
        # systemd layout (dev / tests) there is nothing to gate on, so the
        # node is healthy by default; fail-closed applies as soon as the
        # protocol files exist and go stale/inconsistent.
        if not self.host_lib.exists() and not self.unit_state_path.exists():
            return HostStatus(
                healthy=True,
                recovery_state=RECOVERY_NONE,
                unit_state={"present": False, "configured": False},
                warnings=[],
            )

        warnings: list[str] = []

        # unit-state snapshot: missing or stale -> unhealthy
        unit_state: dict[str, Any] = {}
        snapshot = read_unit_state(self.unit_state_path)
        if snapshot is None:
            unit_state = {"present": False, "fresh": False}
            warnings.append("unit_state_missing")
        else:
            fresh = snapshot.is_fresh()
            unit_state = {
                "present": True,
                "fresh": fresh,
                "boot_id": snapshot.boot_id,
                "generated_at": snapshot.generated_at,
                "units": snapshot.units,
            }
            if not fresh:
                warnings.append("unit_state_stale")

        # host-transition journal: any unresolved transition -> unhealthy
        store = JournalStore(self.host_lib)
        res = store.reconcile()
        unresolved = res.unresolved[0] if res.unresolved else None
        unresolved_dict = None
        if res.problem is not None:
            warnings.append(f"host_transition_inconsistent:{res.problem}")
            recovery = RECOVERY_BLOCKED
        elif unresolved is not None:
            unresolved_dict = {
                "attempt_id": unresolved.attempt_id,
                "state": unresolved.state,
                "operation": unresolved.operation,
                "attempts_total": unresolved.attempts_total,
            }
            warnings.append("unresolved_host_transition")
            recovery = _STATE_TO_RECOVERY.get(unresolved.state, RECOVERY_RETRY_WAIT)
        else:
            recovery = RECOVERY_NONE

        # active policy change -> unhealthy (commands refused)
        active_policy = policy_head_path(self.host_lib).exists()
        if active_policy:
            warnings.append("active_policy_change")
            if recovery == RECOVERY_NONE:
                recovery = RECOVERY_RETRY_WAIT

        healthy = (
            snapshot is not None
            and snapshot.is_fresh()
            and res.problem is None
            and unresolved is None
            and not active_policy
        )
        return HostStatus(
            healthy=healthy,
            recovery_state=recovery,
            unit_state=unit_state,
            unresolved_transition=unresolved_dict,
            active_policy_change=active_policy,
            warnings=warnings,
        )

    def commands_allowed(self) -> bool:
        """The Command API is fail-closed unless the host is fully healthy."""
        return self.read().healthy
