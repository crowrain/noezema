"""systemd entry point for boot and timer-driven runtime recovery."""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from apps.host_control.journal import (
    HostJournalError,
    HostJournalPaths,
    HostOperation,
    HostTransitionJournal,
    HostTransitionRecord,
    HostTransitionState,
)
from apps.host_control.policy import (
    HostPolicyInvalidError,
    HostRecoveryPolicy,
    MaterializedHostPolicy,
    load_host_recovery_policy,
)
from apps.host_control.policy_change import (
    PolicyChangeJournal,
    observe_effective_policy,
)
from apps.host_control.recovery import HostRecoveryService, read_boot_id, start_runtime_target
from packages.persistence import create_session_factory

_LOGGER = logging.getLogger(__name__)


def main(environment: Mapping[str, str] | None = None) -> int:
    values = os.environ if environment is None else environment
    database_url = values.get("NOEZEMA_DATABASE_URL", "").strip()
    if not database_url:
        _LOGGER.critical("NOEZEMA_DATABASE_URL is required")
        return 78
    create_if_absent = values.get("NOEZEMA_RECOVERY_CREATE_IF_ABSENT", "1").strip() not in {
        "0",
        "false",
        "no",
    }
    paths = HostJournalPaths(
        Path(values.get("NOEZEMA_HOST_STATE_ROOT", "/var/lib/noezema")),
        lock_path=Path(
            values.get(
                "NOEZEMA_HOST_TRANSITION_LOCK_PATH",
                "/run/lock/noezema-host-transition.lock",
            )
        ),
    )
    if not create_if_absent and not _present_or_unreadable(paths.head):
        return 0
    journal = HostTransitionJournal(paths)
    baseline_path = Path(
        values.get(
            "NOEZEMA_HOST_RECOVERY_DEFAULTS_PATH",
            "/usr/lib/noezema/host-recovery.defaults.toml",
        )
    )
    override_value = values.get(
        "NOEZEMA_HOST_RECOVERY_OVERRIDE_PATH",
        "/etc/noezema/host-recovery.toml",
    ).strip()
    try:
        boot_id = read_boot_id(
            Path(values.get("NOEZEMA_BOOT_ID_PATH", "/proc/sys/kernel/random/boot_id"))
        )
        active = journal.active()
        if active is not None:
            policy = _materialized_record_policy(active)
        else:
            override_path = Path(override_value) if override_value else Path(
                "/etc/noezema/host-recovery.toml"
            )
            policy_change_journal = PolicyChangeJournal(paths)
            policy_change = policy_change_journal.active()
            policy, observed_sha256 = observe_effective_policy(
                baseline_path,
                override_path,
            )
            if policy_change is not None:
                policy_change = policy_change_journal.reconcile(
                    observed_canonical_sha256=(
                        policy.canonical_sha256 if policy is not None else None
                    ),
                    observed_source_file_sha256=observed_sha256,
                    actor="host-recovery",
                    reason="boot reconciliation of host policy change",
                    occurred_at=datetime.now(UTC),
                )
                if policy_change is not None and not policy_change.state.terminal:
                    _LOGGER.critical("host policy change requires explicit resolution")
                    return 78
            if policy is None:
                exc = HostPolicyInvalidError("selected host policy is invalid")
                return _record_invalid_policy(
                    journal,
                    baseline_path=baseline_path,
                    boot_id=boot_id,
                    error=exc,
                )
    except (HostJournalError, OSError, ValueError) as exc:
        _LOGGER.critical("host recovery prerequisites are invalid: %s", exc)
        return 78

    engine, session_factory = create_session_factory(database_url)
    try:
        service = HostRecoveryService(
            journal,
            session_factory,
            policy=policy,
            boot_id=boot_id,
            start_target=start_runtime_target,
            create_if_absent=create_if_absent,
        )
        return service.run_once()
    finally:
        engine.dispose()


def _materialized_record_policy(record: HostTransitionRecord) -> MaterializedHostPolicy:
    return MaterializedHostPolicy(
        policy=HostRecoveryPolicy.model_validate(record.policy.values),
        source_kind=record.policy.source_kind,
        source_path=Path(record.policy.source_path),
        source_file_sha256=record.policy.source_file_sha256,
        canonical_sha256=record.policy.canonical_sha256,
    )


def _record_invalid_policy(
    journal: HostTransitionJournal,
    *,
    baseline_path: Path,
    boot_id: UUID,
    error: HostPolicyInvalidError,
) -> int:
    try:
        emergency = load_host_recovery_policy(baseline_path)
        now = datetime.now(UTC)
        record = journal.create(
            operation=HostOperation.RUNTIME_START,
            boot_id=boot_id,
            policy=emergency,
            actor="host-recovery",
            reason="invalid host policy detected",
            occurred_at=now,
            initial_snapshot_updates={
                "attempts_total": 1,
                "current_attempt_seq": 1,
                "last_probe_boot_id": boot_id,
                "last_probe_started_at": now,
            },
        )
        journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.RESUME_BLOCKED,
            actor="host-recovery",
            reason=str(error)[:1024],
            error_class="host_policy_invalid",
            occurred_at=now,
            snapshot_updates={"last_probe_classified_at": now},
        )
    except (HostJournalError, OSError, ValueError) as journal_error:
        _LOGGER.critical("invalid policy could not be journaled: %s", journal_error)
    _LOGGER.critical("host recovery policy is invalid: %s", error)
    return 78


def _present_or_unreadable(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
