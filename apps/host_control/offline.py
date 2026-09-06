"""Privileged systemd entry point for one offline rules-maintenance operation."""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from apps.host_control.atomic import atomic_write, durable_unlink, read_bounded_regular
from apps.host_control.journal import (
    HostJournalError,
    HostJournalPaths,
    HostOperation,
    HostPolicyChangeInProgressError,
    HostTransitionInProgressError,
    HostTransitionJournal,
    HostTransitionState,
)
from apps.host_control.lock import HostTransitionLockTimeoutError
from apps.host_control.offline_rules import (
    OfflineActivationError,
    OfflineRulesActivator,
    validate_offline_rules_payload,
)
from apps.host_control.policy import load_host_recovery_policy
from apps.host_control.recovery import read_boot_id
from apps.host_control.systemd import stop_and_verify_runtime_target
from packages.persistence import create_session_factory

_LOGGER = logging.getLogger(__name__)


def run_offline_rules(
    payload: dict[str, object],
    *,
    journal: HostTransitionJournal,
    policy,
    boot_id: UUID,
    marker_path: Path,
    activator: OfflineRulesActivator,
    stop_and_verify: Callable[[], tuple[str, ...]],
    clock: Callable[[], datetime] | None = None,
) -> int:
    """Own the marker, quiesce all writers and publish or leave resume work."""

    validate_offline_rules_payload(payload)
    now = _aware((clock or (lambda: datetime.now(UTC)))())
    try:
        candidate_id, base_id = activator.describe_candidate(payload)
    except (OfflineActivationError, OSError, SQLAlchemyError, ValueError):
        _LOGGER.exception("offline rules candidate could not be described")
        return 75
    try:
        record = journal.create(
            operation=HostOperation.OFFLINE_RULES,
            boot_id=boot_id,
            policy=policy,
            actor="offline-rules",
            reason="offline rules maintenance opened",
            occurred_at=now,
            candidate_snapshot_id=candidate_id,
            base_snapshot_id=base_id,
            observed_active_snapshot_id=base_id,
            initial_snapshot_updates={
                "attempts_total": 1,
                "current_attempt_seq": 1,
                "last_probe_boot_id": boot_id,
                "last_probe_started_at": now,
            },
        )
    except (
        HostPolicyChangeInProgressError,
        HostTransitionInProgressError,
        HostTransitionLockTimeoutError,
    ):
        return 75
    except HostJournalError:
        return 78
    marker_owned = False
    try:
        try:
            marker_path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise RuntimeError("offline maintenance marker already exists")
        atomic_write(
            marker_path,
            json.dumps(
                {
                    "schema_version": "offline-rules-owner/v1",
                    "attempt_id": str(record.attempt_id),
                },
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8"),
        )
        marker_owned = True
        members = stop_and_verify()
        _LOGGER.info("quiesced runtime members: %s", ",".join(members))
        activator.activate(payload)
    except (OfflineActivationError, RuntimeError, OSError, SQLAlchemyError, ValueError):
        _LOGGER.exception("offline rules maintenance failed before safe completion")
        current = journal.active()
        if current is not None and current.attempt_id == record.attempt_id:
            journal.transition(
                record.attempt_id,
                to_state=HostTransitionState.RESUME_DEGRADED,
                actor="offline-rules",
                reason="maintenance failed; runtime outcome requires reclassification",
                occurred_at=_aware((clock or (lambda: datetime.now(UTC)))()),
                error_class="offline_rules_failed",
                snapshot_updates={
                    "last_probe_classified_at": None,
                    "next_attempt_at": now,
                    "dispatch_id": None,
                    "dispatch_deadline": None,
                },
            )
        return 1
    finally:
        if marker_owned:
            try:
                durable_unlink(marker_path)
            except OSError:
                _LOGGER.exception("offline marker cleanup failed")
    return 0


def main(environment: Mapping[str, str] | None = None) -> int:
    values = os.environ if environment is None else environment
    database_url = values.get("NOEZEMA_DATABASE_URL", "").strip()
    payload_path_value = values.get("NOEZEMA_OFFLINE_RULES_PAYLOAD_PATH", "").strip()
    if not database_url or not payload_path_value:
        _LOGGER.critical("database URL and offline rules payload path are required")
        return 78
    try:
        decoded = json.loads(
            read_bounded_regular(Path(payload_path_value), maximum_bytes=1024 * 1024)
        )
        if not isinstance(decoded, dict):
            raise ValueError("offline payload must be a JSON object")
        validate_offline_rules_payload(decoded)
        baseline = Path(
            values.get(
                "NOEZEMA_HOST_RECOVERY_DEFAULTS_PATH",
                "/usr/lib/noezema/host-recovery.defaults.toml",
            )
        )
        override = values.get(
            "NOEZEMA_HOST_RECOVERY_OVERRIDE_PATH",
            "/etc/noezema/host-recovery.toml",
        ).strip()
        policy = load_host_recovery_policy(
            baseline,
            override_path=Path(override) if override else None,
        )
        boot_id = read_boot_id(
            Path(values.get("NOEZEMA_BOOT_ID_PATH", "/proc/sys/kernel/random/boot_id"))
        )
    except (OSError, ValueError) as exc:
        _LOGGER.critical("offline rules prerequisites are invalid: %s", exc)
        return 78

    engine, session_factory = create_session_factory(database_url)
    try:
        return run_offline_rules(
            decoded,
            journal=HostTransitionJournal(
                HostJournalPaths(
                    Path(values.get("NOEZEMA_HOST_STATE_ROOT", "/var/lib/noezema")),
                    lock_path=Path(
                        values.get(
                            "NOEZEMA_HOST_TRANSITION_LOCK_PATH",
                            "/run/lock/noezema-host-transition.lock",
                        )
                    ),
                )
            ),
            policy=policy,
            boot_id=boot_id,
            marker_path=Path(
                values.get(
                    "NOEZEMA_MAINTENANCE_MARKER_PATH",
                    "/run/noezema-offline-rules/active",
                )
            ),
            activator=OfflineRulesActivator(session_factory),
            stop_and_verify=stop_and_verify_runtime_target,
        )
    finally:
        engine.dispose()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("offline maintenance clock must be timezone-aware")
    return value.astimezone(UTC)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())
