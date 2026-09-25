"""Scenario (DB): T7.2 backup/PITR (§15.3, §22.1 item 12).

Covers: backup manifest creation (DB recovery point + content-addressed
artifact inventory + the host-contour state with the explicit
host_ops_absent evidence), the restore drill (random retained point,
every referenced hash re-verified, boot reconciliation + admission
before the runtime would start, verified_at stamp + audit), and the
failure paths (corrupted store object, expired retention, surprise
active head).

T7.46b (the time bomb): the drill must run on the INJECTED clock — the
same clock ``create_backup`` stamped ``retention_until`` with — not on
the DB ``now()`` (the pre-fix drill compared host-stamped
``retention_until`` against the wall clock, so a manifest that was
retained at backup time expired "by itself" 10/20 days later and the
drill tests failed on every run after 2026-09-25 12:00Z). The tests
anchor on the FIXED reference date ``NOW`` and parameterize the drill
clock via ``drill_now`` (+0/+1y/+5y — via a parameter, not a wall-clock
change), proving the suite is independent of the day it runs on while
keeping the expired-window semantics testable (see
test_restore_drill_no_retained_backup).
"""

from __future__ import annotations

import hashlib
import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from hostctl.journal import STATE_CHECKING, JournalStore, TransitionRecord
from hostctl.policy_change import PolicyChange
from packages.artifacts.store import FilesystemArtifactStore
from packages.backup.restore import RestoreDrillError, run_restore_drill
from packages.backup.service import create_backup
from packages.domain.canonical import canonical_sha256
from packages.domain.db.uow import transaction

pytestmark = [pytest.mark.scenario]

# the module's FIXED reference date (NOT the run date, T7.46b): every
# backup is stamped from it and the drill clock is a shift of it, so no
# test in this file depends on the day it runs on.
NOW = datetime(2026, 9, 15, 12, 0, 0, tzinfo=UTC)


class _LastChoiceRng:
    """Deterministic stand-in: always picks the newest candidate."""

    def choice(self, seq: list[Any]) -> Any:
        return seq[-1]


@pytest.fixture(params=[0, 1, 5])
def drill_now(request: pytest.FixtureRequest) -> datetime:
    """T7.46b: the drill's injected clock — the fixed reference NOW
    shifted by 0/1/5 YEARS (a parameter, not a wall-clock change). The
    drill must select on this clock (restore.py: the same clock stamps
    ``verified_at``); with it, a 10-day retention window anchored at the
    shifted now is still open for the drill no matter when the suite
    runs — the +1y/+5y shifts prove the tests are independent of the
    run date (pre-fix, the drill compared retention_until against the
    DB ``now()``, so the 10-day windows expired on 2026-09-25 12:00Z
    and the 20-day ones on 2026-10-05 — the time bomb)."""
    return NOW.replace(year=NOW.year + request.param)


async def _scalar(
    engine: AsyncEngine, sql: str, params: dict[str, Any] | None = None
) -> Any:
    factory = async_sessionmaker(engine)
    async with factory() as db, db.begin():
        res = await db.execute(text(sql), params or {})
        return res.first() if res.returns_rows else None


def _seed_transition(base: Path, attempt_id: str) -> None:
    """An active (nonterminal) host transition: record + event + head."""
    store = JournalStore(base)
    rec = TransitionRecord(
        attempt_id=attempt_id,
        operation="offline_rules",
        candidate_snapshot_id=None,
        base_snapshot_id=None,
        observed_pointer_tuple={"active": "x"},
        state=STATE_CHECKING,
        last_event_seq=1,
    )
    store.write_record(rec)
    store.write_event(attempt_id, 1, {"kind": "initial"})
    store.write_head(rec, initial_event_sha256="e1", creation_boot_id="boot-1")


def _seed_policy_change(base: Path, change_id: str) -> None:
    """An active host-policy change: prepared event + head."""
    change = PolicyChange(base=base, change_id=change_id)
    prepared = change.publish(
        "prepared",
        old_hash="a" * 64,
        proposed_hash="b" * 64,
        actor="operator",
        reason="drill fixture",
        source_kind="override",
    )
    change.write_head("a" * 64, "b" * 64, canonical_sha256(prepared))


