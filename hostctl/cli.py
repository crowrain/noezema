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
        except (ConfigError, WakeScheduleError, ReassessmentAdmissionError) as exc:
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


if __name__ == "__main__":
    main()
