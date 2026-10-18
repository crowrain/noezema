"""T7.2 (stage 7, §15.3, §22.1 item 12): the restore drill.

The drill picks a RANDOM backup point inside the retention window and
verifies every referenced hash:

- the artifact inventory document (re-fetched from the store, re-hashed
  against ``artifact_inventory_hash``), then every artifact in it
  (re-fetched, re-hashed, size-checked) and the DB registry rows they
  map to (no id→sha drift);
- every host policy file recorded in the manifest (re-read, re-hashed);
- the host contour: a live re-capture must agree with the manifest
  (heads, unresolved records), and the boot reconciliation + admission
  run BEFORE the runtime would start: an active head (or an orphan
  nonterminal stream) does not fail the drill — it must be exactly the
  degraded state the manifest predicted (§15.3: the web opens degraded
  and passes the resume/policy classification).

A passed drill stamps ``verified_at`` on the manifest and records the
audit (``backup_restore_drill``) in the same transaction. A failed
drill stamps nothing and records the problems in the audit payload.

Clock (T7.46b): the drill runs on the INJECTED clock — ``now`` (or the
host clock when None). ``retention_until`` is host-stamped by
``create_backup`` (the same injected-clock convention), so the
retention window is evaluated on that clock: selection, the
``verified_at`` stamp and the drill are ONE operation with ONE clock.
(Evaluating retention on the DB ``now()`` while stamping ``verified_at``
with the injected clock made the drill's answer depend on the wall
clock at run time — a time bomb: the same manifest was retained at
backup time and expired at drill time.)
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from hostctl.admission import admission_check
from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.backup.service import build_host_state
from packages.domain.models.enums import AuditEventType
from packages.domain.models.memory import ORMBackupManifest
from packages.domain.services.audit import AuditService


class RestoreDrillError(RuntimeError):
    """The drill could not run (no backup in the retention window)."""


@dataclass(frozen=True)
class DrillReport:
    backup_id: str
    database_recovery_point: str
    inventory_checked: int
    inventory_ok: int
    policy_files_checked: int
    policy_files_ok: int
    host_consistent: bool
    admission: dict[str, Any]
    problems: list[str] = field(default_factory=list)

    @property
    def outcome(self) -> str:
        return "passed" if not self.problems else "failed"


def _file_sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def admission_problems(admission: dict[str, Any]) -> str:
    """The admission verdict as a compact summary string."""
    problems = [str(p) for p in admission.get("problems", [])]
    return "ok" if not problems else ";".join(problems)


async def _pick_retained_backup(
    db: AsyncSession, rng: random.Random, now: datetime
) -> ORMBackupManifest:
    """A random backup point inside the retention window, evaluated on
    the drill's clock (T7.46b — the same clock that stamps
    ``verified_at``; the DB ``now()`` ignored the injected clock)."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id FROM backup_manifests "
                    "WHERE (retention_until IS NULL OR retention_until > :drill_now) "
                    "ORDER BY created_at, id"
                ),
                {"drill_now": now},
            )
        )
        .all()
    )
    if not rows:
        raise RestoreDrillError("no backup point inside the retention window")
    backup_id = rng.choice([r.id for r in rows])
    manifest = (
        (
            await db.execute(
                select(ORMBackupManifest).where(ORMBackupManifest.id == backup_id)
            )
        )
        .scalars()
        .one()
    )
    return manifest


