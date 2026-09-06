"""Host ownership and quiescence ordering for offline rules maintenance."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from apps.host_control import (
    HostJournalPaths,
    HostOperation,
    HostTransitionJournal,
    HostTransitionState,
    OfflineRulesActivator,
    load_host_recovery_policy,
)
from apps.host_control.offline import run_offline_rules
from apps.host_control.recovery import HostRecoveryService
from packages.memory import mvp_claim_type_rules
from packages.persistence import (
    BOOTSTRAP_CONFIG_SNAPSHOT_ID,
    INVALID_QUESTION_NAMESPACE,
    bootstrap_payload,
)
from packages.persistence.models import RuntimeConfigHeadRecord, SystemConstantRecord

NOW = datetime(2026, 9, 6, 10, tzinfo=UTC)
BOOT_ID = UUID("11111111-2222-4333-8444-555555555555")
ROOT = Path(__file__).resolve().parents[3]
BASELINE = ROOT / "infra" / "systemd" / "host-recovery.defaults.toml"


def _policy():
    return load_host_recovery_policy(BASELINE, enforce_root_metadata=False)


def _payload() -> dict[str, object]:
    payload = bootstrap_payload()
    payload["claim_type_rules"] = mvp_claim_type_rules().model_dump(mode="json")
    payload["activation_limits"] = {
        "offline_activation_max_invalid_questions": 10
    }
    return payload


def test_marker_is_visible_before_stop_and_removed_after_publish(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    marker = tmp_path / "run" / "active"
    journal = HostTransitionJournal(HostJournalPaths(tmp_path / "state"))
    observed_marker: list[bool] = []

    def stop_and_verify() -> tuple[str, ...]:
        observed_marker.append(marker.is_file())
        return ("noezema-orchestrator.service",)

    result = run_offline_rules(
        _payload(),
        journal=journal,
        policy=_policy(),
        boot_id=BOOT_ID,
        marker_path=marker,
        activator=OfflineRulesActivator(session_factory, clock=lambda: NOW),
        stop_and_verify=stop_and_verify,
        clock=lambda: NOW,
    )

    assert result == 0
    assert observed_marker == [True]
    assert not marker.exists()
    active = journal.active()
    assert active is not None and active.state is HostTransitionState.CHECKING
    assert active.operation is HostOperation.OFFLINE_RULES
    with session_factory() as db:
        head = db.get(RuntimeConfigHeadRecord, "global")
        assert head is not None
        assert head.active_config_snapshot_id != BOOTSTRAP_CONFIG_SNAPSHOT_ID

    with session_factory.begin() as db:
        db.add(
            SystemConstantRecord(
                key="invalid_question_uuid5_namespace",
                value=str(INVALID_QUESTION_NAMESPACE),
                created_at=NOW,
            )
        )
    starts: list[bool] = []
    recovery = HostRecoveryService(
        journal,
        session_factory,
        policy=_policy(),
        boot_id=BOOT_ID,
        start_target=lambda: starts.append(True) is None,
        clock=lambda: NOW,
    )
    assert recovery.run_once() == 0
    assert starts == [True]
    assert journal.active() is None


def test_quiescence_failure_leaves_due_resume_work_and_removes_owned_marker(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    marker = tmp_path / "run" / "active"
    journal = HostTransitionJournal(HostJournalPaths(tmp_path / "state"))

    def fail_stop() -> tuple[str, ...]:
        assert marker.exists()
        raise RuntimeError("member remained active")

    result = run_offline_rules(
        _payload(),
        journal=journal,
        policy=_policy(),
        boot_id=BOOT_ID,
        marker_path=marker,
        activator=OfflineRulesActivator(session_factory, clock=lambda: NOW),
        stop_and_verify=fail_stop,
        clock=lambda: NOW,
    )

    assert result == 1
    assert not marker.exists()
    active = journal.active()
    assert active is not None and active.state is HostTransitionState.RESUME_DEGRADED
    assert active.snapshot.next_attempt_at == NOW


def test_existing_transition_rejects_second_maintenance_without_touching_marker(
    session_factory: sessionmaker[Session],
    tmp_path: Path,
) -> None:
    marker = tmp_path / "run" / "active"
    journal = HostTransitionJournal(HostJournalPaths(tmp_path / "state"))
    existing = journal.create(
        operation=HostOperation.RUNTIME_START,
        boot_id=BOOT_ID,
        policy=_policy(),
        actor="test",
        reason="already recovering",
        occurred_at=NOW,
    )
    stops: list[bool] = []

    result = run_offline_rules(
        _payload(),
        journal=journal,
        policy=_policy(),
        boot_id=BOOT_ID,
        marker_path=marker,
        activator=OfflineRulesActivator(session_factory, clock=lambda: NOW),
        stop_and_verify=lambda: stops.append(True) or (),
        clock=lambda: NOW,
    )

    assert result == 75
    assert not marker.exists()
    assert stops == []
    active = journal.active()
    assert active is not None and active.attempt_id == existing.attempt_id
