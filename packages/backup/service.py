"""T7.2 (stage 7, §15.3, §22.1 item 12): backup creation.

A backup binds the DB recovery point (WAL LSN) with the
content-addressed artifact inventory and the host-contour state. Per
§15.3 the manifest includes the host-transition active head, the
host-policy-change active head, all unresolved current records with
their immutable event directories, the host policy files with their
hashes, the unfinished / retention-window policy event streams — OR the
explicit evidence of the absence of both active operations
(``host_ops_absent``).

The manifest, the inventory artifact registry row, and the audit event
(+ outbox twin) are written by the caller in ONE transaction.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hostctl.journal import JournalStore
from hostctl.policy_change import policy_events_dir, read_policy_head
from packages.artifacts.store import ArtifactStore
from packages.domain.canonical import canonical_json_bytes, sha256_hex
from packages.domain.models.artifacts import ORMArtifact
from packages.domain.models.enums import AuditEventType
from packages.domain.models.memory import ORMBackupManifest

HOST_STATE_SCHEMA_VERSION = 1

#: terminal kinds of the host-policy event stream (§8.7.1.1)
POLICY_STREAM_TERMINAL_KINDS = frozenset({"committed", "aborted", "resolved"})


class BackupError(RuntimeError):
    """Backup creation failed (the caller rolls the transaction back)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_host_state(
    base: Path,
    *,
    policy_baseline: Path | None = None,
    policy_override: Path | None = None,
) -> dict[str, Any]:
    """Capture the host-contour state for a backup manifest (§15.3).

    The capture runs the boot reconciliation first: a head that exists
    after reconcile is exactly what the restore-side admission will see
    (an orphan unresolved record rebuilds its head, a head on a
    resolved record is dropped after the full replay check). A journal
    inconsistency is recorded as ``journal_problem`` — never hidden.
    """
    store = JournalStore(base)
    res = store.reconcile()
    head = store.read_head()

    transition_head: dict[str, Any] | None = dict(head) if head is not None else None
    policy_change_head: dict[str, Any] | None = read_policy_head(base)

    unresolved: list[dict[str, Any]] = []
    for rec in res.unresolved:
        unresolved.append(
            {
                "attempt_id": rec.attempt_id,
                "state": rec.state,
                "last_event_seq": rec.last_event_seq,
                "replayed_through_seq": rec.replayed_through_seq,
                "events_count": len(store.list_event_seqs(rec.attempt_id)),
            }
        )

    policy_files: list[dict[str, Any]] = []
    for path in (policy_baseline, policy_override):
        if path is not None and path.exists():
            policy_files.append({"path": str(path), "sha256": _file_sha256(path)})

    streams: list[dict[str, Any]] = []
    events_root = policy_events_dir(base)
    if events_root.exists():
        for change_dir in sorted(events_root.iterdir()):
            if not change_dir.is_dir():
                continue
            event_files = sorted(change_dir.glob("*.json"))
            last_kind = ""
            if event_files:
                try:
                    last_kind = str(json.loads(event_files[-1].read_bytes()).get("kind", ""))
                except (OSError, ValueError):
                    last_kind = ""
            streams.append(
                {
                    "change_id": change_dir.name,
                    "event_count": len(event_files),
                    "terminal": last_kind in POLICY_STREAM_TERMINAL_KINDS,
                }
            )

    host_ops_absent = (
        transition_head is None and policy_change_head is None and not unresolved
    )
    state: dict[str, Any] = {
        "schema_version": HOST_STATE_SCHEMA_VERSION,
        "transition_head": transition_head,
        "policy_change_head": policy_change_head,
        "unresolved_current_records": unresolved,
        "policy_files": policy_files,
        "policy_event_streams": streams,
        "host_ops_absent": host_ops_absent,
    }
    if res.problem is not None:
        state["journal_problem"] = res.problem
    return state


