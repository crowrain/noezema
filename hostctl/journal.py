"""Host-transition journal — fsync-safe durable chronology (T3.11,
§8.7.1.1).

Files (all under a base dir, normally ``/var/lib/noezema``):

- ``host-transitions/<attempt_id>.json`` — the mutable CURRENT record;
- ``host-transition-events/<attempt_id>/<event_seq>.json`` — immutable
  events (the real host chronology);
- ``host-transition-head.json`` — the SINGLE authoritative index of the
  unresolved transition. Its identity hash is computed only over the
  immutable header and does NOT change when the mutable state is updated.

Every publication is ``tmp file -> fsync(file) -> atomic rename ->
fsync(directory)``. Creation order under one flock: initial event ->
current record -> active head -> marker. Recovery deduplicates
``(attempt_id, event_seq)`` and rebuilds the snapshot from the longest
contiguous event prefix.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from contextlib import suppress
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from packages.domain.canonical import canonical_sha256

HEAD_SCHEMA_VERSION = 1
RECORD_SCHEMA_VERSION = 1

RECORD_MODE = 0o640

#: the states a record can be in (§8.7.1.1)
STATE_CHECKING = "checking"
STATE_RETRY_WAIT = "retry_wait"
STATE_READY_TO_START = "ready_to_start"
STATE_RESOLVED = "resolved"
STATE_RESUME_BLOCKED = "resume_blocked"
STATE_RESUME_DEGRADED = "resume_degraded"

TERMINAL_STATES = frozenset({STATE_RESOLVED})


def fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_write_atomic(path: Path, data: bytes, *, mode: int = RECORD_MODE) -> None:
    """tmp file -> fsync(file) -> atomic rename -> fsync(directory)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with suppress(OSError):
            tmp.unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


@dataclass
class HostPolicyContract:
    """The policy materialized into a transition record (frozen for the
    duration of the recovery)."""

    host_policy_schema_version: int
    source_kind: str
    source_path: str
    source_hash: str
    canonical_hash: str
    resume_retry_initial: int
    resume_retry_multiplier: float
    resume_retry_max: int
    resume_retry_jitter: float
    resume_retry_escalate_after: int
    retry_timer_period: int
    retry_timer_accuracy: int
    maintenance_flock_deadline: int


@dataclass
class TransitionRecord:
    attempt_id: str
    operation: str
    candidate_snapshot_id: str | None
    base_snapshot_id: str | None
    observed_pointer_tuple: dict[str, Any]
    state: str
    attempts_total: int = 0
    current_attempt_seq: int = 0
    consecutive_unclassified_failures: int = 0
    backoff_step: int = 0
    error_class: str | None = None
    last_probe_started_at: str | None = None
    last_probe_classified_at: str | None = None
    next_attempt_at: str | None = None
    dispatch_id: str | None = None
    dispatch_deadline: str | None = None
    last_event_seq: int = 0
    replayed_through_seq: int = 0
    replayed_at: str | None = None
    created_at: str = field(default_factory=_now_iso)
    host_policy: HostPolicyContract | None = None

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL_STATES

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> TransitionRecord:
        policy = d.get("host_policy")
        if policy is not None:
            d = dict(d)
            d["host_policy"] = HostPolicyContract(**policy)
        return cls(**d)


def record_identity_header(record: TransitionRecord, initial_event_sha256: str) -> dict[str, Any]:
    """The IMMUTABLE header the head's identity hash is computed over."""
    return {
        "schema_version": RECORD_SCHEMA_VERSION,
        "attempt_id": record.attempt_id,
        "operation": record.operation,
        "created_at": record.created_at,
        "initial_event_sha256": initial_event_sha256,
    }


class ReconcileResult:
    def __init__(self) -> None:
        self.unresolved: list[TransitionRecord] = []
        self.head_attempt_id: str | None = None
        self.head_valid: bool = False
        self.problem: str | None = None

    @property
    def ok(self) -> bool:
        return self.problem is None


