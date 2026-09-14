"""Host recovery policy change — the audited install/resolve protocol
(T3.15, §8.7.1.1).

The policy is changed ONLY by the root command
``noezemactl install-host-recovery-policy --file ... --reason ...``. The
single authoritative index of an in-progress change is
``/var/lib/noezema/host-policy-change-head.json`` (fsync-safe,
``root:noezema-observer 0640``). While the head exists, a normal install
and the creation of any host transition are forbidden (typed
``host_policy_change_in_progress``).

Normal protocol:

```text
validate candidate
  -> publish <change_id>/1.json: prepared(old_hash, proposed_hash, actor, reason)
  -> publish host-policy-change-head.json
  -> atomic replace override + fsync(directory)
  -> publish terminal:
       committed(effective_hash=proposed_hash)   # file == proposed
       aborted(effective_hash=old_hash)          # file stayed old
  -> unlink head + fsync(parent directory)
```

The event stream ``/var/lib/noezema/host-policy-events/<change_id>/
<event_seq>.json`` uses the same immutable/fsync-safe protocol as host
transitions. The head is removed only after the terminal event is fsynced
with a mandatory ``effective_hash``.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from hostctl.journal import fsync_write_atomic
from hostctl.policy import HostPolicy, PolicyError, read_policy_file
from packages.domain.canonical import canonical_sha256

POLICY_HEAD_SCHEMA_VERSION = 1
POLICY_HEAD_MODE = 0o640

#: the packaged baseline is the only allowed emergency envelope
BASELINE_PATH = Path("/usr/lib/noezema/host-recovery.defaults.toml")
OVERRIDE_PATH = Path("/etc/noezema/host-recovery.toml")


class PolicyChangeError(RuntimeError):
    pass


def policy_events_dir(base: Path) -> Path:
    return base / "host-policy-events"


def policy_head_path(base: Path) -> Path:
    return base / "host-policy-change-head.json"


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class PolicyChange:
    base: Path
    change_id: str

    def __post_init__(self) -> None:
        self._events_dir = policy_events_dir(self.base) / self.change_id
        self._events_dir.mkdir(parents=True, exist_ok=True)
        self._seq = len(list(self._events_dir.glob("*.json")))

    def _next_seq(self) -> int:
        self._seq += 1
        return self._seq

    def publish(self, kind: str, **fields: Any) -> dict[str, Any]:
        seq = self._next_seq()
        event = {
            "change_id": self.change_id,
            "event_seq": seq,
            "kind": kind,
            "host_timestamp": _now(),
            **fields,
        }
        import json

        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        fsync_write_atomic(self._events_dir / f"{seq}.json", payload, mode=POLICY_HEAD_MODE)
        return event

    def write_head(self, old_hash: str, proposed_hash: str, prepared_event_sha256: str) -> None:
        head = {
            "schema_version": POLICY_HEAD_SCHEMA_VERSION,
            "change_id": self.change_id,
            "prepared_event_sha256": prepared_event_sha256,
            "old_hash": old_hash,
            "proposed_hash": proposed_hash,
            "created_at": _now(),
        }
        fsync_write_atomic(policy_head_path(self.base), _json_bytes(head), mode=POLICY_HEAD_MODE)

    def remove_head(self) -> None:
        path = policy_head_path(self.base)
        if path.exists():
            path.unlink()
            fsync_dir_local(path.parent)


def fsync_dir_local(path: Path) -> None:
    import os

    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _json_bytes(value: dict[str, Any]) -> bytes:
    import json

    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def read_policy_head(base: Path) -> dict[str, Any] | None:
    path = policy_head_path(base)
    if not path.exists():
        return None
    import json

    try:
        data: dict[str, Any] = json.loads(path.read_bytes().decode("utf-8"))
        return data
    except (OSError, ValueError):
        return None


def install_policy(
    *,
    base: Path,
    override: Path,
    candidate_file: Path,
    actor: str,
    reason: str,
) -> HostPolicy:
    """The normal install protocol. Idempotent-friendly: a crash between
    steps allows an audited retry of the same command."""
    if read_policy_head(base) is not None:
        raise PolicyChangeError("host_policy_change_in_progress")

    # validate the candidate BEFORE publishing anything
    proposed = read_policy_file(candidate_file)
    # the current (old) state: the override if present, else the baseline
    old_hash = _current_hash(override)
    proposed_hash = proposed.host_policy_sha256
    if old_hash == proposed_hash:
        # no change: still audited, but nothing to replace
        raise PolicyChangeError("candidate_policy_is_already_current")

    change = PolicyChange(base=base, change_id=uuid.uuid4().hex)
    prepared = change.publish(
        "prepared",
        old_hash=old_hash,
        proposed_hash=proposed_hash,
        actor=actor,
        reason=reason,
        source_kind="override",
    )
    change.write_head(old_hash, proposed_hash, canonical_sha256(prepared))

    # atomic replace of the override + fsync(directory)
    fsync_write_atomic(override, candidate_file.read_bytes(), mode=POLICY_HEAD_MODE)

    change.publish("committed", effective_hash=proposed_hash, actor=actor, reason=reason)
    change.remove_head()
    return proposed


def resolve_policy(
    *,
    base: Path,
    override: Path,
    actor: str,
    reason: str,
    accept_current: bool = False,
    replacement_file: Path | None = None,
) -> HostPolicy:
    """Exit for a specific active change_id: ``--accept-current`` (only for a
    valid current file) or ``--file`` (works even if the current file is
    invalid)."""
    head = read_policy_head(base)
    if head is None:
        raise PolicyChangeError("no_active_host_policy_change")
    change = PolicyChange(base=base, change_id=head["change_id"])

    if accept_current:
        current = read_policy_file(override)  # raises PolicyError if invalid
        change.publish(
            "resolved",
            resolution_action="accept_current",
            effective_hash=current.host_policy_sha256,
            actor=actor,
            reason=reason,
        )
        change.remove_head()
        return current

    if replacement_file is None:
        raise PolicyChangeError("resolve requires --accept-current or --file")
    replacement = read_policy_file(replacement_file)
    observed = _current_hash(override)
    change.publish(
        "resolution_prepared",
        base_observed_hash=observed,
        replacement_hash=replacement.host_policy_sha256,
    )
    fsync_write_atomic(override, replacement_file.read_bytes(), mode=POLICY_HEAD_MODE)
    change.publish(
        "resolved",
        resolution_action="install_replacement",
        effective_hash=replacement.host_policy_sha256,
        actor=actor,
        reason=reason,
    )
    change.remove_head()
    return replacement


def _current_hash(override: Path) -> str:
    """The canonical hash of the current policy (override if valid, else
    the packaged baseline as the emergency envelope). An empty string means
    no current source could be read (the caller treats it as absent)."""
    if override.exists():
        try:
            return read_policy_file(override).host_policy_sha256
        except PolicyError:
            pass
    if BASELINE_PATH.exists():
        return read_policy_file(BASELINE_PATH).host_policy_sha256
    return ""
