"""Fail-closed shared admission check for every systemd runtime start."""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from packages.domain import canonical_json_sha256
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    BOOTSTRAP_PAYLOAD_SHA256,
    BOOTSTRAP_REVISION_SHA256,
    INVALID_QUESTION_NAMESPACE,
    create_session_factory,
)
from packages.persistence.models import (
    ConfigSnapshotRecord,
    RuntimeConfigHeadRecord,
    RuntimeControlRecord,
    SystemConstantRecord,
)

_LOGGER = logging.getLogger(__name__)


class RuntimeAdmissionRejectedError(RuntimeError):
    """A durable or host invariant forbids starting cognitive writers."""


class RuntimeAdmissionTemporaryError(RuntimeError):
    """Admission could not classify the durable state because the DB is unavailable."""


def check_runtime_admission(
    session_factory: Callable[[], Session],
    *,
    maintenance_marker_path: Path,
    transition_head_path: Path,
    policy_change_head_path: Path,
) -> None:
    for path, reason in (
        (maintenance_marker_path, "offline maintenance is active"),
        (transition_head_path, "host transition is unresolved"),
        (policy_change_head_path, "host policy change is unresolved"),
    ):
        if _present_or_unreadable(path):
            raise RuntimeAdmissionRejectedError(reason)

    try:
        with session_factory() as db:
            head_count = db.scalar(select(func.count()).select_from(RuntimeConfigHeadRecord))
            runtime_count = db.scalar(select(func.count()).select_from(RuntimeControlRecord))
            head = db.get(RuntimeConfigHeadRecord, "global")
            runtime = db.get(RuntimeControlRecord, "global")
            bootstrap = db.get(ConfigSnapshotRecord, BOOTSTRAP_CONFIG_SNAPSHOT_ID)
            namespace = db.get(SystemConstantRecord, "invalid_question_uuid5_namespace")
            active = (
                db.get(ConfigSnapshotRecord, head.active_config_snapshot_id)
                if head is not None
                else None
            )
    except SQLAlchemyError as exc:
        raise RuntimeAdmissionTemporaryError("operational database is unavailable") from exc

    if head_count != 1 or runtime_count != 1 or head is None or runtime is None:
        raise RuntimeAdmissionRejectedError("global runtime singleton invariant failed")
    if head.activating_config_snapshot_id is not None:
        raise RuntimeAdmissionRejectedError("configuration activation is unfinished")
    if (head.lease_owner is None) != (head.lease_expires_at is None):
        raise RuntimeAdmissionRejectedError("runtime configuration lease tuple is invalid")
    if (
        active is None
        or active.activation_state != "active"
        or not _valid_config_fingerprint(active)
    ):
        raise RuntimeAdmissionRejectedError("active runtime configuration is invalid")
    if (
        bootstrap is None
        or not _valid_config_fingerprint(bootstrap)
        or bootstrap.payload_sha256 != BOOTSTRAP_PAYLOAD_SHA256
        or bootstrap.sha != BOOTSTRAP_REVISION_SHA256
    ):
        raise RuntimeAdmissionRejectedError("bootstrap configuration fingerprint mismatch")
    if namespace is None or namespace.value != str(INVALID_QUESTION_NAMESPACE):
        raise RuntimeAdmissionRejectedError("system constant fingerprint mismatch")


def main(environment: Mapping[str, str] | None = None) -> int:
    values = os.environ if environment is None else environment
    database_url = values.get("NOEZEMA_DATABASE_URL", "").strip()
    if not database_url:
        _LOGGER.critical("NOEZEMA_DATABASE_URL is required")
        return 78
    engine, session_factory = create_session_factory(database_url)
    try:
        try:
            check_runtime_admission(
                session_factory,
                maintenance_marker_path=Path(
                    values.get(
                        "NOEZEMA_MAINTENANCE_MARKER_PATH",
                        "/run/noezema-offline-rules/active",
                    )
                ),
                transition_head_path=Path(
                    values.get(
                        "NOEZEMA_HOST_TRANSITION_HEAD_PATH",
                        "/var/lib/noezema/host-transition-head.json",
                    )
                ),
                policy_change_head_path=Path(
                    values.get(
                        "NOEZEMA_HOST_POLICY_CHANGE_HEAD_PATH",
                        "/var/lib/noezema/host-policy-change-head.json",
                    )
                ),
            )
        except RuntimeAdmissionTemporaryError:
            _LOGGER.warning("runtime admission deferred: operational database unavailable")
            return 75
        except RuntimeAdmissionRejectedError as exc:
            _LOGGER.critical("runtime admission rejected: %s", exc)
            return 78
    finally:
        engine.dispose()
    _LOGGER.info("runtime admission accepted at %s", datetime.now(UTC).isoformat())
    return 0


def _present_or_unreadable(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _valid_config_fingerprint(record: ConfigSnapshotRecord) -> bool:
    payload_sha256 = canonical_json_sha256(record.payload)
    revision_sha256 = canonical_json_sha256(
        {
            "base_snapshot_id": (
                str(record.base_snapshot_id) if record.base_snapshot_id is not None else None
            ),
            "payload_sha256": payload_sha256,
        }
    )
    return record.payload_sha256 == payload_sha256 and record.sha == revision_sha256


if __name__ == "__main__":
    raise SystemExit(main())
