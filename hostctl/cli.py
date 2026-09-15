"""noezemactl — the trusted host-side operator CLI (T3.10, §8.7.1.1).

Root-only commands. The CLI is a thin wrapper over the trusted helper
modules (policy, journal, offline_rules, admission, resume,
policy_change, unit_state); all invariants live in those modules, not here.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import click

from hostctl import policy_change, resume, unit_state
from hostctl.journal import JournalStore
from hostctl.policy import PolicyError

DEFAULT_HOST_LIB = Path("/var/lib/noezema")
DEFAULT_OVERRIDE = Path("/etc/noezema/host-recovery.toml")
DEFAULT_BASELINE = Path("/usr/lib/noezema/host-recovery.defaults.toml")
DEFAULT_UNIT_STATE = Path("/run/noezema/unit-state.json")

EXIT_USAGE_ERROR = 2


@click.group()
def main() -> None:
    """noezemactl — host recovery operator CLI (root)."""


@main.command("resume-runtime")
@click.option("--reason", required=True, help="Audited reason for the (re)start.")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def resume_runtime(reason: str, host_lib: str) -> None:
    """Run one resume probe and exit with the outcome code.

    exit 0  -> retry_wait / resolved (the retry timer or systemd proceeds)
    exit 78 -> resume_blocked (permanent/inconsistent; alert raised)
    """
    import os

    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from packages.domain.services.audit import AuditService

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    import asyncio

    async def _run() -> int:
        override = DEFAULT_OVERRIDE if DEFAULT_OVERRIDE.exists() else None
        baseline = DEFAULT_BASELINE if DEFAULT_BASELINE.exists() else None
        try:
            async with factory() as db:
                outcome = await resume.run_resume_probe(
                    db,
                    host_lib_base=Path(host_lib),
                    policy_override=override,
                    policy_baseline=baseline,
                    boot_id=unit_state.read_boot_id(),
                )
                if outcome.outcome in ("resume_blocked", "resume_degraded"):
                    store = JournalStore(Path(host_lib))
                    await replay_and_alert(db, outcome.attempt_id or "", store)
        finally:
            await engine.dispose()
        return outcome.exit_code

    async def replay_and_alert(db: AsyncSession, attempt_id: str, store: JournalStore) -> None:
        audit = AuditService(db)
        await resume.replay_audit_events(db, audit, attempt_id=attempt_id, store=store)
        await db.commit()

    exit_code = asyncio.run(_run())
    click.echo(f"resume-runtime: reason={reason} exit={exit_code}")
    sys.exit(exit_code)


@main.command("install-host-recovery-policy")
@click.option("--file", "policy_file", required=True, type=click.Path(exists=True))
@click.option("--reason", required=True)
@click.option("--actor", default="operator")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def install_host_recovery_policy(policy_file: str, reason: str, actor: str, host_lib: str) -> None:
    """Validate + atomically install the host recovery policy (audited)."""
    try:
        policy = policy_change.install_policy(
            base=Path(host_lib),
            override=DEFAULT_OVERRIDE,
            candidate_file=Path(policy_file),
            actor=actor,
            reason=reason,
        )
    except (PolicyError, policy_change.PolicyChangeError) as exc:
        click.echo(f"install-host-recovery-policy failed: {exc}", err=True)
        sys.exit(1)
    click.echo(f"installed policy sha256={policy.host_policy_sha256}")


@main.command("resolve-host-policy")
@click.option("--accept-current", is_flag=True)
@click.option("--file", "replacement_file", type=click.Path())
@click.option("--reason", required=True)
@click.option("--actor", default="operator")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def resolve_host_policy(
    accept_current: bool, replacement_file: str | None, reason: str, actor: str, host_lib: str
) -> None:
    """Resolve the active host-policy change (accept-current or --file)."""
    if not accept_current and replacement_file is None:
        click.echo("resolve-host-policy requires --accept-current or --file", err=True)
        sys.exit(1)
    try:
        policy = policy_change.resolve_policy(
            base=Path(host_lib),
            override=DEFAULT_OVERRIDE,
            actor=actor,
            reason=reason,
            accept_current=accept_current,
            replacement_file=Path(replacement_file) if replacement_file else None,
        )
    except (PolicyError, policy_change.PolicyChangeError) as exc:
        click.echo(f"resolve-host-policy failed: {exc}", err=True)
        sys.exit(1)
    click.echo(f"resolved policy sha256={policy.host_policy_sha256}")


@main.command("publish-unit-state")
@click.option("--path", type=click.Path(), default=str(DEFAULT_UNIT_STATE))
def publish_unit_state(path: str) -> None:
    """Publish the current unit-state snapshot (idempotent, 1s timer)."""
    units = unit_state.collect_unit_states()
    snapshot = unit_state.publish_unit_state(Path(path), units=units)
    click.echo(f"unit-state published: {len(snapshot.units)} units, boot_id={snapshot.boot_id[:8]}")


@main.command("offline-rules")
@click.option("--payload", "payload_file", required=True, type=click.Path(exists=True))
@click.option("--reason", required=True)
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def offline_rules(payload_file: str, reason: str, host_lib: str) -> None:
    """Run the offline rules change (runtime must be stopped, root)."""
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import hostctl.offline_rules as orl
    from packages.domain.config import QUESTION_UUID5_NAMESPACE
    from packages.domain.services.audit import AuditService

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)
    import json

    with open(payload_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> int:
        try:
            async with factory() as db, db.begin():
                audit = AuditService(db)
                result = await orl.run_offline_change(
                    db,
                    audit,
                    requested_payload=payload,
                    question_namespace=uuid.UUID(QUESTION_UUID5_NAMESPACE),
                )
        except orl.OfflineRulesError as exc:
            click.echo(f"offline-rules failed: {exc}", err=True)
            return 1
        click.echo(
            f"offline-rules: state={result.state} published={result.published} "
            f"invalid_questions={result.invalid_questions_created}"
        )
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    click.echo(f"offline-rules: reason={reason} exit={code}")
    sys.exit(code)


@main.command("wake-tick")
@click.option("--node-owner", default=None, help="Node identity (default: $NOEZEMA_NODE_OWNER).")
@click.option(
    "--data-root",
    type=click.Path(),
    default=None,
    help="Node data root for the disk quota gate (default: $NOEZEMA_DATA_ROOT).",
)
def wake_tick(node_owner: str | None, data_root: str | None) -> None:
    """Evaluate one wake tick (T3.29, §5.2.1) and run a session if admitted.

    The schedule, admission gates and backoff live in the effective config
    snapshot (wake_schedule). wait/skip is not an error; a non-zero exit
    means the admitted session failed or the node is misconfigured.
    """
    import asyncio
    import os

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)

    from apps.orchestrator.main import build_orchestrator
    from apps.orchestrator.scheduler import (
        ReassessmentAdmissionError,
        RepairAdmissionError,
        WakeScheduleError,
        WakeScheduler,
        data_root_from_env,
        node_owner_from_env,
    )
    from packages.domain.services.config import ConfigError

    owner = node_owner or node_owner_from_env()
    root = Path(data_root) if data_root else data_root_from_env()

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _tick() -> int:
        from datetime import UTC, datetime

        try:
            async with factory() as db:
                decision = await WakeScheduler(db, node_owner=owner, data_root=root).decide(
                    source="scheduled", now=datetime.now(UTC)
                )
        except (
            ConfigError,
            WakeScheduleError,
            ReassessmentAdmissionError,
            RepairAdmissionError,
        ) as exc:
            # Fail-closed: a broken effective config must not start a session.
            click.echo(f"wake-tick: fail-closed ({type(exc).__name__}: {exc})", err=True)
            return 78
        if decision.action != "wake":
            click.echo(f"wake-tick: {decision.action} ({decision.reason})")
            return 0

        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO system_constants (key, value) VALUES ('node_state', :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = :v"
                ),
                {"v": "session_running"},
            )

        orchestrator, gateway = build_orchestrator(factory, root / "workspace")
        try:
            outcome = await orchestrator.run_session()
        except Exception as exc:
            # Infra failure around the session; the session row is left to the
            # reconciler, and the failure counts for the backoff.
            final_state = "failed"
            click.echo(f"wake-tick: session error ({type(exc).__name__}: {exc})", err=True)
        else:
            final_state = outcome.final_state.value
        finally:
            await gateway.close()

        async with factory() as db:
            node_state = await WakeScheduler(db, node_owner=owner, data_root=root).record_session_result(
                final_state=final_state, now=datetime.now(UTC)
            )
        click.echo(f"wake-tick: session -> {final_state} (node_state={node_state})")
        return 0 if final_state in ("succeeded", "succeeded_partial") else 1

    async def _run() -> int:
        try:
            return await _tick()
        finally:
            await engine.dispose()

    sys.exit(asyncio.run(_run()))


@main.command("reassessment-tick")
@click.option("--batch-size", default=8, show_default=True, help="Bounded batch size.")
@click.option(
    "--lease-seconds", default=300, show_default=True, help="Per-job lease TTL."
)
def reassessment_tick(batch_size: int, lease_seconds: int) -> None:
    """Run one reassessment worker batch (T4.3/T4.4, §5.9.1).

    The liveness driver: the scheduler (wake-tick) runs sessions and
    SKIPS them while the dependency-critical queue is over
    ``T_worker_admission``; this command (its own timer in v1) drains the
    queue in the window between sessions. Crash-recovery of expired job
    leases runs first. The worker takes the writer gate NOWAIT and
    yields to the session commit intent — it never fights a session.
    """
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)

    from packages.memory.reassessment import (
        recover_expired_leases,
        run_reassessment_batch,
    )

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> int:
        async with factory() as db:
            recovered = await recover_expired_leases(db)
        async with factory() as db:
            outcome = await run_reassessment_batch(
                db, batch_size=batch_size, lease_seconds=lease_seconds
            )
        click.echo(
            f"reassessment-tick: recovered={recovered} "
            f"processed={outcome.processed} completed={outcome.completed} "
            f"retried={outcome.retried} blocked={outcome.blocked} "
            f"deferred={outcome.deferred}"
        )
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(code)


@main.command("activate-online")
@click.option("--payload", "payload_file", required=True, type=click.Path(exists=True))
@click.option("--reason", required=True)
def activate_online(payload_file: str, reason: str) -> None:
    """Run an ONLINE config change (T4.5, §8.7.2; runtime is live, root).

    Crash-idempotent: the run resumes from the candidate state
    (preparing_heads / ready / publishing / post_publish). The
    activating slot quiesces the worker and sessions for the whole
    prepare → flip → post-publish window; the slot clears in the
    terminal cleanup. exit 1 means the activation failed (or is
    blocked — the repair lane completes a post_publish_blocked
    manifest).
    """
    import asyncio
    import json
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import packages.memory.activation as act
    from packages.domain.services.audit import AuditService

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)

    with open(payload_file, encoding="utf-8") as fh:
        payload = json.load(fh)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> int:
        async with factory() as db:
            audit = AuditService(db)
            try:
                result = await act.run_online_change(db, audit, requested_payload=payload)
            except act.ActivationError as exc:
                click.echo(f"activate-online failed: {exc}", err=True)
                return 1
        click.echo(
            f"activate-online: state={result.state} published={result.published} "
            f"resumed={result.resumed} pending={result.pending_heads} "
            f"questions={result.questions_created}"
        )
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    click.echo(f"activate-online: reason={reason} exit={code}")
    sys.exit(code)


@main.command("activation-repair-tick")
@click.option("--batch-size", default=64, show_default=True, help="Bounded batch size.")
def activation_repair_tick(batch_size: int) -> None:
    """Run one repair batch over the post_publish_blocked manifest
    (T4.5, §8.7.2; root).

    The trusted repair lane: the slot is already cleared by the
    terminal cleanup, so the batch CAS is the repair CAS (active =
    candidate, activating IS NULL, state post_publish_blocked, due
    cursor). A superseded pointer closes the remainder as
    ``superseded``. No backlog → a no-op.
    """
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    import packages.memory.activation as act
    from packages.domain.services.audit import AuditService

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _run() -> int:
        async with factory() as db:
            audit = AuditService(db)
            candidate = await act.find_repair_backlog(db)
        if candidate is None:
            click.echo("activation-repair-tick: no repair backlog")
            return 0
        async with factory() as db:
            audit = AuditService(db)
            try:
                result = await act.run_activation_repair(
                    db, audit, candidate=candidate, batch_size=batch_size
                )
            except act.ActivationError as exc:
                click.echo(f"activation-repair-tick failed: {exc}", err=True)
                return 1
        click.echo(
            f"activation-repair-tick: state={result.state} cursor={result.cursor} "
            f"questions={result.questions_created} deferred={result.deferred}"
        )
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(code)


@main.command("show-policy")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def show_policy(host_lib: str) -> None:
    """Show the effective recovery policy (override first, else baseline)."""
    override = DEFAULT_OVERRIDE if DEFAULT_OVERRIDE.exists() else None
    baseline = DEFAULT_BASELINE if DEFAULT_BASELINE.exists() else None
    try:
        policy, source = resume.load_resume_policy(override=override, baseline=baseline)
    except PolicyError as exc:
        click.echo(f"no valid recovery policy: {exc}", err=True)
        sys.exit(1)
    click.echo(f"source={source} sha256={policy.host_policy_sha256}")
    click.echo(
        f"initial={policy.resume_retry_initial}ns max={policy.resume_retry_max}ns jitter={policy.resume_retry_jitter}"
    )


@main.command("backup")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
@click.option("--retention-days", type=int, default=30, show_default=True)
def backup(host_lib: str, retention_days: int) -> None:
    """Create one backup manifest: DB recovery point + artifact inventory
    + host-contour state (§15.3). Root-only; audited."""
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.artifacts.store import FilesystemArtifactStore
    from packages.backup.service import BackupError, create_backup
    from packages.domain.db.uow import transaction

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store_root = Path(os.environ.get("NOEZEMA_ARTIFACTS_ROOT", "/var/lib/noezema/artifacts"))

    async def _run() -> int:
        async with factory() as db, transaction(db):
            try:
                result = await create_backup(
                    db,
                    FilesystemArtifactStore(store_root),
                    Path(host_lib),
                    retention_days=retention_days,
                    policy_baseline=DEFAULT_BASELINE if DEFAULT_BASELINE.exists() else None,
                    policy_override=DEFAULT_OVERRIDE if DEFAULT_OVERRIDE.exists() else None,
                )
            except BackupError as exc:
                click.echo(f"backup failed: {exc}", err=True)
                return 1
        click.echo(
            f"backup: id={result['backup_id']} recovery_point="
            f"{result['database_recovery_point']} inventory="
            f"{result['artifact_inventory_hash'][:12]} "
            f"host_ops_absent={result['host_state']['host_ops_absent']}"
        )
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(code)


@main.command("restore-drill")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def restore_drill(host_lib: str) -> None:
    """Run one restore drill on a random retained backup point (§15.3):
    verify every referenced hash + boot reconciliation/admission before
    the runtime would start. Exit 0 = passed, 1 = failed, 2 = no usable
    backup point."""
    import asyncio
    import os
    import random

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.artifacts.store import FilesystemArtifactStore
    from packages.backup.restore import RestoreDrillError, run_restore_drill
    from packages.domain.db.uow import transaction

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store_root = Path(os.environ.get("NOEZEMA_ARTIFACTS_ROOT", "/var/lib/noezema/artifacts"))

    async def _run() -> int:
        async with factory() as db, transaction(db):
            try:
                drill = await run_restore_drill(
                    db,
                    FilesystemArtifactStore(store_root),
                    Path(host_lib),
                    policy_baseline=DEFAULT_BASELINE if DEFAULT_BASELINE.exists() else None,
                    policy_override=DEFAULT_OVERRIDE if DEFAULT_OVERRIDE.exists() else None,
                    rng=random.Random(),
                )
            except RestoreDrillError as exc:
                click.echo(str(exc), err=True)
                return 2
        click.echo(f"restore-drill: outcome={drill.outcome} backup={drill.backup_id}")
        for problem in drill.problems:
            click.echo(f"  problem: {problem}", err=True)
        click.echo(
            f"  inventory={drill.inventory_ok}/{drill.inventory_checked} "
            f"policy_files={drill.policy_files_ok}/{drill.policy_files_checked} "
            f"admission_ok={drill.admission['ok']}"
        )
        return 0 if drill.outcome == "passed" else 1

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(code)


@main.command("gc")
@click.option("--apply", is_flag=True, default=False, help="Actually delete (default: dry run).")
@click.option("--host-lib", type=click.Path(), default=str(DEFAULT_HOST_LIB))
def gc(apply: bool, host_lib: str) -> None:
    """Run one GC sweep (§15.3, §20.12): the full root set is computed
    and the retention candidates reported. ``--apply`` deletes in one
    transaction (artifacts + store objects first, then registry rows)
    and records the audit ``gc_sweep``. GC of a session in
    ``reconciling_commit`` is forbidden (its rows are skipped)."""
    import asyncio
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from packages.artifacts.store import FilesystemArtifactStore
    from packages.domain.db.uow import transaction
    from packages.gc.service import run_gc

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    store_root = Path(os.environ.get("NOEZEMA_ARTIFACTS_ROOT", "/var/lib/noezema/artifacts"))

    async def _run() -> int:
        async with factory() as db, transaction(db):
            result = await run_gc(
                db,
                artifact_store=FilesystemArtifactStore(store_root) if apply else None,
                apply=apply,
            )
        click.echo(
            f"gc ({'apply' if apply else 'dry-run'}): roots={result['root_artifact_count']} "
            f"artifact_candidates={len(result['candidate_artifacts'])} "
            f"workspace={len(result['candidate_workspace_manifests'])} "
            f"terminal_attempts={len(result['candidate_terminal_attempts'])} "
            f"config_attempts={len(result['candidate_terminal_config_attempts'])} "
            f"barriers={len(result['candidate_resolved_barriers'])} "
            f"expired_backups={len(result['expired_backup_manifests'])}"
        )
        if result["reconciling_sessions"]:
            click.echo(
                "skipped (reconciling_commit): "
                + ", ".join(result["reconciling_sessions"]),
                err=True,
            )
        if apply:
            click.echo(f"deleted={result['deleted']}")
        return 0

    try:
        code = asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    sys.exit(code)


@main.command("security-gate")
@click.option(
    "--python",
    "python_bin",
    type=str,
    default=None,
    help="Python interpreter to run pytest with (default: sys.executable).",
)
@click.option(
    "--metrics-url",
    type=str,
    default=None,
    help="Optional NOEZEMA_DATABASE_URL to also emit the §16.3 security report.",
)
def security_gate(python_bin: str | None, metrics_url: str | None) -> None:
    """T7.4 (stage 7): the security regression gate.

    Runs the full ``pytest -m security`` suite as a subprocess and
    exits 0 only if every security test passes. If ``--metrics-url``
    is given, also emits the §16.3 security report from the database
    before the gate runs (so the gate log carries the metric baseline).
    Wire this as a CI / systemd gate: non-zero exit = gate failed.
    """
    import asyncio
    import os
    import subprocess
    import sys

    # emit the §16.3 report if a DB is available
    if metrics_url:
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        from apps.web.metrics import security_metrics

        os.environ["NOEZEMA_DATABASE_URL"] = metrics_url
        engine = create_async_engine(metrics_url)
        factory = async_sessionmaker(engine, expire_on_commit=False)

        async def _emit() -> None:
            async with factory() as db:
                report = await security_metrics(db)

            click.echo("security report (§16.3):")
            for k, v in report.items():
                click.echo(f"  {k}: {v}")

        try:
            asyncio.run(_emit())
        finally:
            asyncio.run(engine.dispose())

    # run the security suite
    cmd = [python_bin or sys.executable, "-m", "pytest", "-m", "security", "-q"]
    click.echo(f"running security suite: {' '.join(cmd)}")
    proc = subprocess.run(cmd, cwd=os.getcwd())
    click.echo(f"security gate: {'PASSED' if proc.returncode == 0 else 'FAILED'}")
    sys.exit(proc.returncode)


@main.command("eval-run")
@click.option("--label", required=True, help="Run label (e.g. EVAL-1).")
@click.option(
    "--questions",
    "questions_file",
    required=True,
    type=click.Path(exists=True, dir_okay=False),
    help="JSONL question corpus (one {'text': ...} per line); seeded as origin='seeded'.",
)
@click.option("--count", default=50, show_default=True, help="Number of sessions to run.")
@click.option(
    "--slo-seconds",
    default=3600.0,
    show_default=True,
    help="Fixed reassessment wall-clock SLO (§22.2: fixed before the series).",
)
@click.option("--seed", default=20260915, show_default=True, help="Blind sample seed.")
@click.option("--blind-size", default=50, show_default=True, help="Blind sample size.")
@click.option(
    "--skip-sessions",
    is_flag=True,
    help="Do not run the series (freeze + finish only); for gate re-computation.",
)
def eval_run(
    label: str,
    questions_file: str,
    count: int,
    slo_seconds: float,
    seed: int,
    blind_size: int,
    skip_sessions: bool,
) -> None:
    """T7.7 (stage 7): the actual §22.2 evaluation run.

    Freezes the run configuration (model fingerprint, effective config
    snapshot, rules version/hash, thresholds incl. the fixed SLO, blind
    seed/size) BEFORE the series, seeds the question corpus (origin
    'seeded'), runs ``count`` real sessions through the standard wake
    admission + orchestrator pipeline, then computes all 11 §22.2
    gates from the domain data and finishes the run.
    """
    import asyncio
    import hashlib
    import json
    import os

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    url = os.environ.get("NOEZEMA_DATABASE_URL", "")
    if not url:
        click.echo("NOEZEMA_DATABASE_URL is not set", err=True)
        sys.exit(EXIT_USAGE_ERROR)
    from apps.orchestrator.main import build_orchestrator
    from apps.orchestrator.scheduler import (
        ReassessmentAdmissionError,
        RepairAdmissionError,
        WakeScheduleError,
        WakeScheduler,
        data_root_from_env,
        node_owner_from_env,
    )
    from packages.domain.services.config import ConfigError, ConfigService
    from packages.evaluation.gates import compute_gates
    from packages.evaluation.service import (
        create_evaluation_run,
        finish_evaluation_run,
        get_evaluation_run,
    )
    from packages.llm_gateway.config import LLMGatewayConfig
    from packages.memory.evidence import RULES_ENGINE_VERSION, rules_hash

    owner = node_owner_from_env()
    root = data_root_from_env()
    llm = LLMGatewayConfig()

    raw = Path(questions_file).read_text(encoding="utf-8")
    questions = [
        json.loads(line)
        for line in raw.splitlines()
        if line.strip() and not line.startswith("#")
    ]
    corpus_sha = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    click.echo(f"corpus: {len(questions)} questions, sha256={corpus_sha[:16]}…")

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _freeze() -> uuid.UUID:
        async with factory() as db, db.begin():
            effective = await ConfigService.get_effective(db)
            mf = {
                "model": llm.model,
                "base_url": llm.base_url,
                "max_output_tokens": llm.max_output_tokens,
                "corpus_sha256": corpus_sha,
                "corpus_size": len(questions),
            }
            run = await create_evaluation_run(
                db,
                label=label,
                config_snapshot_id=effective.id,
                model_fingerprint=mf,
                rules_version=RULES_ENGINE_VERSION,
                rules_hash=rules_hash(dict(effective.claim_type_rules)),
                thresholds={"reassessment_slo_seconds": slo_seconds},
                blind_sample_seed=seed,
                blind_sample_size=blind_size,
            )
            click.echo(
                f"run frozen: id={run.id} snapshot={effective.id} "
                f"model={llm.model} slo={slo_seconds}s seed={seed} size={blind_size}"
            )
            return run.id

    async def _seed_questions() -> None:
        async with factory() as db, db.begin():
            n = 0
            for q in questions:
                t = str(q["text"])
                exists = (
                    await db.execute(
                        text("SELECT 1 FROM questions WHERE text = :t"), {"t": t}
                    )
                ).first()
                if exists is not None:
                    continue
                await db.execute(
                    text(
                        "INSERT INTO questions (id, text, origin, state) "
                        "VALUES (:id, :t, 'seeded', 'candidate')"
                    ),
                    {"id": uuid.uuid4(), "t": t},
                )
                n += 1
            click.echo(f"seeded {n} new questions (corpus frozen)")

    async def _node_state_set(state: str) -> None:
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO system_constants (key, value) VALUES ('node_state', :v) "
                    "ON CONFLICT (key) DO UPDATE SET value = :v"
                ),
                {"v": state},
            )

    async def _load_node_state() -> str | None:
        async with factory() as db:
            return (
                await db.execute(
                    text(
                        "SELECT value FROM system_constants WHERE key = 'node_state'"
                    )
                )
            ).scalar_one_or_none()

    async def _operator_resume() -> None:
        """Operator resume semantics (web command API): clear the sticky
        pause + failure bookkeeping so the series can continue."""
        async with factory() as db, db.begin():
            await db.execute(
                text("UPDATE system_constants SET value = 'idle' WHERE key = 'node_state'")
            )
            await db.execute(
                text(
                    "UPDATE wake_scheduler_state SET consecutive_failures = 0, "
                    "backoff_until = NULL, last_failure_at = NULL, "
                    "paused_reason = NULL, updated_at = now() WHERE node_id = :n"
                ),
                {"n": owner},
            )

    async def _run_sessions() -> tuple[int, int]:
        import time as _time
        from datetime import UTC, datetime

        completed = 0
        succeeded = 0
        for i in range(count):
            deadline = _time.monotonic() + 600
            decision = None
            while _time.monotonic() < deadline:
                async with factory() as db:
                    decision = await WakeScheduler(
                        db, node_owner=owner, data_root=root
                    ).decide(source="wake_now", now=datetime.now(UTC))
                if decision.action == "wake":
                    break
                if decision.action == "skip" and await _load_node_state() == "paused":
                    # an auto-pause (consecutive failures) is sticky until the
                    # operator resume; apply that semantics once, then retry
                    click.echo(
                        f"session {i + 1}: node paused ({decision.reason}); "
                        "operator resume"
                    )
                    await _operator_resume()
                    continue
                click.echo(
                    f"session {i + 1}: {decision.action} ({decision.reason}); retry"
                )
                await asyncio.sleep(10)
            if decision is None or decision.action != "wake":
                click.echo(
                    f"session {i + 1}: admission never granted; aborting series", err=True
                )
                break

            await _node_state_set("session_running")
            orchestrator, gateway = build_orchestrator(factory, root / "workspace")
            t0 = _time.monotonic()
            outcome_steps = 0
            try:
                outcome = await orchestrator.run_session()
            except Exception as exc:  # infra failure around the session
                # T7.7 (EVAL-2): a LeaseLost can be reported after the
                # session already committed — the guard's last refused
                # renewal (phase deadline) is detected at guard exit,
                # while the fenced commit's own is_live() check passed.
                # The durable row wins: re-read the session state before
                # counting a failure (the EVAL-2 smoke session
                # f8c548cc was succeeded in the DB but reported failed).
                final_state = "failed"
                outcome_steps = 0
                if "LeaseLost" in type(exc).__name__ or "lease lost" in str(exc).lower():
                    # the session id is in the LeaseLost message
                    sid = None
                    for part in str(exc).split():
                        if len(part) == 36 and part.count("-") == 4:
                            sid = part
                            break
                    if sid is not None:
                        async with factory() as probe:
                            row = (
                                await probe.execute(
                                    text(
                                        "SELECT state, termination_reason "
                                        "FROM sessions WHERE id = :id"
                                    ),
                                    {"id": uuid.UUID(sid)},
                                )
                            ).first()
                    else:
                        row = None
                    if row is not None and row[0] in (
                        "succeeded", "succeeded_partial"
                    ):
                        final_state = row[0]
                        click.echo(
                            f"session {i + 1}: lease-lost reported after "
                            f"durable {row[0]} commit (reason={row[1]!r}) — "
                            f"counted as {final_state}"
                        )
                    else:
                        click.echo(
                            f"session {i + 1}: error (LeaseLost: {exc})", err=True
                        )
                else:
                    click.echo(f"session {i + 1}: error ({type(exc).__name__}: {exc})", err=True)
            else:
                final_state = outcome.final_state.value
                outcome_steps = outcome.steps
            finally:
                await gateway.close()
            elapsed = _time.monotonic() - t0
            async with factory() as db:
                node_state = await WakeScheduler(
                    db, node_owner=owner, data_root=root
                ).record_session_result(final_state=final_state, now=datetime.now(UTC))
            completed += 1
            if final_state in ("succeeded", "succeeded_partial"):
                succeeded += 1
            click.echo(
                f"session {i + 1}/{count}: {final_state} (steps={outcome_steps}, "
                f"{elapsed:.0f}s, node_state={node_state})"
            )
        return completed, succeeded

    async def _finish(run_id: uuid.UUID, completed: int, succeeded: int) -> None:
        from datetime import UTC, datetime

        async with factory() as db, db.begin():
            run = await get_evaluation_run(db, run_id)
            assert run is not None
            gates = await compute_gates(db, run=run)
            finished = await finish_evaluation_run(
                db,
                run_id,
                gates=gates,
                eligible_sessions=completed,
                completed_sessions=succeeded,
                now=datetime.now(UTC),
            )
            click.echo(f"run finished: outcome={finished.outcome}")
            for name, g in gates.items():
                click.echo(f"  {name}: {g.get('outcome')} {g}")

    async def _main() -> int:
        run_id = await _freeze()
        if skip_sessions:
            click.echo("--skip-sessions: freezing only, finishing now")
            await _finish(run_id, 0, 0)
        else:
            await _seed_questions()
            completed, succeeded = await _run_sessions()
            await _finish(run_id, completed, succeeded)
        return 0

    try:
        sys.exit(asyncio.run(_main()))
    except (
        ConfigError,
        WakeScheduleError,
        ReassessmentAdmissionError,
        RepairAdmissionError,
    ) as exc:
        click.echo(f"eval-run: fail-closed ({type(exc).__name__}: {exc})", err=True)
        sys.exit(78)


if __name__ == "__main__":
    main()
