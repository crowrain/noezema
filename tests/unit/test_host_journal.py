"""Unit: host-transition journal — fsync-safe, head, boot reconcile
(T3.11, §8.7.1.1)."""

from __future__ import annotations

from pathlib import Path

from hostctl.journal import (
    STATE_CHECKING,
    STATE_RESOLVED,
    JournalStore,
    TransitionRecord,
    fsync_write_atomic,
)


def _rec(attempt_id: str, state: str = STATE_CHECKING) -> TransitionRecord:
    return TransitionRecord(
        attempt_id=attempt_id,
        operation="offline_rules",
        candidate_snapshot_id=None,
        base_snapshot_id=None,
        observed_pointer_tuple={"active": "x"},
        state=state,
    )


def test_fsync_write_atomic_creates_file(tmp_path: Path):
    p = tmp_path / "sub" / "f.json"
    fsync_write_atomic(p, b"hello")
    assert p.read_bytes() == b"hello"
    # no leftover tmp files
    assert list(p.parent.glob(".f.json.*.tmp")) == []


def test_head_write_and_identity(tmp_path: Path):
    store = JournalStore(tmp_path)
    rec = _rec("attempt-1")
    head = store.write_head(rec, initial_event_sha256="e1", creation_boot_id="boot-1")
    assert head["attempt_id"] == "attempt-1"
    assert head["record_identity_sha256"]
    assert store.read_head()["attempt_id"] == "attempt-1"


def test_head_identity_stable_across_state_updates(tmp_path: Path):
    store = JournalStore(tmp_path)
    rec = _rec("attempt-1", state=STATE_CHECKING)
    head1 = store.write_head(rec, initial_event_sha256="e1", creation_boot_id="b")
    # mutate the mutable state, rewrite the head with the same immutable
    # header -> the identity hash must not change
    rec.state = "retry_wait"
    rec.attempts_total = 3
    head2 = store.write_head(rec, initial_event_sha256="e1", creation_boot_id="b")
    assert head1["record_identity_sha256"] == head2["record_identity_sha256"]


def test_reconcile_zero_unresolved_no_head(tmp_path: Path):
    store = JournalStore(tmp_path)
    res = store.reconcile()
    assert res.ok
    assert res.unresolved == []
    assert not store.head_exists()


def test_reconcile_single_unresolved_rebuilds_head(tmp_path: Path):
    store = JournalStore(tmp_path)
    rec = _rec("attempt-1")
    store.write_record(rec)
    store.write_event("attempt-1", 1, {"from_state": None, "to_state": "checking"})
    res = store.reconcile()
    assert res.ok
    assert res.head_attempt_id == "attempt-1"
    assert store.head_exists()


def test_reconcile_multiple_unresolved_is_problem(tmp_path: Path):
    store = JournalStore(tmp_path)
    store.write_record(_rec("a"))
    store.write_record(_rec("b"))
    res = store.reconcile()
    assert not res.ok
    assert res.problem == "multiple_unresolved_transitions"


def test_reconcile_head_on_missing_record(tmp_path: Path):
    store = JournalStore(tmp_path)
    rec = _rec("a")
    store.write_record(rec)
    store.write_head(rec, initial_event_sha256="e", creation_boot_id="b")
    store.record_path("a").unlink()
    res = store.reconcile()
    assert not res.ok
    assert res.problem == "head_points_to_missing_record"


def test_reconcile_head_on_resolved_record_drops_head(tmp_path: Path):
    store = JournalStore(tmp_path)
    rec = _rec("a", state=STATE_CHECKING)
    store.write_record(rec)
    store.write_event("a", 1, {"to_state": "checking"})
    store.write_event("a", 2, {"to_state": "resolved"})
    store.write_head(rec, initial_event_sha256="e", creation_boot_id="b")
    # now mark the record resolved with a complete replay
    rec.state = STATE_RESOLVED
    rec.last_event_seq = 2
    rec.replayed_through_seq = 2
    rec.replayed_at = "now"
    store.write_record(rec)
    res = store.reconcile()
    assert res.ok
    assert not store.head_exists()


def test_events_are_contiguous_and_deduped(tmp_path: Path):
    store = JournalStore(tmp_path)
    store.write_event("a", 1, {"to_state": "checking"})
    store.write_event("a", 2, {"to_state": "retry_wait"})
    # re-writing the same (attempt_id, event_seq) is idempotent
    store.write_event("a", 2, {"to_state": "retry_wait"})
    assert store.list_event_seqs("a") == [1, 2]
