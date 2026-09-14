"""Host resume — the transient-first resume protocol (T3.14, §8.7.1.1).

The resume unit is a TRIGGER, not the checker: it runs the admission
probe and translates the result into one of three outcomes. Correctness
lives in the runtime units + admission, so a manual start cannot bypass
the checks.

Outcomes and exit codes (single-node, schema v1, jitter = 0):

- ``retry_wait``      -> exit 0 (the retry timer re-probes; a DB outage
                         stays here with a long backoff, unbounded, and
                         resolves on its own);
- ``resume_blocked``  -> exit 78 (permanent/inconsistent; a critical
                         alert is raised; the node does NOT silently sit);
- ``resume_degraded`` -> an unclassified crash/signal/timeout burst
                         exhausts its fast restart budget (the node is
                         degraded, NOT permanently blocked).

Every probe publishes a host event; DB audit replay is idempotent by
``(attempt_id, event_seq)``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from hostctl.admission import AdmissionReport, admission_check
from hostctl.journal import (
    STATE_CHECKING,
    STATE_RESOLVED,
    JournalStore,
    TransitionRecord,
    record_identity_header,
)
from hostctl.policy import HostPolicy, PolicyError, read_policy_file
from packages.domain.canonical import canonical_sha256
from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService

EXIT_RETRY_WAIT = 0
EXIT_RESUME_BLOCKED = 78

#: error classes that are permanently inconsistent (-> resume_blocked)
PERMANENT_ERROR_CLASSES = frozenset(
    {
        "multiple_unresolved_transitions",
        "head_identity_mismatch",
        "head_points_to_missing_record",
        "host_policy_change_in_progress",
        "host_policy_invalid",
        "config_head",
        "corrupt_journal",
    }
)


@dataclass
class ResumeOutcome:
    outcome: str
    exit_code: int
    attempt_id: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


def classify(error_class: str | None) -> str:
    """Map a typed error class to a resume outcome (transient-first)."""
    if error_class is None:
        # an unclassified failure: fast on-failure restarts until the
        # burst is exhausted, then degraded (never a silent block)
        return "resume_degraded"
    if error_class in PERMANENT_ERROR_CLASSES:
        return "resume_blocked"
    # everything else (db unavailable, transient admission) is retry_wait
    return "retry_wait"


def backoff_seconds(policy: HostPolicy, step: int) -> int:
    """Deterministic (jitter = 0) exponential backoff, capped at max."""
    if step <= 0:
        return max(1, policy.resume_retry_initial // 1_000_000_000)
    value = policy.resume_retry_initial * (policy.resume_retry_multiplier ** step)
    value = min(value, policy.resume_retry_max)
    return max(1, int(value // 1_000_000_000))


async def run_resume_probe(
    db: AsyncSession,
    *,
    host_lib_base: Path,
    policy_override: Path | None = None,
    policy_baseline: Path | None = None,
    boot_id: str = "boot",
) -> ResumeOutcome:
    """One resume probe: reconcile, admit, translate to an outcome.

    The journal record + events are published before the DB commit so the
    host chronology survives even if the DB write fails (transient)."""
    store = JournalStore(host_lib_base)
    res = store.reconcile()
    attempt_id = res.head_attempt_id or uuid.uuid4().hex
    record = store.read_record(attempt_id)
    if record is None:
        record = TransitionRecord(
            attempt_id=attempt_id,
            operation="resume",
            candidate_snapshot_id=None,
            base_snapshot_id=None,
            observed_pointer_tuple={},
            state=STATE_CHECKING,
        )

    # a DB outage surfaces as a connection error, not a typed problem: it
    # is transient (retry_wait with a long backoff, unbounded)
    try:
        report = await admission_check(
            db,
            host_lib_base=host_lib_base,
            policy_override=policy_override,
            policy_baseline=policy_baseline,
        )
    except Exception as exc:  # any probe failure (e.g. DB down) is transient here
        report = AdmissionReport(ok=False, problems=[f"db_unavailable: {type(exc).__name__}"])

    # pick the dominant error class (permanent wins over transient).
    # problems are "class: detail" (or "host_transition:subclass: detail")
    error_class: str | None = None
    if report.ok:
        outcome = "resolved"
    else:
        for problem in report.problems:
            for cls in PERMANENT_ERROR_CLASSES:
                if problem == cls or problem.startswith(cls + ":") or (":" + cls) in problem:
                    error_class = cls
                    break
            if error_class is not None:
                break
        if error_class is None:
            error_class = report.problems[0].split(":", 1)[0].strip() or "admission"
        outcome = classify(error_class)

    record.attempts_total += 1
    record.current_attempt_seq = record.attempts_total
    record.error_class = error_class
    record.last_probe_started_at = record.last_probe_classified_at = _now()
    record.observed_pointer_tuple = dict(report_problems_tuple(report))

    if outcome == "retry_wait":
        record.state = "retry_wait"
        record.backoff_step += 1
        record.next_attempt_at = _now()
        record.consecutive_unclassified_failures = 0
    elif outcome == "resume_blocked":
        record.state = "resume_blocked"
        record.consecutive_unclassified_failures = 0
    elif outcome == "resume_degraded":
        record.consecutive_unclassified_failures += 1
        record.state = "resume_degraded"
    else:  # ready: admission passed
        record.state = STATE_RESOLVED

    store.write_record(record)
    event_seq = record.last_event_seq + 1
    store.write_event(
        attempt_id,
        event_seq,
        {
            "from_state": STATE_CHECKING,
            "to_state": record.state,
            "error_class": error_class,
            "outcome": outcome,
            "attempts_total": record.attempts_total,
        },
    )
    record.last_event_seq = event_seq
    record.replayed_through_seq = event_seq
    record.replayed_at = _now()
    store.write_record(record)
    if not store.head_exists():
        header = record_identity_header(record, "")
        store.write_head(record, initial_event_sha256=canonical_sha256(header), creation_boot_id=boot_id)

    return ResumeOutcome(outcome=outcome, exit_code=_exit_code(outcome), attempt_id=attempt_id)


def report_problems_tuple(report: AdmissionReport) -> dict[str, Any]:
    return {"problems": list(report.problems), "ok": report.ok}


def _exit_code(outcome: str) -> int:
    if outcome == "resume_blocked":
        return EXIT_RESUME_BLOCKED
    return EXIT_RETRY_WAIT  # retry_wait and resume_degraded both exit 0


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


async def replay_audit_events(
    db: AsyncSession,
    audit: AuditService,
    *,
    attempt_id: str,
    store: JournalStore,
) -> int:
    """Idempotently replay host events into the DB audit by
    ``(attempt_id, event_seq)``. Only the terminal/degraded outcomes are
    surfaced to the operator audit (transient retry_wait stays in the host
    journal). Returns the number newly recorded."""
    seqs = store.list_event_seqs(attempt_id)
    recorded = 0
    for seq in seqs:
        event = store._read_event(attempt_id, seq)
        outcome = str(event.get("outcome", ""))
        if outcome not in ("resume_blocked", "resume_degraded"):
            continue
        exists = (
            await db.execute(
                text(
                    "SELECT 1 FROM audit_events WHERE type=:t "
                    "AND payload->>'attempt_id'=:a AND payload->>'event_seq'=:s"
                ),
                {"t": AuditEventType.ALERT_RAISED.value, "a": attempt_id, "s": str(seq)},
            )
        ).scalar_one_or_none()
        if exists is not None:
            continue
        to_state = str(event.get("to_state", ""))
        await audit.record(
            AuditEventType.ALERT_RAISED,
            payload={
                "attempt_id": attempt_id,
                "event_seq": seq,
                "to_state": to_state,
                "outcome": outcome,
            },
            actor="resume-unit",
            public_summary=f"host resume: {outcome} (state={to_state})",
        )
        recorded += 1
    return recorded


def load_resume_policy(
    *,
    override: Path | None = None,
    baseline: Path | None = None,
) -> tuple[HostPolicy, str]:
    """Load the effective recovery policy (override first, else baseline).

    A present-but-invalid override is NOT silently fallen back: it is
    surfaced (the caller raises resume_blocked with host_policy_invalid).
    Returns (policy, source_kind)."""
    if override is not None and override.exists():
        try:
            return read_policy_file(override), "override"
        except PolicyError:
            # the caller must treat this as host_policy_invalid
            raise
    if baseline is not None:
        return read_policy_file(baseline), "baseline"
    raise PolicyError("no recovery policy source available (override and baseline both absent)")
