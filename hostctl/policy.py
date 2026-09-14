"""Host recovery policy — schema v1 (T3.10, T3.15, §8.7.1.1).

The durable backoff policy lives in a TOML file (packaged baseline
``/usr/lib/noezema/host-recovery.defaults.toml``, optional override
``/etc/noezema/host-recovery.toml``). The trusted helper parses durations
to integer nanoseconds, validates the ranges, builds a canonical JCS JSON
and computes ``host_policy_sha256``. Schema v1 REQUIRES
``resume_retry_jitter == 0.0`` (single-node: nothing to decorrelate); a
non-zero jitter is a validation error, not a warning.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from packages.domain.canonical import canonical_sha256

POLICY_SCHEMA_VERSION = 1

_DUR_RE = re.compile(r"^\s*(\d+(?:\.\d+)?)\s*(ns|us|ms|s|min|h)\s*$")

_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "min": 60_000_000_000,
    "h": 3_600_000_000_000,
}

REQUIRED_DURATION_KEYS = (
    "resume_retry_initial",
    "resume_retry_max",
    "resume_retry_escalate_after",
    "retry_timer_period",
    "retry_timer_accuracy",
    "maintenance_flock_deadline",
)


class PolicyError(ValueError):
    """The policy file is absent, malformed or fails the v1 ranges."""


def parse_duration_ns(value: Any, key: str) -> int:
    """Parse a duration string (``30s``, ``30min``, ``2s``) to ns."""
    if not isinstance(value, str):
        raise PolicyError(f"{key}: expected a duration string, got {type(value).__name__}")
    m = _DUR_RE.match(value)
    if not m:
        raise PolicyError(f"{key}: invalid duration {value!r}")
    number, unit = m.groups()
    return int(float(number) * _UNIT_NS[unit])


@dataclass(frozen=True)
class HostPolicy:
    """A validated host-recovery policy (schema v1)."""

    schema_version: int
    resume_retry_initial: int
    resume_retry_multiplier: float
    resume_retry_max: int
    resume_retry_jitter: float
    resume_retry_escalate_after: int
    retry_timer_period: int
    retry_timer_accuracy: int
    maintenance_flock_deadline: int
    host_policy_sha256: str
    source: str = ""
    raw: dict[str, Any] = field(default_factory=dict, repr=False)

    def canonical(self) -> dict[str, Any]:
        """The canonical JCS-able dict the hash is computed over."""
        return {
            "schema_version": self.schema_version,
            "resume_retry_initial": self.resume_retry_initial,
            "resume_retry_multiplier": self.resume_retry_multiplier,
            "resume_retry_max": self.resume_retry_max,
            "resume_retry_jitter": self.resume_retry_jitter,
            "resume_retry_escalate_after": self.resume_retry_escalate_after,
            "retry_timer_period": self.retry_timer_period,
            "retry_timer_accuracy": self.retry_timer_accuracy,
            "maintenance_flock_deadline": self.maintenance_flock_deadline,
        }


def _validate_ranges(d: dict[str, Any]) -> None:
    initial = d["resume_retry_initial"]
    maxv = d["resume_retry_max"]
    multiplier = d["resume_retry_multiplier"]
    jitter = d["resume_retry_jitter"]
    period = d["retry_timer_period"]
    accuracy = d["retry_timer_accuracy"]
    if initial <= 0:
        raise PolicyError("resume_retry_initial must be > 0")
    if multiplier < 1:
        raise PolicyError("resume_retry_multiplier must be >= 1")
    if maxv < initial:
        raise PolicyError("resume_retry_max must be >= resume_retry_initial")
    if jitter != 0.0:
        # schema v1: single-node, nothing to decorrelate (ADR §21.6)
        raise PolicyError(f"resume_retry_jitter must be 0.0 in schema v1, got {jitter}")
    if period > initial:
        raise PolicyError("retry_timer_period must be <= resume_retry_initial")
    if not (0 < accuracy < period):
        raise PolicyError("require 0 < retry_timer_accuracy < retry_timer_period")


def load_policy(data: bytes, *, source: str = "") -> HostPolicy:
    """Parse + validate a policy file's bytes. Raises PolicyError."""
    try:
        raw = tomllib.loads(data.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise PolicyError(f"invalid TOML: {exc}") from exc

    if raw.get("schema_version") != POLICY_SCHEMA_VERSION:
        raise PolicyError(f"schema_version must be {POLICY_SCHEMA_VERSION}, got {raw.get('schema_version')}")

    parsed: dict[str, Any] = {"schema_version": POLICY_SCHEMA_VERSION}
    for key in REQUIRED_DURATION_KEYS:
        if key not in raw:
            raise PolicyError(f"missing required key {key!r}")
        parsed[key] = parse_duration_ns(raw[key], key)

    if "resume_retry_multiplier" not in raw:
        raise PolicyError("missing required key 'resume_retry_multiplier'")
    multiplier = raw["resume_retry_multiplier"]
    if not isinstance(multiplier, (int, float)) or isinstance(multiplier, bool):
        raise PolicyError("resume_retry_multiplier must be a number")
    parsed["resume_retry_multiplier"] = float(multiplier)

    if "resume_retry_jitter" not in raw:
        raise PolicyError("missing required key 'resume_retry_jitter'")
    jitter = raw["resume_retry_jitter"]
    if not isinstance(jitter, (int, float)) or isinstance(jitter, bool):
        raise PolicyError("resume_retry_jitter must be a number")
    parsed["resume_retry_jitter"] = float(jitter)

    _validate_ranges(parsed)

    policy = HostPolicy(
        schema_version=POLICY_SCHEMA_VERSION,
        resume_retry_initial=parsed["resume_retry_initial"],
        resume_retry_multiplier=parsed["resume_retry_multiplier"],
        resume_retry_max=parsed["resume_retry_max"],
        resume_retry_jitter=parsed["resume_retry_jitter"],
        resume_retry_escalate_after=parsed["resume_retry_escalate_after"],
        retry_timer_period=parsed["retry_timer_period"],
        retry_timer_accuracy=parsed["retry_timer_accuracy"],
        maintenance_flock_deadline=parsed["maintenance_flock_deadline"],
        host_policy_sha256="",  # filled below (self-referential)
        source=source,
        raw=dict(raw),
    )
    # the hash is over the canonical dict (NOT including the hash itself)
    digest = canonical_sha256(policy.canonical())
    return HostPolicy(
        schema_version=policy.schema_version,
        resume_retry_initial=policy.resume_retry_initial,
        resume_retry_multiplier=policy.resume_retry_multiplier,
        resume_retry_max=policy.resume_retry_max,
        resume_retry_jitter=policy.resume_retry_jitter,
        resume_retry_escalate_after=policy.resume_retry_escalate_after,
        retry_timer_period=policy.retry_timer_period,
        retry_timer_accuracy=policy.retry_timer_accuracy,
        maintenance_flock_deadline=policy.maintenance_flock_deadline,
        host_policy_sha256=digest,
        source=source,
        raw=dict(raw),
    )


def read_policy_file(path: Path) -> HostPolicy:
    """Read a policy file via a checked descriptor (owner/mode sanity).

    A missing file raises PolicyError; a present-but-invalid file is
    surfaced as PolicyError (never silently fallen back)."""
    if not path.exists():
        raise PolicyError(f"policy file not found: {path}")
    if path.is_symlink():
        raise PolicyError(f"policy file must not be a symlink: {path}")
    data = path.read_bytes()
    return load_policy(data, source=str(path))
