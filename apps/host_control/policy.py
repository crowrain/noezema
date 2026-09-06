"""Root-owned, versioned host recovery policy independent from PostgreSQL."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import ConfigDict, Field, ValidationError, model_validator

from apps.host_control.atomic import read_bounded_regular
from packages.domain import canonical_json_sha256
from packages.domain._base import ContractModel, Sha256Hex

_DURATION = re.compile(r"^(?P<number>[1-9][0-9]*)(?P<unit>ns|us|ms|s|min|h)$")
_UNIT_NS = {
    "ns": 1,
    "us": 1_000,
    "ms": 1_000_000,
    "s": 1_000_000_000,
    "min": 60_000_000_000,
    "h": 3_600_000_000_000,
}
_POLICY_KEYS = {
    "schema_version",
    "resume_retry_initial",
    "resume_retry_multiplier",
    "resume_retry_max",
    "resume_retry_jitter",
    "resume_retry_escalate_after",
    "retry_timer_period",
    "retry_timer_accuracy",
    "maintenance_flock_deadline",
}


class HostPolicyInvalidError(ValueError):
    """The selected host policy file is absent, unsafe or semantically invalid."""


class HostRecoveryPolicy(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal[1]
    resume_retry_initial_ns: int = Field(gt=0)
    resume_retry_multiplier: float = Field(ge=1.0, le=100.0)
    resume_retry_max_ns: int = Field(gt=0)
    resume_retry_jitter: Literal[0.0]
    resume_retry_escalate_after_ns: int = Field(gt=0)
    retry_timer_period_ns: int = Field(gt=0)
    retry_timer_accuracy_ns: int = Field(gt=0)
    maintenance_flock_deadline_ns: int = Field(gt=0)

    @model_validator(mode="after")
    def validate_order_and_pinned_timer(self) -> HostRecoveryPolicy:
        if self.resume_retry_max_ns < self.resume_retry_initial_ns:
            raise ValueError("resume retry maximum must cover the initial delay")
        if self.retry_timer_period_ns > self.resume_retry_initial_ns:
            raise ValueError("retry timer period must not exceed the initial delay")
        if self.retry_timer_accuracy_ns >= self.retry_timer_period_ns:
            raise ValueError("retry timer accuracy must be smaller than its period")
        if self.retry_timer_period_ns != 30_000_000_000:
            raise ValueError("schema v1 pins retry_timer_period to 30s")
        if self.retry_timer_accuracy_ns != 1_000_000_000:
            raise ValueError("schema v1 pins retry_timer_accuracy to 1s")
        return self

    def retry_delay_ns(self, backoff_step: int) -> int:
        if backoff_step < 0:
            raise ValueError("backoff step must be nonnegative")
        delay = float(self.resume_retry_initial_ns)
        for _ in range(backoff_step):
            delay = min(float(self.resume_retry_max_ns), delay * self.resume_retry_multiplier)
            if delay >= self.resume_retry_max_ns:
                break
        return int(delay)

    def canonical_material(self) -> dict[str, int | float]:
        return self.model_dump(mode="json")


@dataclass(frozen=True, slots=True)
class MaterializedHostPolicy:
    policy: HostRecoveryPolicy
    source_kind: Literal["packaged", "override"]
    source_path: Path
    source_file_sha256: Sha256Hex
    canonical_sha256: Sha256Hex


def load_host_recovery_policy(
    baseline_path: Path,
    *,
    override_path: Path | None = None,
    enforce_root_metadata: bool | None = None,
) -> MaterializedHostPolicy:
    """Select and validate one policy; a present invalid override never falls back."""

    enforce = os.name != "nt" if enforce_root_metadata is None else enforce_root_metadata
    if override_path is not None and _exists_fail_closed(override_path):
        selected = override_path
        source_kind: Literal["packaged", "override"] = "override"
    else:
        selected = baseline_path
        source_kind = "packaged"
    try:
        raw = read_bounded_regular(selected, maximum_bytes=32 * 1024)
        if enforce:
            _validate_root_metadata(selected)
        decoded = tomllib.loads(raw.decode("utf-8"))
        policy = _parse_policy(decoded)
    except (OSError, UnicodeError, ValueError, tomllib.TOMLDecodeError, ValidationError) as exc:
        raise HostPolicyInvalidError(f"invalid {source_kind} host policy: {exc}") from exc
    return MaterializedHostPolicy(
        policy=policy,
        source_kind=source_kind,
        source_path=selected.resolve(strict=True),
        source_file_sha256=hashlib.sha256(raw).hexdigest(),
        canonical_sha256=canonical_json_sha256(policy.canonical_material()),
    )


def _parse_policy(values: dict[str, object]) -> HostRecoveryPolicy:
    if set(values) != _POLICY_KEYS:
        missing = sorted(_POLICY_KEYS - set(values))
        extra = sorted(set(values) - _POLICY_KEYS)
        raise ValueError(f"host policy keys mismatch; missing={missing}, extra={extra}")
    return HostRecoveryPolicy(
        schema_version=values["schema_version"],
        resume_retry_initial_ns=_duration(values["resume_retry_initial"]),
        resume_retry_multiplier=float(values["resume_retry_multiplier"]),
        resume_retry_max_ns=_duration(values["resume_retry_max"]),
        resume_retry_jitter=float(values["resume_retry_jitter"]),
        resume_retry_escalate_after_ns=_duration(values["resume_retry_escalate_after"]),
        retry_timer_period_ns=_duration(values["retry_timer_period"]),
        retry_timer_accuracy_ns=_duration(values["retry_timer_accuracy"]),
        maintenance_flock_deadline_ns=_duration(values["maintenance_flock_deadline"]),
    )


def _duration(value: object) -> int:
    if not isinstance(value, str):
        raise ValueError("durations must be strings")
    match = _DURATION.fullmatch(value)
    if match is None:
        raise ValueError(f"invalid duration: {value!r}")
    return int(match.group("number")) * _UNIT_NS[match.group("unit")]


def _validate_root_metadata(path: Path) -> None:
    metadata = path.stat(follow_symlinks=False)
    if metadata.st_uid != 0 or metadata.st_gid != 0:
        raise ValueError("host policy must be owned by root:root")
    if stat.S_IMODE(metadata.st_mode) != 0o644:
        raise ValueError("host policy mode must be 0644")


def _exists_fail_closed(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True