def _verify_inventory(
    manifest: ORMBackupManifest,
    artifact_store: ArtifactStore,
) -> tuple[int, int, list[str], dict[str, Any] | None]:
    """Verify the inventory document + every artifact it references.

    Returns (checked, ok, problems, inventory_dict).
    """
    problems: list[str] = []
    if manifest.host_state is None:
        problems.append("manifest_has_no_host_state (legacy row — fail-closed)")
    if manifest.artifact_inventory_hash is None or not manifest.artifact_inventory_hash:
        problems.append("missing_artifact_inventory_hash")
        return 0, 0, problems, None
    try:
        data = artifact_store.get(manifest.artifact_inventory_hash)
    except ArtifactStoreError:
        problems.append(f"inventory_document_missing:{manifest.artifact_inventory_hash[:12]}")
        return 0, 0, problems, None
    if hashlib.sha256(data).hexdigest() != manifest.artifact_inventory_hash:
        problems.append(f"inventory_document_hash_mismatch:{manifest.artifact_inventory_hash[:12]}")
        return 0, 0, problems, None
    try:
        inventory: dict[str, Any] = json.loads(data)
    except ValueError:
        problems.append(f"inventory_document_not_json:{manifest.artifact_inventory_hash[:12]}")
        return 0, 0, problems, None

    checked = 0
    ok = 0
    for entry in inventory.get("artifacts", []):
        checked += 1
        sha = str(entry.get("sha256", ""))
        if not sha:
            problems.append("inventory_entry_without_sha")
            continue
        # store-resident objects (the content-addressed transport holds
        # them — research proxy content, backup inventories) are
        # re-fetched and re-hashed; registry-only objects (e.g. session
        # observation artifacts whose content lives in the audit trail)
        # are verified through their registry row below
        if artifact_store.exists(sha):
            blob = artifact_store.get(sha)
            if hashlib.sha256(blob).hexdigest() != sha:
                problems.append(f"artifact_hash_mismatch:{sha[:12]}")
                continue
            expected_size = entry.get("size")
            if expected_size is not None and len(blob) != int(expected_size):
                problems.append(f"artifact_size_mismatch:{sha[:12]}")
                continue
        ok += 1
    return checked, ok, problems, inventory


async def _verify_db_registry(
    db: AsyncSession, inventory: dict[str, Any], problems: list[str]
) -> None:
    """No id→sha drift: every inventory id still maps to its sha."""
    entries = inventory.get("artifacts", [])
    if not entries:
        return
    ids = [e["id"] for e in entries if "id" in e]
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id::text, sha256 FROM artifacts WHERE id::text = ANY(:ids)"
                ),
                {"ids": ids},
            )
        )
        .all()
    )
    by_id = {r[0]: r[1] for r in rows}
    for entry in entries:
        row_id = str(entry.get("id", ""))
        sha = str(entry.get("sha256", ""))
        if row_id and row_id not in by_id:
            problems.append(f"registry_row_missing:{row_id[:8]}")
        elif row_id and by_id[row_id] != sha:
            problems.append(f"registry_drift:{row_id[:8]}")


def _verify_policy_files(
    host_state: dict[str, Any] | None,
) -> tuple[int, int, list[str]]:
    """Re-read + re-hash every policy file the manifest recorded."""
    problems: list[str] = []
    checked = 0
    ok = 0
    for entry in (host_state or {}).get("policy_files", []):
        checked += 1
        path = Path(str(entry.get("path", "")))
        expected = str(entry.get("sha256", ""))
        if not path.exists():
            problems.append(f"policy_file_missing:{path}")
            continue
        if _file_sha256(path) != expected:
            problems.append(f"policy_file_hash_mismatch:{path}")
            continue
        ok += 1
    return checked, ok, problems


def _host_consistency(
    manifest: ORMBackupManifest,
    live: dict[str, Any],
    problems: list[str],
) -> bool:
    """The live host contour must agree with the manifest's prediction."""
    recorded = manifest.host_state
    consistent = True
    if recorded is None:
        # nothing was predicted (legacy row) — the admission report is
        # the only ground truth, and any active head is a surprise
        return bool(live.get("host_ops_absent", False))
    for key, label in (
        ("transition_head", "transition_head"),
        ("policy_change_head", "policy_change_head"),
    ):
        rec = recorded.get(key)
        live_v = live.get(key)
        if (rec is None) != (live_v is None):
            problems.append(f"host_mismatch:{label}_presence")
            consistent = False
            continue
        if rec is not None and live_v is not None:
            rec_id = rec.get("attempt_id") or rec.get("change_id")
            live_id = live_v.get("attempt_id") or live_v.get("change_id")
            if rec_id != live_id:
                problems.append(f"host_mismatch:{label}_identity")
                consistent = False
    rec_records = {
        r.get("attempt_id") for r in recorded.get("unresolved_current_records", [])
    }
    live_records = {
        r.get("attempt_id") for r in live.get("unresolved_current_records", [])
    }
    if rec_records != live_records:
        problems.append("host_mismatch:unresolved_records")
        consistent = False
    if recorded.get("journal_problem") is not None and live.get("journal_problem") != recorded.get(
        "journal_problem"
    ):
        problems.append("host_mismatch:journal_problem")
        consistent = False
    return consistent


