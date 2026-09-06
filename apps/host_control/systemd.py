"""Strict systemd target inventory and lifecycle helpers for host maintenance."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence

_TARGET = "noezema-runtime.target"


def stop_and_verify_runtime_target(
    *,
    run: Callable[[Sequence[str], int], int] | None = None,
    show: Callable[[str, Sequence[str]], Mapping[str, str]] | None = None,
) -> tuple[str, ...]:
    """Stop the cognitive target and prove every canonical member is inactive."""

    execute = run or _run
    inspect = show or _show
    if execute(("systemctl", "stop", _TARGET), 50) != 0:
        raise RuntimeError("systemd rejected runtime target stop")
    target = inspect(_TARGET, ("Id", "ActiveState", "ConsistsOf", "Requires", "Wants"))
    if target.get("Id") != _TARGET or target.get("ActiveState") != "inactive":
        raise RuntimeError("runtime target did not become inactive")
    members = tuple(sorted(set(target.get("ConsistsOf", "").split())))
    if not members or any(not member.endswith(".service") for member in members):
        raise RuntimeError("runtime target has no valid ConsistsOf inventory")
    direct = set(target.get("Requires", "").split()) | set(target.get("Wants", "").split())
    if any(member not in direct for member in members):
        raise RuntimeError("runtime target inventory contains an indirect member")
    for member in members:
        values = inspect(member, ("Id", "ActiveState", "PartOf"))
        if (
            values.get("Id") != member
            or values.get("ActiveState") != "inactive"
            or _TARGET not in values.get("PartOf", "").split()
        ):
            raise RuntimeError(f"runtime member did not quiesce: {member}")
    return members


def _run(arguments: Sequence[str], timeout: int) -> int:
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
        timeout=timeout,
    )
    return completed.returncode


def _show(unit: str, properties: Sequence[str]) -> Mapping[str, str]:
    arguments = ["systemctl", "show", unit, "--no-pager"]
    arguments.extend(f"--property={name}" for name in properties)
    completed = subprocess.run(
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        check=False,
        timeout=5,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"systemctl show failed for {unit}")
    values: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in properties:
            values[key] = value
    if any(name not in values for name in properties):
        raise RuntimeError(f"systemctl omitted required properties for {unit}")
    return values
