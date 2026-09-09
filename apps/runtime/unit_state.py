"""Publish a bounded systemd state snapshot for the unprivileged web observer."""

from __future__ import annotations

import json
import os
import stat
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from packages.domain import canonical_json_sha256

_TARGET = "noezema-runtime.target"
_PROPERTIES = ("Id", "ActiveState", "SubState", "Result")


def publish_unit_state(
    *,
    output_path: Path,
    boot_id_path: Path,
    clock: Callable[[], datetime] | None = None,
    show_unit: Callable[[str, Sequence[str]], Mapping[str, str]] | None = None,
) -> dict[str, object]:
    observed_at = _aware((clock or (lambda: datetime.now(UTC)))())
    boot_id = UUID(_read_regular_file(boot_id_path, 128).decode("ascii").strip())
    show = show_unit or _systemctl_show
    target_values = show(_TARGET, (*_PROPERTIES, "ConsistsOf"))
    member_names = tuple(sorted(set(target_values.get("ConsistsOf", "").split())))
    if not member_names or any(not name.endswith(".service") for name in member_names):
        raise RuntimeError("runtime target has no valid ConsistsOf service inventory")
    target = _unit_projection(_TARGET, target_values)
    members = [_unit_projection(name, show(name, _PROPERTIES)) for name in member_names]
    payload: dict[str, object] = {
        "schema_version": "unit-state/v1",
        "boot_id": str(boot_id),
        "observed_at": observed_at.isoformat().replace("+00:00", "Z"),
        "publisher_result": "ok",
        "target": target,
        "members": members,
        "inventory_sha256": canonical_json_sha256({"members": list(member_names)}),
    }
    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    _atomic_write(output_path, encoded)
    return payload


def main() -> int:
    output_path = Path(os.environ.get("NOEZEMA_UNIT_STATE_PATH", "/run/noezema/unit-state.json"))
    boot_id_path = Path(os.environ.get("NOEZEMA_BOOT_ID_PATH", "/proc/sys/kernel/random/boot_id"))
    publish_unit_state(output_path=output_path, boot_id_path=boot_id_path)
    return 0


def _systemctl_show(unit: str, properties: Sequence[str]) -> Mapping[str, str]:
    arguments = ["systemctl", "show", unit, "--no-pager"]
    arguments.extend(f"--property={name}" for name in properties)
    completed = subprocess.run(  # noqa: S603 - fixed executable and closed unit inventory
        arguments,
        stdin=subprocess.DEVNULL,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="strict",
        timeout=3,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"systemctl show failed for {unit}")
    result: dict[str, str] = {}
    for line in completed.stdout.splitlines():
        key, separator, value = line.partition("=")
        if separator and key in properties:
            result[key] = value
    if any(name not in result for name in properties):
        raise RuntimeError(f"systemctl omitted required properties for {unit}")
    return result


def _unit_projection(expected_name: str, values: Mapping[str, str]) -> dict[str, str]:
    if values.get("Id") != expected_name:
        raise RuntimeError(f"systemd returned the wrong unit identity for {expected_name}")
    return {
        "name": expected_name,
        "active_state": values.get("ActiveState") or "unknown",
        "sub_state": values.get("SubState") or "unknown",
        "result": values.get("Result") or "unknown",
    }


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4()}.tmp")
    try:
        with temporary.open("xb") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        if os.name != "nt":
            temporary.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
        os.replace(temporary, path)
        _fsync_directory(path.parent)
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _read_regular_file(path: Path, maximum_bytes: int) -> bytes:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RuntimeError(f"{path} is not a bounded regular file") from exc
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise RuntimeError(f"{path} is not a bounded regular file")
        content = os.read(descriptor, maximum_bytes + 1)
        if not 1 <= len(content) <= maximum_bytes:
            raise RuntimeError(f"{path} is not a bounded regular file")
        return content
    finally:
        os.close(descriptor)


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("unit-state clock must be timezone-aware")
    return value.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