def _admission_matches(
    manifest: ORMBackupManifest, admission: dict[str, Any], problems: list[str]
) -> None:
    """The admission verdict must match the manifest's prediction.

    An active head recorded in the manifest is the EXPECTED degraded
    state (§15.3), not a drill failure; a surprise active head (or a
    missing one) is.
    """
    recorded = manifest.host_state or {}
    has_transition = recorded.get("transition_head") is not None
    has_policy = recorded.get("policy_change_head") is not None
    unresolved = bool(recorded.get("unresolved_current_records"))
    problems_ = set(admission.get("problems", []))
    if (has_transition or unresolved) and "unresolved_host_transition" not in problems_:
        problems.append("admission_mismatch:transition_not_flagged")
    if has_policy and "host_policy_change_in_progress" not in problems_:
        problems.append("admission_mismatch:policy_change_not_flagged")
    if not (has_transition or unresolved) and "unresolved_host_transition" in problems_:
        problems.append("admission_mismatch:unexpected_transition")
    if not has_policy and "host_policy_change_in_progress" in problems_:
        problems.append("admission_mismatch:unexpected_policy_change")


async def run_restore_drill(
    db: AsyncSession,
    artifact_store: ArtifactStore,
    base_dir: str | Path,
    *,
    policy_baseline: Path | None = None,
    policy_override: Path | None = None,
    rng: random.Random | None = None,
    now: datetime | None = None,
) -> DrillReport:
    """Run one restore drill on a random retained backup point.

    The caller owns the transaction: the audit joins it, and
    ``verified_at`` is stamped only on a passed drill.
    """
    base = Path(base_dir)
    roll = rng or random.Random()
    # T7.46b: ONE clock for the whole drill — selection, the verified_at
    # stamp, the audit. The production CLI passes no ``now``: the host
    # clock (datetime.now(UTC)) is used, the real-time semantics kept.
    moment = now or datetime.now(UTC)
    manifest = await _pick_retained_backup(db, roll, moment)
    problems: list[str] = []

    # 1. the artifact inventory + every referenced hash
    checked, ok, inv_problems, inventory = _verify_inventory(manifest, artifact_store)
    problems.extend(inv_problems)
    if inventory is not None:
        await _verify_db_registry(db, inventory, problems)

    # 2. the host policy files
    pf_checked, pf_ok, pf_problems = _verify_policy_files(manifest.host_state)
    problems.extend(pf_problems)

    # 3. boot reconciliation + admission BEFORE the runtime would start
    live = build_host_state(
        base, policy_baseline=policy_baseline, policy_override=policy_override
    )
    host_consistent = _host_consistency(manifest, live, problems)
    report = await admission_check(
        db,
        host_lib_base=base,
        policy_override=policy_override,
        policy_baseline=policy_baseline,
    )
    admission = {"ok": report.ok, "problems": list(report.problems)}
    _admission_matches(manifest, admission, problems)

    drill = DrillReport(
        backup_id=str(manifest.id),
        database_recovery_point=manifest.database_recovery_point,
        inventory_checked=checked,
        inventory_ok=ok,
        policy_files_checked=pf_checked,
        policy_files_ok=pf_ok,
        host_consistent=host_consistent,
        admission=admission,
        problems=problems,
    )

    if drill.outcome == "passed":
        manifest.verified_at = moment
    await AuditService(db).record(
        AuditEventType.BACKUP_RESTORE_DRILL,
        payload={
            "backup_id": drill.backup_id,
            "database_recovery_point": drill.database_recovery_point,
            "outcome": drill.outcome,
            "inventory_checked": checked,
            "inventory_ok": ok,
            "policy_files_checked": pf_checked,
            "policy_files_ok": pf_ok,
            "host_consistent": host_consistent,
            "admission": admission,
            "problems": problems,
        },
        actor="operator",
        public_summary=(
            f"Restore drill {drill.outcome} on backup {drill.backup_id[:8]} "
            f"(recovery point {drill.database_recovery_point}, "
            f"inventory {ok}/{checked}, "
            f"admission {admission_problems(admission)}"
        ),
    )
    return drill
