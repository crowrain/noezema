"""Wake scheduler (T3.29, §5.2.1).

Wake scheduling is owned by the trusted boundary and is invisible from the
sandbox:

- the base schedule (periodic interval + minimum gap between sessions), the
  backoff parameters and the admission limits live in the effective config
  snapshot (``wake_schedule`` section);
- the per-node wake state (consecutive failures, backoff window, last
  outcome, auto-pause reason) is durable in ``wake_scheduler_state`` and
  survives restarts;
- the operator-visible node state (idle/paused/session_running) lives in
  ``system_constants.node_state``.

Rules (§5.2.1):

- before a wake, the admission list is checked (no nonterminal session, no
  unresolved commit attempt, empty activation slot, disk quota, not paused);
  an unmet condition SKIPS the wake with the exact reason recorded in the
  audit (``wake_skipped``) — the wake is never queued;
- a failed session triggers exponential backoff; after
  ``max_consecutive_failures`` consecutive failures the node goes to
  ``paused`` (sticky until the operator ``resume``);
- a regular ``wake_now`` bypasses the schedule timing (interval, minimum
  gap, backoff window) but NOT admission.

MVP notes: the base schedule is the periodic cron case (every
``interval_seconds``); a richer cron grammar is a v1 extension. The GPU gate
is fail-closed: with ``gpu_required=true`` the wake is skipped because GPU
introspection is not implemented yet.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.db.uow import transaction
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import AuditEventType, AuditVisibility, SessionState
from packages.domain.models.wake import ORMWakeSchedulerState
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigService

NODE_STATE_KEY = "node_state"
NODE_OWNER_ENV = "NOEZEMA_NODE_OWNER"
DATA_ROOT_ENV = "NOEZEMA_DATA_ROOT"
DEFAULT_NODE_OWNER = "local-node"
DEFAULT_DATA_ROOT = "/var/lib/noezema"

# Admission reason codes (stable; recorded in the wake_skipped audit payload).
REASON_PAUSED = "paused"
REASON_NONTERMINAL_SESSION = "nonterminal_session"
REASON_UNRESOLVED_COMMIT = "unresolved_commit_attempt"
REASON_ACTIVATION_SLOT = "activation_slot_busy"
REASON_REASSESSMENT_BACKLOG = "reassessment_backlog"
REASON_REPAIR_BACKLOG = "repair_backlog"
REASON_DISK_QUOTA = "disk_quota_exceeded"
REASON_GPU = "gpu_unavailable"

# Schedule wait reasons (not audited: "not due yet" is not an event).
WAIT_INTERVAL = "interval_not_elapsed"
WAIT_MIN_INTERVAL = "min_interval_not_elapsed"
WAIT_BACKOFF = "backoff_active"

_TERMINAL_SESSION_STATES: tuple[str, ...] = tuple(s.value for s in SessionState if s.is_terminal)
_TERMINAL_SQL = ",".join(f"'{s}'" for s in _TERMINAL_SESSION_STATES)


class WakeScheduleError(RuntimeError):
    """The wake_schedule section is missing or invalid (fail-closed)."""


def node_owner_from_env() -> str:
    return os.environ.get(NODE_OWNER_ENV) or DEFAULT_NODE_OWNER


def data_root_from_env() -> Path:
    return Path(os.environ.get(DATA_ROOT_ENV) or DEFAULT_DATA_ROOT)


@dataclass(frozen=True)
class WakeSchedule:
    """Validated wake_schedule section of the config snapshot (§5.2.1)."""

    interval_seconds: int
    min_session_interval_seconds: int
    backoff_base_seconds: int
    backoff_multiplier: float
    backoff_max_seconds: int
    max_consecutive_failures: int
    disk_quota_mb: int
    gpu_required: bool

    @classmethod
    def from_payload(cls, raw: Any) -> WakeSchedule:
        if not isinstance(raw, Mapping):
            raise WakeScheduleError(f"wake_schedule must be an object, got {type(raw).__name__}")
        required: dict[str, Any] = {
            "interval_seconds": int,
            "min_session_interval_seconds": int,
            "backoff_base_seconds": int,
            "backoff_multiplier": (int, float),
            "backoff_max_seconds": int,
            "max_consecutive_failures": int,
            "disk_quota_mb": int,
            "gpu_required": bool,
        }
        for key, typ in required.items():
            value = raw.get(key)
            if value is None:
                raise WakeScheduleError(f"wake_schedule.{key} is missing")
            if typ is bool:
                if not isinstance(value, bool):
                    raise WakeScheduleError(f"wake_schedule.{key} has wrong type: {value!r}")
            elif isinstance(value, bool) or not isinstance(value, typ):
                # bool is excluded from the numeric fields (isinstance(True, int))
                raise WakeScheduleError(f"wake_schedule.{key} has wrong type: {value!r}")
        if raw["interval_seconds"] <= 0:
            raise WakeScheduleError("wake_schedule.interval_seconds must be > 0")
        if raw["min_session_interval_seconds"] <= 0:
            raise WakeScheduleError("wake_schedule.min_session_interval_seconds must be > 0")
        if raw["backoff_base_seconds"] <= 0:
            raise WakeScheduleError("wake_schedule.backoff_base_seconds must be > 0")
        if raw["backoff_multiplier"] < 1:
            raise WakeScheduleError("wake_schedule.backoff_multiplier must be >= 1")
        if raw["backoff_max_seconds"] < raw["backoff_base_seconds"]:
            raise WakeScheduleError("wake_schedule.backoff_max_seconds must be >= backoff_base_seconds")
        if raw["max_consecutive_failures"] < 1:
            raise WakeScheduleError("wake_schedule.max_consecutive_failures must be >= 1")
        if raw["disk_quota_mb"] <= 0:
            raise WakeScheduleError("wake_schedule.disk_quota_mb must be > 0")
        return cls(
            interval_seconds=raw["interval_seconds"],
            min_session_interval_seconds=raw["min_session_interval_seconds"],
            backoff_base_seconds=raw["backoff_base_seconds"],
            backoff_multiplier=float(raw["backoff_multiplier"]),
            backoff_max_seconds=raw["backoff_max_seconds"],
            max_consecutive_failures=raw["max_consecutive_failures"],
            disk_quota_mb=raw["disk_quota_mb"],
            gpu_required=raw["gpu_required"],
        )


class ReassessmentAdmissionError(RuntimeError):
    """The reassessment_admission section is missing or invalid (fail-closed)."""


@dataclass(frozen=True)
class ReassessmentAdmission:
    """Validated reassessment_admission section of the config snapshot
    (§5.9.1, T4.4).

    ``t_escalate_seconds`` — a runnable job OLDER than this is
    dependency-critical regardless of its original reason (derived at
    admission time, no row mutation).
    ``t_worker_admission_seconds`` — a wake is skipped while the oldest
    runnable dependency-critical job is older than this.
    ``queue_slo_seconds`` — the wall-clock SLO of the runnable queue
    (operator metric; a long-non-empty queue is a memory degradation).
    """

    t_escalate_seconds: int
    t_worker_admission_seconds: int
    queue_slo_seconds: int

    @classmethod
    def from_payload(cls, raw: Any) -> ReassessmentAdmission:
        if not isinstance(raw, Mapping):
            raise ReassessmentAdmissionError(
                f"reassessment_admission must be an object, got {type(raw).__name__}"
            )
        for key in ("t_escalate_seconds", "t_worker_admission_seconds", "queue_slo_seconds"):
            value = raw.get(key)
            if value is None:
                raise ReassessmentAdmissionError(f"reassessment_admission.{key} is missing")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ReassessmentAdmissionError(
                    f"reassessment_admission.{key} must be an int > 0, got {value!r}"
                )
        return cls(
            t_escalate_seconds=raw["t_escalate_seconds"],
            t_worker_admission_seconds=raw["t_worker_admission_seconds"],
            queue_slo_seconds=raw["queue_slo_seconds"],
        )


class RepairAdmissionError(RuntimeError):
    """The repair_admission section is missing or invalid (fail-closed)."""


@dataclass(frozen=True)
class RepairAdmission:
    """Validated repair_admission section of the config snapshot
    (§5.2.1 T_repair_admission, §8.7.2, T4.5).

    ``t_repair_admission_seconds`` — the age of a runnable repair
    backlog (a ``post_publish_blocked`` candidate that owns the
    pointer, has a due cursor and is admission-runnable) beyond which
    a wake is skipped with ``repair_backlog``. A fresh backlog does not
    block the wake immediately — the repair runner is a separate
    trusted lane, the session lane waits only for an old backlog.
    ``repair_slo_seconds`` — the wall-clock SLO of the repair backlog
    (operator metric).
    """

    t_repair_admission_seconds: int
    repair_slo_seconds: int

    @classmethod
    def from_payload(cls, raw: Any) -> RepairAdmission:
        if not isinstance(raw, Mapping):
            raise RepairAdmissionError(
                f"repair_admission must be an object, got {type(raw).__name__}"
            )
        for key in ("t_repair_admission_seconds", "repair_slo_seconds"):
            value = raw.get(key)
            if value is None:
                raise RepairAdmissionError(f"repair_admission.{key} is missing")
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise RepairAdmissionError(
                    f"repair_admission.{key} must be an int > 0, got {value!r}"
                )
        return cls(
            t_repair_admission_seconds=raw["t_repair_admission_seconds"],
            repair_slo_seconds=raw["repair_slo_seconds"],
        )


@dataclass(frozen=True)
class WakeDecision:
    """Outcome of one wake evaluation.

    action: 'wait' (schedule timing not due; nothing recorded) |
            'wake' (admitted; the caller runs the session) |
            'skip' (admission failed; wake_skipped recorded in the audit).
    """

    action: Literal["wait", "wake", "skip"]
    reason: str | None = None
    detail: str | None = None


def evaluate_schedule_timing(
    now: datetime,
    *,
    interval_seconds: int,
    min_session_interval_seconds: int,
    last_session_finished_at: datetime | None,
    backoff_until: datetime | None,
) -> tuple[bool, str | None]:
    """Pure schedule-timing check (§5.2.1). Returns (due, wait_reason).

    First-ever tick (no finished session) is due unless a backoff window is
    still active. Afterwards the tick is due only after BOTH the base
    interval and the minimum gap have elapsed AND the backoff window has
    passed.
    """
    if last_session_finished_at is None:
        if backoff_until is not None and now < backoff_until:
            return False, WAIT_BACKOFF
        return True, None
    if now < last_session_finished_at + timedelta(seconds=interval_seconds):
        return False, WAIT_INTERVAL
    if now < last_session_finished_at + timedelta(seconds=min_session_interval_seconds):
        return False, WAIT_MIN_INTERVAL
    if backoff_until is not None and now < backoff_until:
        return False, WAIT_BACKOFF
    return True, None


def backoff_delay_seconds(
    *,
    consecutive_failures: int,
    base_seconds: int,
    multiplier: float,
    max_seconds: int,
) -> int:
    """Exponential backoff after the n-th consecutive failed session.

    n=1 -> base; n=2 -> base*multiplier; capped at max_seconds.
    """
    if consecutive_failures < 1:
        raise ValueError("consecutive_failures must be >= 1")
    delay = base_seconds * (multiplier ** (consecutive_failures - 1))
    return int(min(max_seconds, delay))


def _du_bytes(root: Path) -> int:
    """Total size of regular files under root (0 for a missing directory)."""
    if not root.is_dir():
        return 0
    total = 0
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink():
                total += path.stat().st_size
        except OSError:
            continue
    return total


class WakeScheduler:
    """One node's wake decisions (schedule + admission + backoff).

    All methods take the caller's AsyncSession; each opens its own
    transaction (audit + outbox are committed together with the skip).
    """

    def __init__(self, db: AsyncSession, *, node_owner: str, data_root: Path) -> None:
        self.db = db
        self.node_owner = node_owner
        self.data_root = Path(data_root)

    # ── reads ────────────────────────────────────────────────────────────

    async def _load_node_state(self) -> str:
        value = (
            await self.db.execute(
                text("SELECT value FROM system_constants WHERE key = :k"), {"k": NODE_STATE_KEY}
            )
        ).scalar_one_or_none()
        return value if value is not None else "idle"

    async def _save_node_state(self, state: str) -> None:
        await self.db.execute(
            text(
                "INSERT INTO system_constants (key, value) VALUES (:k, :v) "
                "ON CONFLICT (key) DO UPDATE SET value = :v"
            ),
            {"k": NODE_STATE_KEY, "v": state},
        )

    async def _load_state(self) -> ORMWakeSchedulerState:
        row = (
            await self.db.execute(
                text("SELECT node_id FROM wake_scheduler_state WHERE node_id = :n"),
                {"n": self.node_owner},
            )
        ).first()
        if row is None:
            state = ORMWakeSchedulerState(node_id=self.node_owner)
            self.db.add(state)
            await self.db.flush()
            return state
        loaded = await self.db.get(ORMWakeSchedulerState, self.node_owner)
        if loaded is None:
            # cannot happen: the row existence was just verified in this
            # session's transaction
            raise RuntimeError(f"wake scheduler state row {self.node_owner} disappeared")
        return loaded

    async def status(self) -> JsonDict:
        """Operator-visible wake state (for the web /status endpoint)."""
        state = await self._load_state()
        snapshot = await ConfigService.get_effective(self.db)
        admission = ReassessmentAdmission.from_payload(snapshot.reassessment_admission)
        oldest = (
            await self.db.execute(
                text(
                    """
                    SELECT MAX(EXTRACT(EPOCH FROM now() - j.enqueued_at))
                    FROM reassessment_jobs j
                    WHERE j.status IN ('queued','retry')
                      AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= now())
                      AND j.attempts < j.max_attempts
                      AND j.target_config_snapshot_id = (
                          SELECT active_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global')
                      AND (
                          SELECT activating_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global') IS NULL
                    """
                )
            )
        ).scalar_one_or_none()
        oldest_age = float(oldest) if oldest is not None else None
        repair = RepairAdmission.from_payload(snapshot.repair_admission)
        repair_age = (
            await self.db.execute(
                text(
                    """
                    SELECT EXTRACT(EPOCH FROM now() - c.post_publish_started_at)
                    FROM config_snapshots c
                    WHERE c.activation_mode = 'online'
                      AND c.activation_state = 'post_publish_blocked'
                      AND c.id = (
                          SELECT active_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global')
                      AND c.post_publish_next_attempt_at IS NOT NULL
                      AND c.post_publish_next_attempt_at <= now()
                    """
                )
            )
        ).scalar_one_or_none()
        repair_age_f = float(repair_age) if repair_age is not None else None
        return {
            "consecutive_failures": state.consecutive_failures,
            "backoff_until": state.backoff_until.isoformat() if state.backoff_until else None,
            "last_session_state": state.last_session_state,
            "paused_reason": state.paused_reason,
            # T4.4 (§5.9.1): the runnable-queue depth/age metrics; the SLO
            # is pinned in the config snapshot
            "reassessment_queue": {
                "oldest_runnable_age_seconds": oldest_age,
                "queue_slo_seconds": admission.queue_slo_seconds,
                "slo_breached": oldest_age is not None and oldest_age > admission.queue_slo_seconds,
            },
            # T4.5 (§8.7.2): the repair-backlog age metric (the
            # post_publish_blocked manifest that owns the pointer and is
            # admission-runnable)
            "repair_backlog": {
                "oldest_age_seconds": repair_age_f,
                "repair_slo_seconds": repair.repair_slo_seconds,
                "slo_breached": repair_age_f is not None
                and repair_age_f > repair.repair_slo_seconds,
            },
        }

    # ── admission ────────────────────────────────────────────────────────

    async def _admission(
        self,
        schedule: WakeSchedule,
        admission: ReassessmentAdmission,
        repair: RepairAdmission,
        *,
        node_state: str,
    ) -> tuple[bool, str | None]:
        """The authoritative §5.2.1 admission list. First failure wins.

        Returns (ok, reason). The paused_reason detail is carried by the
        caller into the audit payload.
        """
        if node_state == "paused":
            return False, REASON_PAUSED
        nonterminal = (
            await self.db.execute(
                text(f"SELECT count(*) FROM sessions WHERE state NOT IN ({_TERMINAL_SQL})")
            )
        ).scalar_one()
        if int(nonterminal) > 0:
            return False, REASON_NONTERMINAL_SESSION
        unresolved = (
            await self.db.execute(
                text("SELECT count(*) FROM commit_attempts WHERE status NOT IN ('committed','aborted')")
            )
        ).scalar_one()
        if int(unresolved) > 0:
            return False, REASON_UNRESOLVED_COMMIT
        activating = (
            await self.db.execute(
                text(
                    "SELECT activating_config_snapshot_id FROM runtime_config_heads "
                    "WHERE scope = 'global'"
                )
            )
        ).scalar_one_or_none()
        if activating is not None:
            return False, REASON_ACTIVATION_SLOT
        # T4.4 (§5.9.1 liveness): the wake does not start while the oldest
        # runnable dependency-critical reassessment job is older than
        # T_worker_admission. A job is dependency-critical when its age
        # exceeds T_escalate (the escalation rule, derived — the reason
        # column is not mutated). The queue gets the window BETWEEN
        # sessions and never fights the active one.
        oldest = (
            await self.db.execute(
                text(
                    """
                    SELECT MAX(EXTRACT(EPOCH FROM now() - j.enqueued_at))
                    FROM reassessment_jobs j
                    WHERE j.status IN ('queued','retry')
                      AND (j.next_attempt_at IS NULL OR j.next_attempt_at <= now())
                      AND j.attempts < j.max_attempts
                      AND j.target_config_snapshot_id = (
                          SELECT active_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global')
                      AND (
                          SELECT activating_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global') IS NULL
                      AND EXTRACT(EPOCH FROM now() - j.enqueued_at) > :t_esc
                    """
                ),
                {"t_esc": admission.t_escalate_seconds},
            )
        ).scalar_one_or_none()
        if oldest is not None and float(oldest) > admission.t_worker_admission_seconds:
            return False, REASON_REASSESSMENT_BACKLOG
        # T4.5 (§5.2.1 T_repair_admission): a runnable repair backlog
        # (a post_publish_blocked candidate owning the pointer with a
        # due, admission-runnable cursor) OLDER than the threshold
        # blocks the session lane — the node must repair the manifest
        # before starting new research. A fresh backlog does not block
        # the wake immediately (the repair runner is a separate lane).
        repair_age = (
            await self.db.execute(
                text(
                    """
                    SELECT EXTRACT(EPOCH FROM now() - c.post_publish_started_at)
                    FROM config_snapshots c
                    WHERE c.activation_mode = 'online'
                      AND c.activation_state = 'post_publish_blocked'
                      AND c.id = (
                          SELECT active_config_snapshot_id FROM runtime_config_heads
                          WHERE scope = 'global')
                      AND c.post_publish_next_attempt_at IS NOT NULL
                      AND c.post_publish_next_attempt_at <= now()
                    """
                )
            )
        ).scalar_one_or_none()
        if repair_age is not None and float(repair_age) > repair.t_repair_admission_seconds:
            return False, REASON_REPAIR_BACKLOG
        if _du_bytes(self.data_root) > schedule.disk_quota_mb * 1024 * 1024:
            return False, REASON_DISK_QUOTA
        if schedule.gpu_required:
            # Fail-closed: GPU introspection is not implemented (MVP is a
            # local CPU model, gpu_required=false in the bootstrap payload).
            return False, REASON_GPU
        return True, None

    # ── decide ───────────────────────────────────────────────────────────

    async def decide(self, *, source: Literal["scheduled", "wake_now"], now: datetime) -> WakeDecision:
        """Evaluate one wake. 'wake' means: the caller must run the session
        and then call :meth:`record_session_result`."""
        async with transaction(self.db):
            snapshot = await ConfigService.get_effective(self.db)
            schedule = WakeSchedule.from_payload(snapshot.wake_schedule)
            admission = ReassessmentAdmission.from_payload(snapshot.reassessment_admission)
            repair = RepairAdmission.from_payload(snapshot.repair_admission)
            state = await self._load_state()
            node_state = await self._load_node_state()

            if source == "scheduled":
                due, wait_reason = evaluate_schedule_timing(
                    now,
                    interval_seconds=schedule.interval_seconds,
                    min_session_interval_seconds=schedule.min_session_interval_seconds,
                    last_session_finished_at=state.last_session_finished_at,
                    backoff_until=state.backoff_until,
                )
                if not due:
                    return WakeDecision(action="wait", reason=wait_reason)

            ok, reason = await self._admission(
                schedule, admission, repair, node_state=node_state
            )
            if not ok:
                audit = AuditService(self.db)
                await audit.record(
                    AuditEventType.WAKE_SKIPPED,
                    payload={
                        "source": source,
                        "reason": reason,
                        "paused_reason": state.paused_reason,
                        "node_owner": self.node_owner,
                    },
                    actor="wake-scheduler",
                    visibility=AuditVisibility.OPERATOR,
                )
                return WakeDecision(action="skip", reason=reason, detail=state.paused_reason)
            return WakeDecision(action="wake", reason=None)

    # ── session outcome ──────────────────────────────────────────────────

    async def record_session_result(self, *, final_state: str, now: datetime) -> str:
        """Apply the backoff/pause rules to a finished session.

        Returns the resulting node state ('idle' or 'paused'). A pause
        (operator or auto) is never cleared here: it is sticky until the
        operator resume.
        """
        async with transaction(self.db):
            snapshot = await ConfigService.get_effective(self.db)
            schedule = WakeSchedule.from_payload(snapshot.wake_schedule)
            state = await self._load_state()
            node_state = await self._load_node_state()

            if final_state == "failed":
                state.consecutive_failures += 1
                state.last_failure_at = now
                delay = backoff_delay_seconds(
                    consecutive_failures=state.consecutive_failures,
                    base_seconds=schedule.backoff_base_seconds,
                    multiplier=schedule.backoff_multiplier,
                    max_seconds=schedule.backoff_max_seconds,
                )
                state.backoff_until = now + timedelta(seconds=delay)
            else:
                # succeeded / succeeded_partial / cancelled: not a failure.
                state.consecutive_failures = 0
                state.backoff_until = None
                state.last_failure_at = None

            state.last_session_finished_at = now
            state.last_session_state = final_state
            state.updated_at = now

            if final_state == "failed" and state.consecutive_failures >= schedule.max_consecutive_failures:
                if node_state != "paused":
                    state.paused_reason = "consecutive_failures"
                    await self._save_node_state("paused")
                return "paused"
            if node_state in ("idle", "session_running"):
                state.paused_reason = None
                await self._save_node_state("idle")
                return "idle"
            return "paused"  # a pause set during the session is preserved

    async def reset_failure_state(self) -> None:
        """Operator resume: clear the failure bookkeeping (T3.29).

        The node becomes eligible for the next scheduled tick immediately;
        the schedule timing still applies (interval since last session).
        """
        async with transaction(self.db):
            state = await self._load_state()
            state.consecutive_failures = 0
            state.backoff_until = None
            state.last_failure_at = None
            state.paused_reason = None
            state.updated_at = datetime.now(UTC)
