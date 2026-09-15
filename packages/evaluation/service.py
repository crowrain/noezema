"""T7.5 (stage 7, §22.2): the evaluation run service.

An evaluation run is a series of 50–100 eligible sessions on a FROZEN
config (model + config snapshot + rules), with the thresholds fixed
before the series («Evaluation thresholds фиксируются до серии»,
§16.3). The run records the frozen config, the session window, the
gate outcomes (passed/failed/insufficient_sample per gate, §22.2),
and the blind sample (seed, size).

Definitions (§22.2):
- **значимый claim (significant claim)** — current assessment E2+ with
  status ``supported | disputed | refuted`` or a claim used by an
  active question/claim;
- **eligible session** — scheduled/wake_now terminal session; operator
  abort is excluded, technical failure is included;
- **слепая выборка (blind sample)** — at least 50 claims or all,
  stratified by type/status, fixed seed, 95% confidence interval
  published;
- **достаточная type-specific выборка** — at least 20 evaluated claims
  of the required type/group.

Each gate has one of three outcomes, and they require different
reactions: ``passed`` (threshold met on a sufficient sample),
``failed`` (threshold not met on a sufficient sample),
``insufficient_sample`` (denominator < 20; nothing is known about the
quality). ``insufficient_sample`` is neither fail nor pass — it means
the measurement did not happen. Full v1 acceptance requires that no
gate is ``failed`` and no gate is left ``insufficient_sample``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


@dataclass(frozen=True)
class EvaluationRun:
    """One evaluation run (the §22.2 record)."""

    id: uuid.UUID
    label: str
    config_snapshot_id: uuid.UUID
    model_fingerprint: dict[str, Any]
    rules_version: str
    rules_hash: str
    thresholds: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None
    eligible_sessions: int
    completed_sessions: int
    gates: dict[str, Any]
    blind_sample_seed: int
    blind_sample_size: int
    outcome: str
    created_at: datetime


def _default_thresholds() -> dict[str, Any]:
    """The §22.2 start gates (fixed before the series)."""
    return {
        "new_supported_refuted_e2": 0.80,
        "external_temporal_e3": 1.00,
        "eligible_sessions_with_outcome": 0.60,
        "near_duplicate_questions": 0.15,
        "significant_claim_reuse": 0.25,
        "due_stale_time_sensitive": 0.20,
        "reassessment_slo_seconds": None,  # fixed before the run
        "current_pending_invalid_ancestor": 0,
        "high_severity_incidents": 0,
        "blind_provenance_path": 0.90,
        "blind_scope": 0.80,
    }


async def create_evaluation_run(
    db: AsyncSession,
    *,
    label: str,
    config_snapshot_id: uuid.UUID,
    model_fingerprint: dict[str, Any],
    rules_version: str,
    rules_hash: str,
    thresholds: dict[str, Any] | None = None,
    blind_sample_seed: int | None = None,
    blind_sample_size: int = 50,
    now: datetime | None = None,
) -> EvaluationRun:
    """Create a new evaluation run (frozen config + thresholds).

    The thresholds are fixed before the series — changing them later
    requires a new run with a new config version («SLO и пороги
    меняются только до нового evaluation run с новой config
    version», §22.2).
    """
    if thresholds is None:
        thresholds = _default_thresholds()
    if blind_sample_seed is None:
        import random

        blind_sample_seed = random.randint(0, 2**31 - 1)
    ts = now or datetime.now(UTC)
    run_id = uuid.uuid4()
    await db.execute(
        text(
            "INSERT INTO evaluation_runs ("
            "  id, label, config_snapshot_id, model_fingerprint, "
            "  rules_version, rules_hash, thresholds, started_at, "
            "  eligible_sessions, completed_sessions, gates, "
            "  blind_sample_seed, blind_sample_size, outcome"
            ") VALUES ("
            "  :id, :label, :cs, :mf, :rv, :rh, :th, :sa, "
            "  0, 0, '{}'::jsonb, :seed, :size, 'running'"
            ")"
        ),
        {
            "id": run_id,
            "label": label,
            "cs": config_snapshot_id,
            "mf": _jsonb(model_fingerprint),
            "rv": rules_version,
            "rh": rules_hash,
            "th": _jsonb(thresholds),
            "sa": ts,
            "seed": blind_sample_seed,
            "size": blind_sample_size,
        },
    )
    return await get_evaluation_run(db, run_id)  # type: ignore[return-value]


async def start_evaluation_run(
    db: AsyncSession, run_id: uuid.UUID
) -> EvaluationRun:
    """Mark the run as started (idempotent)."""
    await db.execute(
        text(
            "UPDATE evaluation_runs SET outcome = 'running' "
            "WHERE id = :id AND outcome = 'running'"
        ),
        {"id": run_id},
    )
    return await get_evaluation_run(db, run_id)  # type: ignore[return-value]


async def finish_evaluation_run(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    gates: dict[str, Any],
    eligible_sessions: int | None = None,
    completed_sessions: int | None = None,
    now: datetime | None = None,
) -> EvaluationRun:
    """Finish the run: compute the overall outcome from the gates.

    The overall outcome is:
    - ``passed`` — no gate is ``failed`` and no gate is
      ``insufficient_sample``;
    - ``failed`` — at least one gate is ``failed``;
    - ``insufficient_sample`` — no gate is ``failed`` but at least one
      is ``insufficient_sample``.
    """
    ts = now or datetime.now(UTC)
    if eligible_sessions is None:
        eligible_sessions = 0
    if completed_sessions is None:
        completed_sessions = 0
    outcomes = [
        v.get("outcome") for v in gates.values() if isinstance(v, dict)
    ]
    if "failed" in outcomes:
        overall = "failed"
    elif "insufficient_sample" in outcomes:
        overall = "insufficient_sample"
    else:
        overall = "passed"
    await db.execute(
        text(
            "UPDATE evaluation_runs SET "
            "  finished_at = :fa, outcome = :o, "
            "  gates = CAST(:g AS jsonb), "
            "  eligible_sessions = :es, completed_sessions = :cs "
            "WHERE id = :id"
        ),
        {
            "fa": ts,
            "o": overall,
            "g": _jsonb(gates),
            "es": eligible_sessions,
            "cs": completed_sessions,
            "id": run_id,
        },
    )
    return await get_evaluation_run(db, run_id)  # type: ignore[return-value]


async def get_evaluation_run(
    db: AsyncSession, run_id: uuid.UUID
) -> EvaluationRun | None:
    """Fetch one evaluation run."""
    row = (
        await db.execute(
            text(
                "SELECT id, label, config_snapshot_id, model_fingerprint, "
                "rules_version, rules_hash, thresholds, started_at, "
                "finished_at, eligible_sessions, completed_sessions, "
                "gates, blind_sample_seed, blind_sample_size, outcome, "
                "created_at "
                "FROM evaluation_runs WHERE id = :id"
            ),
            {"id": run_id},
        )
    ).first()
    if row is None:
        return None
    m = row._mapping
    return EvaluationRun(
        id=m["id"],
        label=m["label"],
        config_snapshot_id=m["config_snapshot_id"],
        model_fingerprint=m["model_fingerprint"] or {},
        rules_version=m["rules_version"],
        rules_hash=m["rules_hash"],
        thresholds=m["thresholds"] or {},
        started_at=m["started_at"],
        finished_at=m["finished_at"],
        eligible_sessions=m["eligible_sessions"],
        completed_sessions=m["completed_sessions"],
        gates=m["gates"] or {},
        blind_sample_seed=m["blind_sample_seed"],
        blind_sample_size=m["blind_sample_size"],
        outcome=m["outcome"],
        created_at=m["created_at"],
    )


async def list_evaluation_runs(
    db: AsyncSession, *, limit: int = 50
) -> list[EvaluationRun]:
    """List evaluation runs (newest first)."""
    rows = (
        await db.execute(
            text(
                "SELECT id FROM evaluation_runs "
                "ORDER BY started_at DESC, created_at DESC LIMIT :n"
            ),
            {"n": limit},
        )
    ).all()
    runs: list[EvaluationRun] = []
    for r in rows:
        run = await get_evaluation_run(db, r[0])
        if run is not None:
            runs.append(run)
    return runs


def _jsonb(obj: Any) -> str:
    import json

    return json.dumps(obj)
