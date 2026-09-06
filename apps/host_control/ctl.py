"""Audited operator escape hatch for a blocked host recovery."""

from __future__ import annotations

import argparse
import os
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from apps.host_control.journal import (
    HostJournalError,
    HostJournalPaths,
    HostTransitionJournal,
    HostTransitionRecord,
    HostTransitionState,
)
from apps.host_control.policy import load_host_recovery_policy
from apps.host_control.policy_change import HostPolicyInstaller, PolicyChangeJournal


def retry_blocked_transition(
    journal: HostTransitionJournal,
    *,
    actor: str,
    reason: str,
    occurred_at: datetime,
) -> HostTransitionRecord:
    """Turn one explicit operator decision into the next durable probe."""

    if not actor.strip() or not reason.strip():
        raise ValueError("operator actor and reason are required")
    record = journal.active()
    if record is None or record.state is not HostTransitionState.RESUME_BLOCKED:
        raise HostJournalError("there is no blocked host transition to resume")
    return journal.transition(
        record.attempt_id,
        to_state=HostTransitionState.CHECKING,
        actor=actor.strip()[:256],
        reason=reason.strip()[:1024],
        occurred_at=_aware(occurred_at),
        snapshot_updates={
            "attempts_total": record.snapshot.attempts_total,
            "current_attempt_seq": record.snapshot.current_attempt_seq,
            "last_probe_boot_id": None,
            "last_probe_started_at": None,
            "last_probe_classified_at": None,
            "next_attempt_at": None,
            "dispatch_id": None,
            "dispatch_deadline": None,
        },
    )


def main(arguments: Sequence[str] | None = None, environment: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="noezemactl")
    commands = parser.add_subparsers(dest="command", required=True)
    resume = commands.add_parser("resume-runtime")
    resume.add_argument("--reason", required=True)
    resume.add_argument("--actor", default="local-operator")
    install = commands.add_parser("install-host-recovery-policy")
    install.add_argument("--file", type=Path, required=True)
    install.add_argument("--reason", required=True)
    install.add_argument("--actor", default="local-operator")
    resolve = commands.add_parser("resolve-host-policy")
    resolution = resolve.add_mutually_exclusive_group(required=True)
    resolution.add_argument("--file", type=Path)
    resolution.add_argument("--accept-current", action="store_true")
    resolve.add_argument("--reason", required=True)
    resolve.add_argument("--actor", default="local-operator")
    parsed = parser.parse_args(arguments)
    values = os.environ if environment is None else environment
    if os.name != "nt" and os.geteuid() != 0:
        parser.error("noezemactl host operations must run as root")
    baseline = Path(
        values.get(
            "NOEZEMA_HOST_RECOVERY_DEFAULTS_PATH",
            "/usr/lib/noezema/host-recovery.defaults.toml",
        )
    )
    override_value = values.get(
        "NOEZEMA_HOST_RECOVERY_OVERRIDE_PATH",
        "/etc/noezema/host-recovery.toml",
    ).strip()
    override = Path(override_value) if override_value else Path(
        "/etc/noezema/host-recovery.toml"
    )
    paths = HostJournalPaths(
        Path(values.get("NOEZEMA_HOST_STATE_ROOT", "/var/lib/noezema")),
        lock_path=Path(
            values.get(
                "NOEZEMA_HOST_TRANSITION_LOCK_PATH",
                "/run/lock/noezema-host-transition.lock",
            )
        ),
    )
    if parsed.command == "install-host-recovery-policy":
        HostPolicyInstaller(
            PolicyChangeJournal(paths),
            baseline_path=baseline,
            override_path=override,
        ).install(
            parsed.file,
            actor=parsed.actor,
            reason=parsed.reason,
            occurred_at=datetime.now(UTC),
        )
        return 0
    if parsed.command == "resolve-host-policy":
        installer = HostPolicyInstaller(
            PolicyChangeJournal(paths),
            baseline_path=baseline,
            override_path=override,
        )
        if parsed.accept_current:
            installer.resolve_accept_current(
                actor=parsed.actor,
                reason=parsed.reason,
                occurred_at=datetime.now(UTC),
            )
        else:
            installer.resolve_with_file(
                parsed.file,
                actor=parsed.actor,
                reason=parsed.reason,
                occurred_at=datetime.now(UTC),
            )
        return 0

    load_host_recovery_policy(
        baseline,
        override_path=override,
    )
    journal = HostTransitionJournal(paths)
    retry_blocked_transition(
        journal,
        actor=parsed.actor,
        reason=parsed.reason,
        occurred_at=datetime.now(UTC),
    )
    completed = subprocess.run(
        ("systemctl", "start", "noezema-runtime-resume.service"),
        check=False,
        timeout=30,
    )
    return 0 if completed.returncode == 0 else 75


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("operator retry timestamp must be timezone-aware")
    return value.astimezone(UTC)


if __name__ == "__main__":
    raise SystemExit(main())