@pytest.mark.asyncio
async def test_backup_captures_recovery_point_inventory_and_host_state(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    policy_file = tmp_path / "policy.toml"
    policy_file.write_text('resume_retry_jitter = 0.0\n')

    factory = async_sessionmaker(engine, expire_on_commit=False)
    # two store-resident artifacts
    for tag in ("p1", "p2"):
        data = f"artifact-content-{tag}".encode()
        sha = store.put(data, origin="research_proxy", trust_class="untrusted", mime="text/plain")
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class) "
                    "VALUES (:id, :s, :n, 'text/plain', 'research_proxy', 'untrusted')"
                ),
                {"id": uuid.uuid4(), "s": sha, "n": len(data)},
            )
    _seed_transition(base, "attempt-1")

    async with factory() as db, transaction(db):
        result = await create_backup(
            db, store, base, retention_days=14, policy_override=policy_file, now=NOW
        )

    assert result["database_recovery_point"].count("/") == 1  # a WAL LSN
    assert result["artifact_inventory_hash"]
    # the inventory document is a stored artifact + a registry row
    inv_bytes = store.get(result["artifact_inventory_hash"])
    assert hashlib.sha256(inv_bytes).hexdigest() == result["artifact_inventory_hash"]
    inventory = json.loads(inv_bytes)
    shas = {e["sha256"] for e in inventory["artifacts"]}
    assert len(shas) == 2
    assert _sha("p1") in shas and _sha("p2") in shas
    for entry in inventory["artifacts"]:
        assert entry["origin"] == "research_proxy"
        assert store.exists(entry["sha256"])
    row = await _scalar(
        engine,
        "SELECT count(*)::int FROM artifacts WHERE id = :id",
        {"id": result["artifact_inventory_artifact_id"]},
    )
    assert row[0] == 1

    # the host-contour state
    hs = result["host_state"]
    assert hs["schema_version"] == 1
    assert hs["transition_head"]["attempt_id"] == "attempt-1"
    assert hs["host_ops_absent"] is False
    assert hs["unresolved_current_records"][0]["attempt_id"] == "attempt-1"
    assert hs["unresolved_current_records"][0]["events_count"] == 1
    assert hs["policy_files"] == [
        {
            "path": str(policy_file),
            "sha256": hashlib.sha256(policy_file.read_bytes()).hexdigest(),
        }
    ]
    assert hs["policy_event_streams"] == []

    # the manifest row + the audit in the same transaction
    m = await _scalar(
        engine,
        "SELECT database_recovery_point, artifact_inventory_hash, host_state, "
        "retention_until, verified_at FROM backup_manifests WHERE id = :id",
        {"id": result["backup_id"]},
    )
    assert m is not None
    assert m[0] == result["database_recovery_point"]
    assert m[1] == result["artifact_inventory_hash"]
    assert m[2]["transition_head"]["attempt_id"] == "attempt-1"
    assert m[3] == NOW + timedelta(days=14)
    assert m[4] is None
    audit = await _scalar(
        engine,
        "SELECT type, payload->>'host_ops_absent' FROM audit_events "
        "WHERE type = 'backup_created'",
    )
    assert audit is not None
    assert audit[1] == "false"


def _sha(tag: str) -> str:
    return hashlib.sha256(f"artifact-content-{tag}".encode()).hexdigest()