class JournalStore:
    """The on-disk host-transition journal rooted at ``base``."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.transitions_dir = base / "host-transitions"
        self.events_dir = base / "host-transition-events"
        self.head_path = base / "host-transition-head.json"
        self.transitions_dir.mkdir(parents=True, exist_ok=True)
        self.events_dir.mkdir(parents=True, exist_ok=True)

    # ── head ──────────────────────────────────────────────────────────────

    def head_exists(self) -> bool:
        return self.head_path.exists()

    def read_head(self) -> dict[str, Any] | None:
        if not self.head_path.exists():
            return None
        try:
            data: dict[str, Any] = json.loads(self.head_path.read_bytes().decode("utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return None

    def write_head(
        self,
        record: TransitionRecord,
        *,
        initial_event_sha256: str,
        creation_boot_id: str,
    ) -> dict[str, Any]:
        header = record_identity_header(record, initial_event_sha256)
        head = {
            "schema_version": HEAD_SCHEMA_VERSION,
            "attempt_id": record.attempt_id,
            # the immutable header is stored so the identity can be
            # re-verified after a crash (initial_event_sha256 is not a
            # field of the mutable record)
            "record_identity_header": header,
            "record_identity_sha256": canonical_sha256(header),
            "created_at": record.created_at,
            "creation_boot_id": creation_boot_id,
        }
        fsync_write_atomic(self.head_path, _json_bytes(head))
        return head

    def remove_head(self) -> None:
        if self.head_path.exists():
            self.head_path.unlink()
            fsync_dir(self.head_path.parent)

    # ── current record ────────────────────────────────────────────────────

    def record_path(self, attempt_id: str) -> Path:
        return self.transitions_dir / f"{attempt_id}.json"

    def write_record(self, record: TransitionRecord) -> None:
        fsync_write_atomic(self.record_path(record.attempt_id), _json_bytes(record.to_dict()))

    def read_record(self, attempt_id: str) -> TransitionRecord | None:
        path = self.record_path(attempt_id)
        if not path.exists():
            return None
        try:
            return TransitionRecord.from_dict(json.loads(path.read_bytes().decode("utf-8")))
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def list_records(self) -> list[TransitionRecord]:
        out: list[TransitionRecord] = []
        for path in sorted(self.transitions_dir.glob("*.json")):
            rec = self.read_record(path.stem)
            if rec is not None:
                out.append(rec)
        return out

    # ── events ────────────────────────────────────────────────────────────

    def event_path(self, attempt_id: str, event_seq: int) -> Path:
        return self.events_dir / attempt_id / f"{event_seq}.json"

    def write_event(self, attempt_id: str, event_seq: int, event: dict[str, Any]) -> str:
        payload = {
            "attempt_id": attempt_id,
            "event_seq": event_seq,
            "host_timestamp": _now_iso(),
            **event,
        }
        fsync_write_atomic(self.event_path(attempt_id, event_seq), _json_bytes(payload))
        return canonical_sha256(payload)

    def list_event_seqs(self, attempt_id: str) -> list[int]:
        d = self.events_dir / attempt_id
        if not d.exists():
            return []
        seqs: list[int] = []
        for path in d.glob("*.json"):
            try:
                seqs.append(int(path.stem))
            except ValueError:
                continue
        return sorted(seqs)

    # ── boot reconciliation (§8.7.1.1) ────────────────────────────────────

    def reconcile(self) -> ReconcileResult:
        """Boot-time / pre-transition reconciliation of the head.

        - zero unresolved records -> no head is left;
        - exactly one unresolved -> the head is (re)built from it;
        - more than one -> permanent inconsistency (runtime start forbidden);
        - a head on a missing/corrupt/different record -> problem;
        - a head on an already-resolved record -> full replay check + drop.
        """
        res = ReconcileResult()
        head = self.read_head()
        if head is not None:
            res.head_attempt_id = head.get("attempt_id")

        records = self.list_records()
        unresolved = [r for r in records if not r.is_terminal]
        res.unresolved = unresolved

        if head is None:
            if len(unresolved) == 0:
                return res
            if len(unresolved) == 1:
                # rebuild the head from the single orphan
                rec = unresolved[0]
                seqs = self.list_event_seqs(rec.attempt_id)
                initial_sha = canonical_sha256(self._read_event(rec.attempt_id, seqs[0])) if seqs else ""
                self.write_head(rec, initial_event_sha256=initial_sha, creation_boot_id="recovered")
                res.head_attempt_id = rec.attempt_id
                return res
            res.problem = "multiple_unresolved_transitions"
            return res

        # head present: verify it matches a single unresolved (or resolved)
        head_attempt = str(head["attempt_id"])
        res.head_attempt_id = head_attempt
        head_rec = self.read_record(head_attempt)
        if head_rec is None:
            res.problem = "head_points_to_missing_record"
            return res
        stored_header = head.get("record_identity_header")
        if not isinstance(stored_header, dict):
            res.problem = "head_identity_mismatch"
            return res
        # re-verify: the record's immutable fields must match the stored
        # header (initial_event_sha256 is trusted from the head)
        recomputed = record_identity_header(head_rec, str(stored_header.get("initial_event_sha256", "")))
        if recomputed != stored_header:
            res.problem = "head_identity_mismatch"
            return res
        if head.get("record_identity_sha256") != canonical_sha256(stored_header):
            res.problem = "head_identity_mismatch"
            return res
        if len(unresolved) > 1:
            res.problem = "multiple_unresolved_transitions"
            return res
        if head_rec.is_terminal:
            # head on a resolved record: after a full replay check, drop it
            if not self._replay_complete(head_rec):
                res.problem = "head_on_resolved_record_incomplete_replay"
                return res
            self.remove_head()
            res.head_attempt_id = None
            return res
        res.head_valid = True
        return res

    def _read_event(self, attempt_id: str, event_seq: int) -> dict[str, Any]:
        try:
            data: dict[str, Any] = json.loads(self.event_path(attempt_id, event_seq).read_bytes().decode("utf-8"))
            return data
        except (OSError, json.JSONDecodeError):
            return {}

    def _replay_complete(self, rec: TransitionRecord) -> bool:
        seqs = self.list_event_seqs(rec.attempt_id)
        if not seqs:
            return False
        # contiguous from 1
        expected = list(range(1, len(seqs) + 1))
        return seqs == expected and rec.replayed_through_seq >= rec.last_event_seq
