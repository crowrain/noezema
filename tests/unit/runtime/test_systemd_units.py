"""Static contracts for the production systemd supervision graph."""

from __future__ import annotations

from pathlib import Path

_ROOT = Path(__file__).resolve().parents[3]
_SYSTEMD = _ROOT / "infra" / "systemd"
_TMPFILES = _ROOT / "infra" / "tmpfiles.d" / "noezema.conf"


def _text(name: str) -> str:
    return (_SYSTEMD / name).read_text(encoding="utf-8")


def test_runtime_target_and_writer_share_fail_closed_admission() -> None:
    target = _text("noezema-runtime.target")
    writer = _text("noezema-orchestrator.service")

    assert "Requires=noezema-runtime-admission.service noezema-orchestrator.service" in target
    assert "ConditionPathExists=!/run/noezema-offline-rules/active" in target
    assert "PartOf=noezema-runtime.target" in writer
    assert "Requires=noezema-runtime-admission.service" in writer
    assert "ConditionPathExists=!/run/noezema-offline-rules/active" in writer
    assert "ExecStartPre=/opt/noezema/.venv/bin/python -m apps.runtime.admission" in writer
    assert "SupplementaryGroups=noezema-host" in writer
    assert "SupplementaryGroups=noezema-host" in _text("noezema-runtime-admission.service")
    assert "RestartPreventExitStatus=78" in writer
    assert "WantedBy=noezema-runtime.target" in writer
    assert "StopWhenUnneeded" not in target


def test_web_is_outside_the_writer_target_and_cannot_open_the_system_bus() -> None:
    web = _text("noezema-web.service")

    assert "WantedBy=multi-user.target" in web
    assert "PartOf=noezema-runtime.target" not in web
    assert "Requires=noezema-runtime" not in web
    assert "systemctl" not in web
    assert "/run/systemd/private" in web
    assert "/run/dbus/system_bus_socket" in web
    assert "/var/lib/noezema/workspace" in web
    assert "/var/lib/noezema/artifacts" in web
    assert "-/var/lib/noezema/host-transition-head.json" in web
    assert "-/var/lib/noezema/host-policy-change-head.json" in web
    assert "SupplementaryGroups=noezema-host" in web


def test_unit_state_timer_meets_the_snapshot_freshness_budget() -> None:
    timer = _text("noezema-unit-state.timer")
    publisher = _text("noezema-unit-state.service")

    assert "OnBootSec=5s" in timer
    assert "OnUnitInactiveSec=5s" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "User=root" in publisher
    assert "Group=noezema-observer" in publisher
    assert "RuntimeDirectory=noezema" in publisher
    assert "EnvironmentFile=" not in publisher


def test_resume_units_separate_fast_crash_retry_from_classified_db_wait() -> None:
    initial = _text("noezema-runtime-resume.service")
    retry = _text("noezema-runtime-resume-retry.service")
    timer = _text("noezema-runtime-resume-retry.timer")
    failure = _text("noezema-resume-failure@.service")

    assert "WantedBy=multi-user.target" in initial
    assert "NOEZEMA_RECOVERY_CREATE_IF_ABSENT=1" in initial
    assert "NOEZEMA_RECOVERY_CREATE_IF_ABSENT=0" in retry
    for unit in (initial, retry):
        assert "Group=noezema-host" in unit
        assert "Restart=on-failure" in unit
        assert "RestartPreventExitStatus=78" in unit
        assert "StartLimitBurst=6" in unit
        assert "OnFailure=noezema-resume-failure@%n.service" in unit
    assert "OnBootSec=30s" in timer
    assert "OnUnitInactiveSec=30s" in timer
    assert "AccuracySec=1s" in timer
    assert "RandomizedDelaySec=0" in timer
    assert "NOEZEMA_FAILED_RESUME_UNIT=%i" in failure


def test_offline_rules_unit_owns_marker_lifetime_and_always_schedules_resume() -> None:
    unit = _text("noezema-offline-rules@.service")

    assert "User=root" in unit
    assert "RuntimeDirectory=noezema-offline-rules" in unit
    assert "OnSuccess=noezema-runtime-resume.service" in unit
    assert "OnFailure=noezema-runtime-resume.service" in unit
    assert "NOEZEMA_OFFLINE_RULES_PAYLOAD_PATH=" in unit
    assert "ReadWritePaths=/var/lib/noezema /run/noezema-offline-rules" in unit


def test_runtime_and_web_secrets_use_separate_environment_files() -> None:
    admission = _text("noezema-runtime-admission.service")
    writer = _text("noezema-orchestrator.service")
    web = _text("noezema-web.service")
    runtime_environment = _text("noezema-runtime.env.example")
    web_environment = _text("noezema-web.env.example")

    assert "EnvironmentFile=/etc/noezema/noezema-runtime.env" in admission
    assert "EnvironmentFile=/etc/noezema/noezema-runtime.env" in writer
    assert "EnvironmentFile=/etc/noezema/noezema-web.env" in web
    assert "NOEZEMA_WEB_SESSION_SECRET" not in runtime_environment
    assert "NOEZEMA_LLM_API_KEY" not in web_environment
    assert "NOEZEMA_LLM_BASE_URL" not in web_environment


def test_unit_files_use_lf_line_endings() -> None:
    for path in _SYSTEMD.glob("noezema-*"):
        assert b"\r" not in path.read_bytes(), path.name


def test_tmpfiles_precreates_shared_host_state_and_lock_permissions() -> None:
    content = _TMPFILES.read_text(encoding="utf-8")

    assert "d /var/lib/noezema 2750 root noezema-host -" in content
    assert "f /run/lock/noezema-host-transition.lock 0660 root noezema-host -" in content
