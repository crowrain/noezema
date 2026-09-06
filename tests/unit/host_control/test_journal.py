"""Crash recovery contracts for the fsync-safe host-transition journal."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import pytest

from apps.host_control import (
    HostJournalPaths,
    HostOperation,
    HostTransitionInProgressError,
    HostTransitionJournal,
    HostTransitionState,
    load_host_recovery_policy,
    read_active_transition,
)

_NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)
_BOOT_ID = UUID("11111111-2222-4333-8444-555555555555")
_ROOT = Path(__file__).resolve().parents[3]
_BASELINE = _ROOT / "infra" / "systemd" / "host-recovery.defaults.toml"


def _policy():
    return load_host_recovery_policy(_BASELINE, enforce_root_metadata=False)


def test_one_active_head_serializes_transitions_and_retains_resolved_history(
    tmp_path: Path,
) -> None:
    paths = HostJournalPaths(tmp_path)
    journal = HostTransitionJournal(paths)
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=_BOOT_ID,
        policy=_policy(),
        actor="resume",
        reason="cold boot",
        occurred_at=_NOW,
    )

    assert paths.head.exists()
    assert read_active_transition(paths.head) == record
    with pytest.raises(HostTransitionInProgressError):
        journal.create(
            operation=HostOperation.OFFLINE_RULES,
            boot_id=_BOOT_ID,
            policy=_policy(),
            actor="maintenance",
            reason="change rules",
            occurred_at=_NOW,
        )

    ready = journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.READY_TO_START,
        actor="resume",
        reason="database admission passed",
        occurred_at=_NOW + timedelta(seconds=1),
        snapshot_updates={
            "dispatch_id": UUID("aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee"),
            "dispatch_deadline": _NOW + timedelta(seconds=31),
        },
    )
    resolved = journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.RESOLVED,
        actor="resume",
        reason="runtime target started",
        occurred_at=_NOW + timedelta(seconds=2),
        snapshot_updates={"dispatch_id": None, "dispatch_deadline": None},
    )
    assert ready.last_event_seq == 2
    assert resolved.last_event_seq == 3
    partial = journal.mark_replayed(
        record.attempt_id,
        through_seq=2,
        replayed_at=_NOW + timedelta(seconds=3),
    )
    assert partial.replayed_through_seq == 2
    assert paths.head.exists()
    journal.mark_replayed(
        record.attempt_id,
        through_seq=3,
        replayed_at=_NOW + timedelta(seconds=4),
    )

    assert not paths.head.exists()
    assert (paths.records / f"{record.attempt_id}.json").exists()
    assert len(journal.events(record.attempt_id)) == 3


def test_reconcile_repairs_event_published_before_current_record(tmp_path: Path) -> None:
    paths = HostJournalPaths(tmp_path)
    crashed = False

    def failpoint(name: str) -> None:
        nonlocal crashed
        if name == "after_transition_event" and not crashed:
            crashed = True
            raise RuntimeError("simulated crash")

    journal = HostTransitionJournal(paths, failpoint=failpoint)
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=_BOOT_ID,
        policy=_policy(),
        actor="resume",
        reason="boot",
        occurred_at=_NOW,
    )
    retry_at = _NOW + timedelta(seconds=30)
    with pytest.raises(RuntimeError, match="simulated crash"):
        journal.transition(
            record.attempt_id,
            to_state=HostTransitionState.RETRY_WAIT,
            actor="resume",
            reason="database unavailable",
            error_class="database_unavailable",
            occurred_at=_NOW + timedelta(seconds=1),
            snapshot_updates={"next_attempt_at": retry_at},
        )

    repaired = HostTransitionJournal(paths).active()

    assert repaired is not None
    assert repaired.state is HostTransitionState.RETRY_WAIT
    assert repaired.snapshot.next_attempt_at == retry_at
    assert repaired.last_event_seq == 2


def test_missing_head_is_restored_from_one_unresolved_current_record(tmp_path: Path) -> None:
    paths = HostJournalPaths(tmp_path)
    journal = HostTransitionJournal(paths)
    record = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=_BOOT_ID,
        policy=_policy(),
        actor="resume",
        reason="boot",
        occurred_at=_NOW,
    )
    paths.head.unlink()

    restored = journal.active()

    assert restored is not None and restored.attempt_id == record.attempt_id
    assert paths.head.exists()
