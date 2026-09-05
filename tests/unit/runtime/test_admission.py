"""Fail-closed runtime admission tests."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from apps.runtime.admission import (
    RuntimeAdmissionRejectedError,
    RuntimeAdmissionTemporaryError,
    check_runtime_admission,
)
from packages.persistence import INVALID_QUESTION_NAMESPACE
from packages.persistence.models import (
    ConfigSnapshotRecord,
    RuntimeConfigHeadRecord,
    SystemConstantRecord,
)


def _paths(root: Path) -> dict[str, Path]:
    return {
        "maintenance_marker_path": root / "maintenance",
        "transition_head_path": root / "transition.json",
        "policy_change_head_path": root / "policy-change.json",
    }


def _seed_system_constant(session_factory: sessionmaker[Session]) -> None:
    with session_factory.begin() as db:
        db.add(
            SystemConstantRecord(
                key="invalid_question_uuid5_namespace",
                value=str(INVALID_QUESTION_NAMESPACE),
                created_at=datetime(2026, 1, 1, tzinfo=UTC),
            )
        )


def test_admission_accepts_the_bootstrapped_idle_runtime(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)

    check_runtime_admission(session_factory, **_paths(tmp_path))


@pytest.mark.parametrize("active_path", ("maintenance", "transition.json", "policy-change.json"))
def test_admission_rejects_every_unresolved_host_operation(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
    active_path: str,
) -> None:
    (tmp_path / active_path).write_text("active", encoding="utf-8")

    with pytest.raises(RuntimeAdmissionRejectedError):
        check_runtime_admission(session_factory, **_paths(tmp_path))


def test_admission_rejects_an_unfinished_configuration_activation(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    with session_factory.begin() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        head.activating_config_snapshot_id = head.active_config_snapshot_id

    with pytest.raises(RuntimeAdmissionRejectedError, match="activation is unfinished"):
        check_runtime_admission(session_factory, **_paths(tmp_path))


def test_admission_rejects_a_changed_system_constant(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    with session_factory.begin() as db:
        constant = db.get(SystemConstantRecord, "invalid_question_uuid5_namespace")
        assert constant is not None
        constant.value = "changed"

    with pytest.raises(RuntimeAdmissionRejectedError, match="system constant"):
        check_runtime_admission(session_factory, **_paths(tmp_path))


def test_admission_recomputes_the_bootstrap_payload_fingerprint(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    _seed_system_constant(session_factory)
    with session_factory.begin() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        active = db.get(ConfigSnapshotRecord, head.active_config_snapshot_id)
        assert active is not None
        active.payload = {**active.payload, "tampered": True}

    with pytest.raises(RuntimeAdmissionRejectedError, match="configuration is invalid"):
        check_runtime_admission(session_factory, **_paths(tmp_path))


def test_admission_classifies_database_outage_as_temporary(tmp_path: Path) -> None:
    def unavailable() -> Session:
        raise OperationalError("SELECT 1", {}, RuntimeError("offline"))

    with pytest.raises(RuntimeAdmissionTemporaryError, match="database is unavailable"):
        check_runtime_admission(unavailable, **_paths(tmp_path))
