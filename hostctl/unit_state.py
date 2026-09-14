"""Unit-state publisher (T3.16, §8.7.1.1).

``noezema-unit-state.service`` (on a 1s timer) reads the state of every
noezema unit and publishes a single snapshot to
``/run/noezema/unit-state.json``:

```json
{
  "schema_version": 1,
  "boot_id": "....",
  "generated_at": "2026-...",
  "ttl_seconds": 15,
  "units": { "noezema-runtime.target": "active", ... }
}
```

The snapshot is the web's unit-state source; the web treats a snapshot
older than ``ttl_seconds`` (or a missing one) as UNHEALTHY (fail-closed).
The publisher is a plain fsync-safe write, so a crash mid-write never
leaves a torn file.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hostctl.journal import fsync_write_atomic

UNIT_STATE_SCHEMA_VERSION = 1
UNIT_STATE_TTL_SECONDS = 15
UNIT_STATE_MODE = 0o644

DEFAULT_UNITS = (
    "noezema-runtime.target",
    "noezema-runtime-admission.service",
    "noezema-runtime-resume.service",
    "noezema-offline-rules.service",
    "noezema-web.service",
)


@dataclass
class UnitStateSnapshot:
    boot_id: str
    units: dict[str, str] = field(default_factory=dict)
    generated_at: str = field(default_factory=lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    schema_version: int = UNIT_STATE_SCHEMA_VERSION
    ttl_seconds: int = UNIT_STATE_TTL_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "boot_id": self.boot_id,
            "generated_at": self.generated_at,
            "ttl_seconds": self.ttl_seconds,
            "units": dict(self.units),
        }

    def age_seconds(self) -> float:
        """How old the snapshot is (for the web's TTL health check)."""
        import datetime

        gen = datetime.datetime.strptime(self.generated_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=datetime.UTC
        )
        now = datetime.datetime.now(datetime.UTC)
        return (now - gen).total_seconds()

    def is_fresh(self, *, max_age: float | None = None) -> bool:
        limit = self.ttl_seconds if max_age is None else max_age
        return self.age_seconds() <= limit


def read_boot_id() -> str:
    try:
        return Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    except OSError:
        return uuid.uuid4().hex


def publish_unit_state(
    path: Path,
    *,
    units: dict[str, str],
    boot_id: str | None = None,
) -> UnitStateSnapshot:
    """Build + fsync-publish the unit-state snapshot. Returns the snapshot."""
    snapshot = UnitStateSnapshot(boot_id=boot_id or read_boot_id(), units=dict(units))
    import json

    payload = json.dumps(snapshot.to_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    fsync_write_atomic(path, payload, mode=UNIT_STATE_MODE)
    return snapshot


def read_unit_state(path: Path) -> UnitStateSnapshot | None:
    import json

    if not path.exists():
        return None
    try:
        data: dict[str, Any] = json.loads(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError):
        return None
    return UnitStateSnapshot(
        boot_id=str(data.get("boot_id", "")),
        units=dict(data.get("units", {})),
        generated_at=str(data.get("generated_at", "")),
        schema_version=int(data.get("schema_version", UNIT_STATE_SCHEMA_VERSION)),
        ttl_seconds=int(data.get("ttl_seconds", UNIT_STATE_TTL_SECONDS)),
    )


def collect_unit_states() -> dict[str, str]:
    """Best-effort read of every noezema unit's active state.

    Uses ``systemctl is-active`` per unit when available; otherwise
    returns an empty inventory (the web treats a missing/empty snapshot
    as unhealthy)."""
    import shutil
    import subprocess

    states: dict[str, str] = {}
    if shutil.which("systemctl") is None:
        return states
    for unit in DEFAULT_UNITS:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            states[unit] = result.stdout.strip() or "unknown"
        except (OSError, subprocess.SubprocessError):
            states[unit] = "unknown"
    return states
