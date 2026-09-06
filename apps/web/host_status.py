"""Read-only, system-bus-free host health projection for degraded web mode."""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from apps.host_control.journal import (
    HostJournalInconsistentError,
    read_active_transition,
    read_transition_events,
)
from apps.web.models import (
    DegradedReason,
    HostStatusProjection,
    HostTransitionEventProjection,
    HostTransitionProjection,
    HostUnitProjection,
)
from packages.domain import canonical_json_sha256
from packages.domain._base import ContractModel, Sha256Hex

_DEFAULT_UNIT_STATE = Path("/run/noezema/unit-state.json")
_DEFAULT_BOOT_ID = Path("/proc/sys/kernel/random/boot_id")
_DEFAULT_MAINTENANCE_MARKER = Path("/run/noezema-offline-rules/active")
_DEFAULT_TRANSITION_HEAD = Path("/var/lib/noezema/host-transition-head.json")
_DEFAULT_POLICY_CHANGE_HEAD = Path("/var/lib/noezema/host-policy-change-head.json")
_MAX_SNAPSHOT_BYTES = 64 * 1024
_TARGET_UNIT = "noezema-runtime.target"


class HostStatusReader(Protocol):
    def status(self, *, observed_at: datetime) -> HostStatusProjection: ...


class _UnitStateRecord(ContractModel):
    name: str = Field(min_length=1, max_length=256, pattern=r"^[A-Za-z0-9_.@:-]+$")
    active_state: str = Field(min_length=1, max_length=64, pattern=r"^[a-z-]+$")
    sub_state: str = Field(min_length=1, max_length=64, pattern=r"^[a-z-]+$")
    result: str = Field(min_length=1, max_length=64, pattern=r"^[a-z-]+$")