def build_artifact_inventory(rows: list[tuple[Any, str, int, str | None, str, str]]) -> dict[str, Any]:
    """The content-addressed inventory of ALL artifact registry rows."""
    return {
        "schema_version": 1,
        "artifacts": [
            {
                "id": str(row_id),
                "sha256": sha,
                "size": size,
                "mime": mime,
                "origin": origin,
                "trust_class": trust_class,
            }
            for (row_id, sha, size, mime, origin, trust_class) in rows
        ],
    }


async def create_backup(
    db: AsyncSession,
    artifact_store: ArtifactStore,
    base_dir: str | Path,
    *,
    retention_days: int = 30,
    policy_baseline: Path | None = None,
    policy_override: Path | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Write one backup manifest: recovery point + inventory + host state.

    The caller owns the transaction (audit + outbox join it). Returns
    the manifest summary.
    """
    base = Path(base_dir)
    moment = now or _now()

    recovery_point = str(
        (await db.execute(text("SELECT pg_current_wal_lsn()::text"))).scalar_one()
    )

    # the artifact inventory (content-addressed; every registry row)
    rows = [
        (
            r.id,
            r.sha256,
            int(r.size),
            r.mime,
            r.origin,
            r.trust_class,
        )
        for r in (
            await db.execute(
                select(
                    ORMArtifact.id,
                    ORMArtifact.sha256,
                    ORMArtifact.size,
                    ORMArtifact.mime,
                    ORMArtifact.origin,
                    ORMArtifact.trust_class,
                ).order_by(ORMArtifact.id)
            )
        ).all()
    ]
    inventory = build_artifact_inventory(rows)
    inventory_bytes = canonical_json_bytes(inventory)
    inventory_sha = sha256_hex(inventory_bytes)
    # the inventory document itself is a stored (content-addressed) object
    artifact_store.put(
        inventory_bytes,
        origin="backup_inventory",
        trust_class="local_trusted",
        mime="application/noezema+json",
    )
    inv_row = (
        (await db.execute(select(ORMArtifact).where(ORMArtifact.sha256 == inventory_sha)))
        .scalars()
        .first()
    )
    if inv_row is None:
        inv_row = ORMArtifact(
            id=uuid.uuid4(),
            sha256=inventory_sha,
            size=len(inventory_bytes),
            mime="application/noezema+json",
            origin="backup_inventory",
            trust_class="local_trusted",
        )
        db.add(inv_row)
        await db.flush()

    # the host-contour state
    host_state = build_host_state(
        base, policy_baseline=policy_baseline, policy_override=policy_override
    )

    manifest = ORMBackupManifest(
        id=uuid.uuid4(),
        database_recovery_point=recovery_point,
        artifact_inventory_hash=inventory_sha,
        artifact_inventory_artifact_id=inv_row.id,
        retention_until=moment + timedelta(days=retention_days),
        host_state=host_state,
    )
    db.add(manifest)
    await db.flush()

    from packages.domain.services.audit import AuditService

    await AuditService(db).record(
        AuditEventType.BACKUP_CREATED,
        payload={
            "backup_id": str(manifest.id),
            "database_recovery_point": recovery_point,
            "artifact_inventory_hash": inventory_sha,
            "artifact_count": len(inventory["artifacts"]),
            "host_ops_absent": host_state["host_ops_absent"],
            "retention_days": retention_days,
        },
        actor="operator",
        public_summary=(
            f"Backup created (recovery point {recovery_point}, "
            f"{len(inventory['artifacts'])} artifacts, "
            f"host ops {'absent' if host_state['host_ops_absent'] else 'present'})"
        ),
    )

    return {
        "backup_id": str(manifest.id),
        "database_recovery_point": recovery_point,
        "artifact_inventory_hash": inventory_sha,
        "artifact_inventory_artifact_id": str(inv_row.id),
        "retention_until": manifest.retention_until.isoformat()
        if manifest.retention_until
        else None,
        "host_state": host_state,
    }
