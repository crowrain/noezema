"""Crash-safe host recovery policy installation tests."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from apps.host_control import HostJournalPaths
from apps.host_control.policy import load_host_recovery_policy
from apps.host_control.policy_change import (
    HostPolicyInstaller,
    PolicyChangeJournal,
    PolicyChangeState,
    replay_terminal_policy_events,
)
from packages.domain import EventType
from packages.persistence.models import AuditEventRecord, HostEventReplayRecord

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
ROOT = Path(__file__).resolve().parents[3]
PACKAGED = ROOT / "infra" / "systemd" / "host-recovery.defaults.toml"


def _files(root: Path) -> tuple[Path, Path, Path]:
    baseline = root / "baseline.toml"
    candidate = root / "candidate.toml"
    override = root / "override.toml"
    content = PACKAGED.read_text(encoding="utf-8")
    baseline.write_text(content, encoding="utf-8")
    candidate.write_text(
        content.replace('resume_retry_max = "30min"', 'resume_retry_max = "20min"'),
        encoding="utf-8",
    )
    return baseline, candidate, override


def test_install_publishes_terminal_event_and_removes_active_head(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    baseline, candidate, override = _files(tmp_path)
    paths = HostJournalPaths(tmp_path / "state")
    journal = PolicyChangeJournal(paths)
    installer = HostPolicyInstaller(
        journal,
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
    )

    result = installer.install(
        candidate,
        actor="operator",
        reason="reduce maximum retry delay",
        occurred_at=NOW,
    )

    assert result.changed
    assert result.change_id is not None
    assert override.read_bytes() == candidate.read_bytes()
    assert not paths.policy_change_head.exists()
    events = sorted((paths.policy_events / str(result.change_id)).glob("*.json"))
    assert len(events) == 2
    assert PolicyChangeState.COMMITTED.value in events[-1].read_text(encoding="utf-8")
    assert replay_terminal_policy_events(
        session_factory,
        journal,
        replayed_at=NOW,
    ) == 2
    assert replay_terminal_policy_events(
        session_factory,
        journal,
        replayed_at=NOW,
    ) == 0
    with session_factory() as db:
        assert db.scalar(select(func.count()).select_from(HostEventReplayRecord)) == 2
        assert (
            db.scalar(
                select(func.count())
                .select_from(AuditEventRecord)
                .where(AuditEventRecord.type == EventType.HOST_POLICY_CHANGED.value)
            )
            == 2
        )


def test_retry_after_crash_post_replace_reconciles_without_second_change(tmp_path: Path) -> None:
    baseline, candidate, override = _files(tmp_path)
    paths = HostJournalPaths(tmp_path / "state")
    journal = PolicyChangeJournal(paths)

    def crash(name: str) -> None:
        if name == "after_policy_override_replace":
            raise RuntimeError("simulated crash")

    crashing = HostPolicyInstaller(
        journal,
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
        failpoint=crash,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.install(candidate, actor="operator", reason="change", occurred_at=NOW)
    active = journal.active()
    assert active is not None and active.state is PolicyChangeState.PREPARED

    result = HostPolicyInstaller(
        journal,
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
    ).install(candidate, actor="operator", reason="retry", occurred_at=NOW)

    assert not result.changed
    assert journal.active() is None
    streams = tuple(paths.policy_events.iterdir())
    assert len(streams) == 1
    assert len(tuple(streams[0].glob("*.json"))) == 2


def test_orphan_prepared_event_restores_the_single_policy_head(tmp_path: Path) -> None:
    baseline, candidate, override = _files(tmp_path)
    paths = HostJournalPaths(tmp_path / "state")

    def crash(name: str) -> None:
        if name == "after_policy_prepared_event":
            raise RuntimeError("simulated crash")

    crashing = HostPolicyInstaller(
        PolicyChangeJournal(paths, failpoint=crash),
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
    )
    with pytest.raises(RuntimeError, match="simulated crash"):
        crashing.install(candidate, actor="operator", reason="change", occurred_at=NOW)
    assert not paths.policy_change_head.exists()

    restored = PolicyChangeJournal(paths).active()

    assert restored is not None and restored.state is PolicyChangeState.PREPARED
    assert paths.policy_change_head.exists()


def test_invalid_third_file_is_resolved_with_replacement_in_the_same_stream(
    tmp_path: Path,
) -> None:
    baseline, candidate, override = _files(tmp_path)
    paths = HostJournalPaths(tmp_path / "state")
    journal = PolicyChangeJournal(paths)
    old = load_host_recovery_policy(baseline, enforce_root_metadata=False)
    proposed = load_host_recovery_policy(candidate, enforce_root_metadata=False)
    prepared = journal.begin(
        old=old,
        proposed=proposed,
        actor="operator",
        reason="change",
        occurred_at=NOW,
    )
    invalid = b"not valid toml"
    override.write_bytes(invalid)
    inconsistent = journal.reconcile(
        observed_canonical_sha256=None,
        observed_source_file_sha256=hashlib.sha256(invalid).hexdigest(),
        actor="recovery",
        reason="unexpected effective file",
        occurred_at=NOW,
    )
    assert inconsistent is not None
    assert inconsistent.state is PolicyChangeState.INCONSISTENT

    replacement = tmp_path / "replacement.toml"
    replacement.write_bytes(candidate.read_bytes())
    result = HostPolicyInstaller(
        journal,
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
    ).resolve_with_file(
        replacement,
        actor="operator",
        reason="accept validated replacement",
        occurred_at=NOW,
    )

    assert result.changed and result.change_id == prepared.change_id
    assert journal.active() is None
    events = tuple(sorted((paths.policy_events / str(prepared.change_id)).glob("*.json")))
    assert len(events) == 4
    assert PolicyChangeState.RESOLVED.value in events[-1].read_text(encoding="utf-8")


def test_valid_third_policy_requires_explicit_accept_current_resolution(
    tmp_path: Path,
) -> None:
    baseline, candidate, override = _files(tmp_path)
    paths = HostJournalPaths(tmp_path / "state")
    journal = PolicyChangeJournal(paths)
    old = load_host_recovery_policy(baseline, enforce_root_metadata=False)
    proposed = load_host_recovery_policy(candidate, enforce_root_metadata=False)
    prepared = journal.begin(
        old=old,
        proposed=proposed,
        actor="operator",
        reason="change",
        occurred_at=NOW,
    )
    third = PACKAGED.read_text(encoding="utf-8").replace(
        'resume_retry_max = "30min"',
        'resume_retry_max = "10min"',
    )
    override.write_text(third, encoding="utf-8")

    result = HostPolicyInstaller(
        journal,
        baseline_path=baseline,
        override_path=override,
        enforce_root_metadata=False,
    ).resolve_accept_current(
        actor="operator",
        reason="accept reviewed third policy",
        occurred_at=NOW,
    )

    assert result.change_id == prepared.change_id
    assert journal.active() is None
    events = tuple(sorted((paths.policy_events / str(prepared.change_id)).glob("*.json")))
    assert len(events) == 4
    assert PolicyChangeState.RESOLVED.value in events[-1].read_text(encoding="utf-8")