class _UnitStateSnapshot(ContractModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    schema_version: Literal["unit-state/v1"]
    boot_id: UUID
    observed_at: AwareDatetime
    publisher_result: Literal["ok", "error"]
    target: _UnitStateRecord
    members: tuple[_UnitStateRecord, ...]
    inventory_sha256: Sha256Hex

    @model_validator(mode="after")
    def verify_inventory(self) -> _UnitStateSnapshot:
        names = tuple(member.name for member in self.members)
        if not names or names != tuple(sorted(set(names))):
            raise ValueError("runtime member inventory must be non-empty, sorted and unique")
        if any(not name.endswith(".service") for name in names):
            raise ValueError("runtime inventory may contain only services")
        if self.target.name != _TARGET_UNIT:
            raise ValueError("unit snapshot belongs to another target")
        expected = canonical_json_sha256({"members": list(names)})
        if self.inventory_sha256 != expected:
            raise ValueError("runtime inventory digest does not match")
        return self


class FilesystemHostStatusReader:
    """Validate root-published snapshots and active host-operation markers."""

    def __init__(
        self,
        *,
        unit_state_path: Path = _DEFAULT_UNIT_STATE,
        boot_id_path: Path = _DEFAULT_BOOT_ID,
        maintenance_marker_path: Path = _DEFAULT_MAINTENANCE_MARKER,
        transition_head_path: Path = _DEFAULT_TRANSITION_HEAD,
        policy_change_head_path: Path = _DEFAULT_POLICY_CHANGE_HEAD,
        snapshot_ttl_seconds: int = 15,
    ) -> None:
        if not 12 <= snapshot_ttl_seconds <= 300:
            raise ValueError("unit-state TTL must be between 12 and 300 seconds")
        self._unit_state_path = unit_state_path
        self._boot_id_path = boot_id_path
        self._maintenance_marker_path = maintenance_marker_path
        self._transition_head_path = transition_head_path
        self._policy_change_head_path = policy_change_head_path
        self._snapshot_ttl_seconds = snapshot_ttl_seconds

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> FilesystemHostStatusReader:
        values = os.environ if environment is None else environment
        try:
            ttl = int(values.get("NOEZEMA_UNIT_STATE_TTL_SECONDS", "15"))
        except ValueError as exc:
            raise ValueError("NOEZEMA_UNIT_STATE_TTL_SECONDS must be an integer") from exc
        return cls(
            unit_state_path=Path(values.get("NOEZEMA_UNIT_STATE_PATH", _DEFAULT_UNIT_STATE)),
            boot_id_path=Path(values.get("NOEZEMA_BOOT_ID_PATH", _DEFAULT_BOOT_ID)),
            maintenance_marker_path=Path(
                values.get("NOEZEMA_MAINTENANCE_MARKER_PATH", _DEFAULT_MAINTENANCE_MARKER)
            ),
            transition_head_path=Path(
                values.get("NOEZEMA_HOST_TRANSITION_HEAD_PATH", _DEFAULT_TRANSITION_HEAD)
            ),
            policy_change_head_path=Path(
                values.get("NOEZEMA_HOST_POLICY_CHANGE_HEAD_PATH", _DEFAULT_POLICY_CHANGE_HEAD)
            ),
            snapshot_ttl_seconds=ttl,
        )

    def status(self, *, observed_at: datetime) -> HostStatusProjection:
        now = _aware(observed_at)
        maintenance = _present(self._maintenance_marker_path)
        transition = _present(self._transition_head_path)
        transition_projection = None
        transition_events: tuple[HostTransitionEventProjection, ...] = ()
        transition_invalid = False
        if transition:
            try:
                record = read_active_transition(self._transition_head_path)
            except (HostJournalInconsistentError, OSError, ValueError):
                transition_invalid = True
            else:
                if record is None:
                    transition_invalid = True
                else:
                    transition_projection = HostTransitionProjection(
                        attempt_id=record.attempt_id,
                        operation=record.operation.value,
                        state=record.state.value,
                        attempts_total=record.snapshot.attempts_total,
                        current_attempt_seq=record.snapshot.current_attempt_seq,
                        error_class=record.snapshot.error_class,
                        next_attempt_at=record.snapshot.next_attempt_at,
                        last_event_seq=record.last_event_seq,
                    )
                    transition_events = tuple(
                        HostTransitionEventProjection(
                            event_seq=event.event_seq,
                            from_state=(
                                event.from_state.value
                                if event.from_state is not None
                                else None
                            ),
                            to_state=event.to_state.value,
                            actor=event.actor,
                            reason=event.reason,
                            error_class=event.error_class,
                            occurred_at=event.occurred_at,
                        )
                        for event in read_transition_events(
                            self._transition_head_path,
                            record.attempt_id,
                        )[-20:]
                    )
        policy_change = _present(self._policy_change_head_path)
        reasons: list[DegradedReason] = []

        snapshot_state: Literal["current", "missing", "invalid", "stale"] = "missing"
        snapshot_observed_at = None
        snapshot_age = None
        boot_id = None
        target = None
        members: tuple[HostUnitProjection, ...] = ()
        try:
            raw = _read_bounded_regular_file(self._unit_state_path)
        except FileNotFoundError:
            reasons.append(DegradedReason.UNIT_STATE_MISSING)
        except (OSError, ValueError):
            snapshot_state = "invalid"
            reasons.append(DegradedReason.UNIT_STATE_INVALID)
        else:
            try:
                snapshot = _UnitStateSnapshot.model_validate_json(raw)
                current_boot_id = UUID(
                    _read_bounded_regular_file(self._boot_id_path, maximum_bytes=128)
                    .decode("ascii")
                    .strip()
                )
            except (OSError, UnicodeError, ValueError):
                snapshot_state = "invalid"
                reasons.append(DegradedReason.UNIT_STATE_INVALID)
                snapshot = None
            if snapshot is None:
                current_boot_id = None
            else:
                snapshot_observed_at = snapshot.observed_at.astimezone(UTC)
                snapshot_age = max(0.0, (now - snapshot_observed_at).total_seconds())
                boot_id = snapshot.boot_id
                target = _project_unit(snapshot.target)
                members = tuple(_project_unit(member) for member in snapshot.members)
                if snapshot.boot_id != current_boot_id:
                    snapshot_state = "invalid"
                    snapshot_observed_at = None
                    snapshot_age = None
                    boot_id = None
                    target = None
                    members = ()
                    reasons.append(DegradedReason.BOOT_ID_MISMATCH)
                elif snapshot.observed_at > now or snapshot_age > self._snapshot_ttl_seconds:
                    snapshot_state = "stale"
                    reasons.append(DegradedReason.UNIT_STATE_STALE)
                else:
                    snapshot_state = "current"
                    if snapshot.publisher_result != "ok":
                        reasons.append(DegradedReason.UNIT_STATE_PUBLISHER_FAILED)
                    if not _healthy_target(snapshot.target):
                        reasons.append(DegradedReason.RUNTIME_INACTIVE)
                    if any(not _healthy_member(member) for member in snapshot.members):
                        reasons.append(DegradedReason.RUNTIME_MEMBER_UNHEALTHY)

        if maintenance:
            reasons.append(DegradedReason.MAINTENANCE_ACTIVE)
        if transition:
            reasons.append(
                DegradedReason.HOST_TRANSITION_INVALID
                if transition_invalid
                else DegradedReason.HOST_TRANSITION_IN_PROGRESS
            )
        if policy_change:
            reasons.append(DegradedReason.HOST_POLICY_CHANGE_IN_PROGRESS)
        return HostStatusProjection(
            snapshot_state=snapshot_state,
            snapshot_observed_at=snapshot_observed_at,
            snapshot_age_seconds=snapshot_age,
            boot_id=boot_id,
            target=target,
            members=members,
            maintenance_active=maintenance,
            host_transition_active=transition,
            host_transition=transition_projection,
            host_transition_events=transition_events,
            host_policy_change_active=policy_change,
            reasons=tuple(dict.fromkeys(reasons)),
        )


class AssumedHealthyHostStatusReader:
    """Explicit in-process adapter for tests that do not emulate systemd."""

    def status(self, *, observed_at: datetime) -> HostStatusProjection:
        now = _aware(observed_at)
        return HostStatusProjection(
            snapshot_state="current",
            snapshot_observed_at=now,
            snapshot_age_seconds=0,
            boot_id=UUID(int=0),
            target=HostUnitProjection(
                name=_TARGET_UNIT,
                active_state="active",
                sub_state="active",
                result="success",
            ),
            members=(
                HostUnitProjection(
                    name="noezema-orchestrator.service",
                    active_state="active",
                    sub_state="running",
                    result="success",
                ),
            ),
            maintenance_active=False,
            host_transition_active=False,
            host_transition=None,
            host_transition_events=(),
            host_policy_change_active=False,
            reasons=(),
        )


def _project_unit(record: _UnitStateRecord) -> HostUnitProjection:
    return HostUnitProjection(
        name=record.name,
        active_state=record.active_state,
        sub_state=record.sub_state,
        result=record.result,
    )


def _healthy_target(record: _UnitStateRecord) -> bool:
    return (
        record.active_state == "active"
        and record.sub_state == "active"
        and record.result == "success"
    )


def _healthy_member(record: _UnitStateRecord) -> bool:
    return (
        record.active_state == "active"
        and record.sub_state == "running"
        and record.result == "success"
    )


def _present(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _read_bounded_regular_file(path: Path, *, maximum_bytes: int = _MAX_SNAPSHOT_BYTES) -> bytes:
    metadata = path.lstat()
    if path.is_symlink() or not path.is_file() or not 1 <= metadata.st_size <= maximum_bytes:
        raise ValueError("host status input must be a bounded regular file")
    return path.read_bytes()


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("host status clock must be timezone-aware")
    return value.astimezone(UTC)