@pytest.mark.asyncio
async def test_backup_explicit_absence_of_host_ops(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db, transaction(db):
        result = await create_backup(db, store, base, retention_days=1, now=NOW)

    hs = result["host_state"]
    assert hs["host_ops_absent"] is True
    assert hs["transition_head"] is None
    assert hs["policy_change_head"] is None
    assert hs["unresolved_current_records"] == []
    assert hs["policy_files"] == []


@pytest.mark.asyncio
async def test_backup_db_check_rejects_inconsistent_host_state(
    migrated_db: tuple[str, AsyncEngine],
) -> None:
    """The CHECK ties host_ops_absent to the actual head presence."""
    _scratch_url, engine = migrated_db
    factory = async_sessionmaker(engine, expire_on_commit=False)
    bad = {
        "schema_version": 1,
        "transition_head": {"attempt_id": "x"},
        "policy_change_head": None,
        "unresolved_current_records": [],
        "policy_files": [],
        "policy_event_streams": [],
        "host_ops_absent": True,  # lies: a head IS present
    }
    with pytest.raises(IntegrityError, match="host_state_shape_check"):
        async with factory() as db, db.begin():
            await db.execute(
                text(
                    "INSERT INTO backup_manifests (id, database_recovery_point, "
                    "artifact_inventory_hash, host_state) "
                    "VALUES (:id, '0/1', :h, :s)"
                ),
                {"id": uuid.uuid4(), "h": "f" * 64, "s": json.dumps(bad)},
            )


@pytest.mark.asyncio
async def test_restore_drill_random_retained_point_and_verification(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    data = b"artifact-content-d1"
    sha = store.put(data, origin="research_proxy", trust_class="untrusted", mime="text/plain")
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class) "
                "VALUES (:id, :s, :n, 'text/plain', 'research_proxy', 'untrusted')"
            ),
            {"id": uuid.uuid4(), "s": sha, "n": len(data)},
        )

    # an EXPIRED backup (retention window passed) and two retained ones
    # — all relative to the drill's clock (T7.46b)
    async with factory() as db, transaction(db):
        await create_backup(db, store, base, retention_days=1, now=drill_now - timedelta(days=40))
    async with factory() as db, transaction(db):
        b1 = await create_backup(db, store, base, retention_days=10, now=drill_now)
    async with factory() as db, transaction(db):
        b2 = await create_backup(db, store, base, retention_days=20, now=drill_now)
    retained = {b1["backup_id"], b2["backup_id"]}

    seen: list[str] = []
    # every create_backup also stores its inventory document — the
    # third backup sees three inventory artifacts in the registry
    for seed in range(20):
        rng = random.Random(seed)
        async with factory() as db, transaction(db):
            drill = await run_restore_drill(db, store, base, rng=rng, now=drill_now)
        seen.append(drill.backup_id)
        assert drill.outcome == "passed"
        assert drill.backup_id in retained  # never the expired one
        assert drill.inventory_checked >= 1
        assert drill.inventory_ok == drill.inventory_checked
        assert drill.admission["ok"] is True
        assert drill.problems == []
        row = await _scalar(
            engine, "SELECT verified_at IS NOT NULL FROM backup_manifests WHERE id = :id",
            {"id": drill.backup_id},
        )
        assert row[0] is True
    assert len(set(seen)) >= 2  # the choice is actually random
    audit = await _scalar(
        engine,
        "SELECT count(*)::int FROM audit_events WHERE type = 'backup_restore_drill' "
        "AND payload->>'outcome' = 'passed'",
    )
    assert audit[0] == 20


@pytest.mark.asyncio
async def test_restore_drill_detects_corrupted_object(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    data = b"artifact-content-corrupt"
    sha = store.put(data, origin="research_proxy", trust_class="untrusted", mime="text/plain")
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class) "
                "VALUES (:id, :s, :n, 'text/plain', 'research_proxy', 'untrusted')"
            ),
            {"id": uuid.uuid4(), "s": sha, "n": len(data)},
        )
    async with factory() as db, transaction(db):
        b = await create_backup(db, store, base, retention_days=10, now=drill_now)

    # corrupt the store object (the content no longer matches its hash)
    path = store.root / sha[:2] / sha
    path.write_bytes(b"corrupted")

    async with factory() as db, transaction(db):
        drill = await run_restore_drill(db, store, base, rng=random.Random(1), now=drill_now)
    assert drill.outcome == "failed"
    assert drill.backup_id == b["backup_id"]
    assert any(p.startswith("artifact_hash_mismatch") for p in drill.problems)
    assert drill.inventory_ok == 0
    row = await _scalar(
        engine, "SELECT verified_at IS NULL FROM backup_manifests WHERE id = :id",
        {"id": b["backup_id"]},
    )
    assert row[0] is True  # a failed drill stamps nothing
    audit = await _scalar(
        engine,
        "SELECT payload->>'outcome' FROM audit_events WHERE type = 'backup_restore_drill'",
    )
    assert audit[0] == "failed"


