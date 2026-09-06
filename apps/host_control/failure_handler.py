"""Convert an exhausted, unclassified systemd restart burst into slow recovery."""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

from apps.host_control.journal import (
    HostJournalPaths,
    HostTransitionJournal,
    HostTransitionState,
)
from apps.host_control.policy import HostRecoveryPolicy

_LOGGER = logging.getLogger(__name__)


def degrade_unclassified_failure(
    journal: HostTransitionJournal,
    *,
    occurred_at: datetime,
    reset_failed: Callable[[], None],
) -> bool:
    """Persist degradation only when the current probe lacks classification."""

    record = journal.active()
    if record is None or record.state is HostTransitionState.RESUME_BLOCKED:
        return False
    started = record.snapshot.last_probe_started_at
    classified = record.snapshot.last_probe_classified_at
    checking_is_classified = (
        record.state is HostTransitionState.CHECKING
        and classified is not None
        and started is not None
        and classified >= started
    )
    if record.state not in {
        HostTransitionState.CHECKING,
        HostTransitionState.READY_TO_START,
    } or (record.state is HostTransitionState.CHECKING and (started is None or checking_is_classified)):
        return False
    policy = HostRecoveryPolicy.model_validate(record.policy.values)
    journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.RESUME_DEGRADED,
        actor="systemd-failure-handler",
        reason="unclassified resume restart burst exhausted",
        occurred_at=_aware(occurred_at),
        error_class="resume_crash_loop",
        snapshot_updates={
            "consecutive_unclassified_failures": (
                record.snapshot.consecutive_unclassified_failures + 1
            ),
            "backoff_step": record.snapshot.backoff_step + 1,
            "next_attempt_at": _aware(occurred_at)
            + timedelta(microseconds=policy.resume_retry_max_ns / 1_000),
            "dispatch_id": None,
            "dispatch_deadline": None,
        },
    )
    reset_failed()
    return True


def main(environment: Mapping[str, str] | None = None) -> int:
    values = os.environ if environment is None else environment
    source_unit = values.get("NOEZEMA_FAILED_RESUME_UNIT", "").strip()
    if source_unit not in {
        "noezema-runtime-resume.service",
        "noezema-runtime-resume-retry.service",
    }:
        _LOGGER.critical("unexpected failed resume unit: %s", source_unit)
        return 78
    journal = HostTransitionJournal(
        HostJournalPaths(Path(values.get("NOEZEMA_HOST_STATE_ROOT", "/var/lib/noezema")))
    )

    def reset() -> None:
        completed = subprocess.run(
            ("systemctl", "reset-failed", source_unit),
            check=False,
            timeout=10,
        )
        if completed.returncode != 0:
            raise RuntimeError("systemctl reset-failed was rejected")

    try:
        changed = degrade_unclassified_failure(
            journal,
            occurred_at=datetime.now(UTC),
            reset_failed=reset,
        )
    except (OSError, RuntimeError, ValueError):
        _LOGGER.exception("resume failure handler could not persist degradation")
        return 1
    if changed:
        _LOGGER.critical("resume entered degraded slow-retry mode")
    return 0


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("failure-handler timestamp must be timezone-aware")
    return value.astimezone(UTC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
