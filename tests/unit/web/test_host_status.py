"""Fail-closed host snapshot validation for the observer-plane web service."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

from apps.web.host_status import FilesystemHostStatusReader
from apps.web.models import DegradedReason
from packages.domain import canonical_json_sha256

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=UTC)
BOOT_ID = UUID("11111111-2222-4333-8444-555555555555")


def _reader(root: Path) -> FilesystemHostStatusReader:
    return FilesystemHostStatusReader(
        unit_state_path=root / "unit-state.json",
        boot_id_path=root / "boot-id",
        maintenance_marker_path=root / "maintenance-active",
        transition_head_path=root / "transition-head.json",
        policy_change_head_path=root / "policy-head.json",
        snapshot_ttl_seconds=15,
    )


def _write_snapshot(
    root: Path,
    *,
    observed_at: datetime = NOW,
    boot_id: UUID = BOOT_ID,
    target_state: str = "active",
    member_state: str = "active",
    member_result: str = "success",
) -> None:
    members = ["noezema-orchestrator.service"]
    payload = {
        "schema_version": "unit-state/v1",
        "boot_id": str(boot_id),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "publisher_result": "ok",
        "target": {
            "name": "noezema-runtime.target",
            "active_state": target_state,
            "sub_state": "active" if target_state == "active" else "dead",
            "result": "success",
        },
        "members": [
            {
                "name": members[0],
                "active_state": member_state,
                "sub_state": "running" if member_state == "active" else "dead",
                "result": member_result,
            }
        ],
        "inventory_sha256": canonical_json_sha256({"members": members}),
    }
    (root / "unit-state.json").write_text(
        json.dumps(payload, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def test_current_snapshot_reports_healthy_runtime(tmp_path: Path) -> None:
    (tmp_path / "boot-id").write_text(str(BOOT_ID), encoding="ascii")
    _write_snapshot(tmp_path)

    status = _reader(tmp_path).status(observed_at=NOW)

    assert status.snapshot_state == "current"
    assert status.snapshot_age_seconds == 0
    assert status.target is not None and status.target.active_state == "active"
    assert status.reasons == ()


def test_missing_stale_and_wrong_boot_snapshots_fail_closed(tmp_path: Path) -> None:
    reader = _reader(tmp_path)
    missing = reader.status(observed_at=NOW)
    assert missing.reasons == (DegradedReason.UNIT_STATE_MISSING,)

    (tmp_path / "boot-id").write_text(str(BOOT_ID), encoding="ascii")
    _write_snapshot(tmp_path, observed_at=NOW - timedelta(seconds=16))
    stale = reader.status(observed_at=NOW)
    assert stale.snapshot_state == "stale"
    assert stale.reasons == (DegradedReason.UNIT_STATE_STALE,)

    _write_snapshot(tmp_path, boot_id=UUID(int=9))
    wrong_boot = reader.status(observed_at=NOW)
    assert wrong_boot.snapshot_state == "invalid"
    assert wrong_boot.reasons == (DegradedReason.BOOT_ID_MISMATCH,)


def test_markers_and_unhealthy_members_are_reported_together(tmp_path: Path) -> None:
    (tmp_path / "boot-id").write_text(str(BOOT_ID), encoding="ascii")
    _write_snapshot(tmp_path, member_state="failed")
    (tmp_path / "maintenance-active").write_text("active", encoding="ascii")
    (tmp_path / "transition-head.json").write_text("{}", encoding="ascii")
    (tmp_path / "policy-head.json").write_text("{}", encoding="ascii")

    status = _reader(tmp_path).status(observed_at=NOW)

    assert status.reasons == (
        DegradedReason.RUNTIME_MEMBER_UNHEALTHY,
        DegradedReason.MAINTENANCE_ACTIVE,
        DegradedReason.HOST_TRANSITION_IN_PROGRESS,
        DegradedReason.HOST_POLICY_CHANGE_IN_PROGRESS,
    )


def test_active_member_with_failed_result_is_not_reported_as_healthy(tmp_path: Path) -> None:
    (tmp_path / "boot-id").write_text(str(BOOT_ID), encoding="ascii")
    _write_snapshot(tmp_path, member_result="exit-code")

    status = _reader(tmp_path).status(observed_at=NOW)

    assert status.reasons == (DegradedReason.RUNTIME_MEMBER_UNHEALTHY,)