@pytest.mark.asyncio
async def test_restore_drill_detects_registry_drift(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    data = b"artifact-content-drift"
    sha = store.put(data, origin="research_proxy", trust_class="untrusted", mime="text/plain")
    art_id = uuid.uuid4()
    async with factory() as db, db.begin():
        await db.execute(
            text(
                "INSERT INTO artifacts (id, sha256, size, mime, origin, trust_class) "
                "VALUES (:id, :s, :n, 'text/plain', 'research_proxy', 'untrusted')"
            ),
            {"id": art_id, "s": sha, "n": len(data)},
        )
    async with factory() as db, transaction(db):
        await create_backup(db, store, base, retention_days=10, now=drill_now)

    # the registry row now points to a different hash (drift)
    async with factory() as db, db.begin():
        await db.execute(
            text("UPDATE artifacts SET sha256 = :s WHERE id = :id"),
            {"s": "e" * 64, "id": art_id},
        )

    async with factory() as db, transaction(db):
        drill = await run_restore_drill(db, store, base, rng=random.Random(1), now=drill_now)
    assert drill.outcome == "failed"
    assert any(p.startswith("registry_drift") for p in drill.problems)


@pytest.mark.asyncio
async def test_restore_drill_boot_reconciliation_degraded(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    """An active transition head recorded in the manifest is the EXPECTED
    degraded state: admission flags it, the drill passes (the prediction
    matches). A surprise head the manifest did not record fails it."""
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # a clean backup
    async with factory() as db, transaction(db):
        await create_backup(db, store, base, retention_days=10, now=drill_now)

    # now a transition starts (after the backup): a surprise for that
    # manifest
    _seed_transition(base, "attempt-late")

    async with factory() as db, transaction(db):
        drill = await run_restore_drill(db, store, base, rng=random.Random(1), now=drill_now)
    assert drill.outcome == "failed"
    assert any("host_mismatch" in p or "admission_mismatch" in p for p in drill.problems)

    # a backup taken WITH the active transition: the degraded state is
    # exactly what the manifest predicted
    async with factory() as db, transaction(db):
        b2 = await create_backup(db, store, base, retention_days=10, now=drill_now)
    async with factory() as db, transaction(db):
        drill2 = await run_restore_drill(
            db, store, base, rng=_LastChoiceRng(), now=drill_now
        )
    assert drill2.outcome == "passed"
    assert drill2.backup_id == b2["backup_id"]
    assert drill2.admission["ok"] is False
    assert "unresolved_host_transition" in drill2.admission["problems"]
    assert drill2.problems == []


@pytest.mark.asyncio
async def test_restore_drill_no_retained_backup(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    # the ONLY backup is outside its retention window AT THE DRILL'S
    # CLOCK (T7.46b — the expired-window semantics stay testable on
    # any run date, because both the window and the drill's clock are
    # anchored at the same shifted now)
    async with factory() as db, transaction(db):
        await create_backup(db, store, base, retention_days=1, now=drill_now - timedelta(days=40))

    async with factory() as db, transaction(db):
        with pytest.raises(RestoreDrillError):
            await run_restore_drill(db, store, base, rng=random.Random(1), now=drill_now)


@pytest.mark.asyncio
async def test_restore_drill_selects_on_injected_clock_not_wall_clock(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path
) -> None:
    """T7.46b regression guard: the drill must select on the INJECTED
    clock. The backup's retention window (NOW + 10d = 2026-09-25
    12:00Z) is already closed on the WALL clock (any run date after
    2026-09-25 12:00Z) but still open at the injected now=NOW — it must
    be selected. If the selection regresses to the DB ``now()``, the
    drill raises RestoreDrillError on every such run date (this is the
    exact time bomb that failed the suite from 2026-09-25)."""
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async with factory() as db, transaction(db):
        b = await create_backup(db, store, base, retention_days=10, now=NOW)

    async with factory() as db, transaction(db):
        drill = await run_restore_drill(db, store, base, rng=_LastChoiceRng(), now=NOW)
    assert drill.outcome == "passed"
    assert drill.backup_id == b["backup_id"]
    # and the stamp uses the SAME injected clock
    row = await _scalar(
        engine, "SELECT verified_at FROM backup_manifests WHERE id = :id",
        {"id": b["backup_id"]},
    )
    assert row is not None
    assert row[0] == NOW


@pytest.mark.asyncio
async def test_restore_drill_policy_change_head_predicted(
    migrated_db: tuple[str, AsyncEngine], tmp_path: Path, drill_now: datetime
) -> None:
    """An active host-policy-change head recorded in the manifest is the
    expected degraded state: admission flags it, the drill passes, and
    the policy event stream is inventoried with its terminal status."""
    _scratch_url, engine = migrated_db
    store = FilesystemArtifactStore(tmp_path / "artifacts")
    base = tmp_path / "host"
    base.mkdir()
    factory = async_sessionmaker(engine, expire_on_commit=False)

    _seed_policy_change(base, "change-1")
    async with factory() as db, transaction(db):
        result = await create_backup(db, store, base, retention_days=10, now=drill_now)

    hs = result["host_state"]
    assert hs["policy_change_head"]["change_id"] == "change-1"
    assert hs["host_ops_absent"] is False
    assert hs["policy_event_streams"] == [
        {"change_id": "change-1", "event_count": 1, "terminal": False}
    ]

    async with factory() as db, transaction(db):
        drill = await run_restore_drill(db, store, base, rng=_LastChoiceRng(), now=drill_now)
    assert drill.outcome == "passed"
    assert drill.admission["ok"] is False
    assert "host_policy_change_in_progress" in drill.admission["problems"]
    assert drill.problems == []
