"""Root-published systemd projection tests."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from apps.runtime.unit_state import publish_unit_state
from apps.web.host_status import FilesystemHostStatusReader

_NOW = datetime(2026, 8, 5, 12, 0, tzinfo=UTC)
_BOOT_ID = UUID("36ab89ef-3d01-4f88-a81d-c86b706810df")


def _show(unit: str, _properties: Sequence[str]) -> Mapping[str, str]:
    if unit == "noezema-runtime.target":
        return {
            "Id": unit,
            "ActiveState": "active",
            "SubState": "active",
            "ConsistsOf": "noezema-worker.service noezema-orchestrator.service",
        }
    return {
        "Id": unit,
        "ActiveState": "active",
        "SubState": "running",
        "Result": "success",
    }


def test_publisher_writes_an_atomic_snapshot_accepted_by_the_web_reader(tmp_path: Path) -> None:
    boot_id_path = tmp_path / "boot-id"
    output_path = tmp_path / "unit-state.json"
    boot_id_path.write_text(f"{_BOOT_ID}\n", encoding="ascii")

    payload = publish_unit_state(
        output_path=output_path,
        boot_id_path=boot_id_path,
        clock=lambda: _NOW,
        show_unit=_show,
    )

    persisted = json.loads(output_path.read_text(encoding="utf-8"))
    assert persisted == payload
    assert [member["name"] for member in persisted["members"]] == [
        "noezema-orchestrator.service",
        "noezema-worker.service",
    ]
    assert not tuple(tmp_path.glob(".unit-state.json.*.tmp"))

    status = FilesystemHostStatusReader(
        unit_state_path=output_path,
        boot_id_path=boot_id_path,
        maintenance_marker_path=tmp_path / "maintenance",
        transition_head_path=tmp_path / "transition",
        policy_change_head_path=tmp_path / "policy-change",
    ).status(observed_at=_NOW + timedelta(seconds=5))
    assert status.snapshot_state == "current"
    assert status.reasons == ()
    assert status.snapshot_age_seconds == 5


def test_publisher_refuses_an_empty_runtime_inventory(tmp_path: Path) -> None:
    boot_id_path = tmp_path / "boot-id"
    output_path = tmp_path / "unit-state.json"
    boot_id_path.write_text(str(_BOOT_ID), encoding="ascii")

    def empty_target(unit: str, _properties: Sequence[str]) -> Mapping[str, str]:
        return {
            "Id": unit,
            "ActiveState": "inactive",
            "SubState": "dead",
            "Result": "success",
            "ConsistsOf": "",
        }

    with pytest.raises(RuntimeError, match="no valid ConsistsOf"):
        publish_unit_state(
            output_path=output_path,
            boot_id_path=boot_id_path,
            clock=lambda: _NOW,
            show_unit=empty_target,
        )
    assert not output_path.exists()


@pytest.mark.skipif(
    not Path("/proc/sys/kernel/random/boot_id").exists(),
    reason="procfs boot ID is only available on Linux",
)
def test_procfs_boot_id_is_accepted_despite_zero_reported_size(tmp_path: Path) -> None:
    boot_id_path = Path("/proc/sys/kernel/random/boot_id")
    output_path = tmp_path / "unit-state.json"

    publish_unit_state(
        output_path=output_path,
        boot_id_path=boot_id_path,
        clock=lambda: _NOW,
        show_unit=_show,
    )
    status = FilesystemHostStatusReader(
        unit_state_path=output_path,
        boot_id_path=boot_id_path,
        maintenance_marker_path=tmp_path / "maintenance",
        transition_head_path=tmp_path / "transition",
        policy_change_head_path=tmp_path / "policy-change",
    ).status(observed_at=_NOW)

    assert status.snapshot_state == "current"
    assert status.boot_id == UUID(boot_id_path.read_text(encoding="ascii").strip())
