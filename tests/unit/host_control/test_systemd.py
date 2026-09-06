"""Canonical runtime inventory checks used before offline DB writes."""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from apps.host_control.systemd import stop_and_verify_runtime_target


def test_stop_verifies_direct_part_of_members_and_inactive_state() -> None:
    calls: list[tuple[str, ...]] = []

    def run(arguments: Sequence[str], _timeout: int) -> int:
        calls.append(tuple(arguments))
        return 0

    def show(unit: str, _properties: Sequence[str]) -> dict[str, str]:
        if unit == "noezema-runtime.target":
            return {
                "Id": unit,
                "ActiveState": "inactive",
                "ConsistsOf": "noezema-orchestrator.service",
                "Requires": "noezema-orchestrator.service noezema-runtime-admission.service",
                "Wants": "",
            }
        return {
            "Id": unit,
            "ActiveState": "inactive",
            "PartOf": "noezema-runtime.target",
        }

    assert stop_and_verify_runtime_target(run=run, show=show) == (
        "noezema-orchestrator.service",
    )
    assert calls == [("systemctl", "stop", "noezema-runtime.target")]


def test_stop_fails_closed_when_any_member_remains_active() -> None:
    def show(unit: str, _properties: Sequence[str]) -> dict[str, str]:
        if unit == "noezema-runtime.target":
            return {
                "Id": unit,
                "ActiveState": "inactive",
                "ConsistsOf": "noezema-orchestrator.service",
                "Requires": "noezema-orchestrator.service",
                "Wants": "",
            }
        return {
            "Id": unit,
            "ActiveState": "active",
            "PartOf": "noezema-runtime.target",
        }

    with pytest.raises(RuntimeError, match="did not quiesce"):
        stop_and_verify_runtime_target(run=lambda _args, _timeout: 0, show=show)
