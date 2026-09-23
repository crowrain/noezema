"""T7.7 (§22.2): gate computation for an evaluation run.

Computes all 11 §22.2 gates from the domain data of the run's session
window (sessions with ``started_at >= run.started_at``). Each ratio
gate returns ``{"outcome", "numerator", "denominator", "ci95":
{"low", "high"}, ...}`` where ``ci95`` is the 95% Wilson score
interval for the measured share («95% доверительный интервал
публикуется», §22.2; reporting only — the outcome is decided by the
ratio alone) and ``outcome`` is one of the three §22.2 outcomes:

- ``passed`` — the threshold is met on a sufficient sample;
- ``failed`` — the threshold is not met on a sufficient sample;
- ``insufficient_sample`` — the denominator is below the minimum
  sample (20, «при N<20 gate получает insufficient_sample», §22.2):
  the measurement did not happen.

Gate definitions (fixed here; see ADR-0005 for the operational run):

1. ``new_supported_refuted_e2`` — of the NEW claims (created in the
   window) whose current head is ``supported | refuted``, the share
   with effective grade E2+ (threshold ≥0.80, direction: at least).
2. ``external_temporal_e3`` — of the NEW claims of type
   ``external_fact | temporal_fact`` with current head
   ``supported | refuted``, the share with grade E3 (threshold 1.00).
3. ``eligible_sessions_with_outcome`` — of the terminal sessions of
   the window (operator abort excluded, technical failure included),
   the share with an outcome: a new evidence row, a claim revision,
   or a question moved out of ``candidate`` (threshold ≥0.60).
4. ``near_duplicate_questions`` — of the questions selected by the
   window's sessions, the share flagged by the repetition guard
   (audit ``repeat_cycle_detected``, threshold ≤0.15, direction:
   at most).
5. ``significant_claim_reuse`` — of the significant claims (current
   head E2+ ``supported | disputed | refuted`` at the end of the
   run), the share reused/re-verified: referenced by at least two
   distinct sessions via evidence / revisions / dependencies
   (threshold ≥0.25).
6. ``due_stale_time_sensitive`` — of the current
   ``temporal_fact`` claims that CAN become due (``reverify_after``
   NOT NULL), the share whose freshness, evaluated at the
   gate-computation instant by the §8.6/T3.7 rule
   (``packages.memory.freshness.freshness_status`` over
   ``reverify_after``, NOT the stored ``claims.freshness_status``
   column), is ``due`` (``now >= reverify_after`` — T7.27, ADR-0014:
   the gate measures the real state at computation time and does not
   depend on whether a background reassessment/activation flip ever
   ran). T7.32 (ADR-0017): the denominator counts ONLY claims with a
   deadline — a NULL ``reverify_after`` is "no deadline by
   construction" (a claim about a fixed point: explicit question date
   or a dateless question with the model's as_of); such a claim can
   never be due, and counting it would dilute the ratio with
   composition instead of measuring freshness. Threshold <0.20,
   direction: strictly below (``ratio < threshold`` — the spec
   «<20%» is strict; T7.28, ADR-0015: exactly 20% is ``failed``).
7. ``reassessment_slo`` — of the completed reassessment jobs, the
   share that completed within the fixed wall-clock SLO (``thresholds.
   reassessment_slo_seconds``; blocked jobs have an alert and are
   NOT counted as runnable backlog, §22.2).
8. ``current_pending_invalid_ancestor`` — the COUNT of current
   claims with an evidential dependency path (transitive,
   from→to) to a claim whose head is ``pending | invalid``
   (threshold 0).
9. ``high_severity_incidents`` — the COUNT of unresolved
   high-severity incidents of the window: audit ``alert_raised``
   kind ``idempotency_key_conflict`` (each = one incident: same
   idempotency key with a different arguments hash, §5.7/§14) plus
   ``records_inconsistent`` (the reconciliation record; each alert
   is an incident, threshold 0).
10. ``blind_provenance_path`` — of the BLIND SAMPLE (seeded,
    stratified by claim_type × epistemic_status, §22.2), the share
    with a complete provenance path: current assessment → linked
    evidence (assessment_evidence) → each evidence resolves to a
    source OR an artifact (threshold ≥0.90).
11. ``blind_scope`` — of the blind sample, the share that does not
    go beyond the evidence scope: ``assessed_scope`` is non-empty;
    a strong status (``supported | refuted``) requires at least one
    linked evidence; every linked evidence declares a non-empty
    ``scope`` (threshold ≥0.80).

The blind sample is drawn deterministically from ``run.blind_sample_
seed`` + ``run.blind_sample_size``: seeded shuffle within each
stratum, proportional allocation, remainder from the global seeded
shuffle (reproducible for the same run row).

METHOD LIMITATION (docs/eval/EVAL-3-freeze.md «Ограничение метода»):
§22.2 defines the blind sample as a MANUAL procedure — a person
judges whether each sampled claim follows from its evidence and the
95% confidence interval is published. The two blind gates measure
only the STRUCTURAL projection (linked evidence exists, scope is
declared); the run report marks them as a structural check, not as
passed §22.2 gates. The sample rendered for human review
(``noezemactl blind-sample``) uses exactly this selection
(``packages.evaluation.blind``).

HEAD SELECTION (T7.19, EVAL-3d post-mortem; §14.1, §8.7.2): a
mid-run online activation leaves a claim with one head per config
snapshot (``UNIQUE(claim_id, config_snapshot_id)`` shadow heads).
Every head-based gate counts EXACTLY ONE head per claim — the head
of the EFFECTIVE snapshot, resolved through the runtime pointer
(``runtime_config_heads.active_config_snapshot_id``; pointer
equality, NOT ``config_snapshots.activation_state``) — the same
resolution the query path uses (``MemoryService.claim_view``).
Claims without a head on the active snapshot have no current
lifecycle under the effective config and are not counted; claims
whose active head is pending/invalid are not "current" and fall out
of the ``assessment_state = 'current'`` filters. Without this filter
a mid-run activation double-counts claims (denominators inflated,
ratios distorted) and the blind-gate per-claim queries raise
``MultipleResultsFound``.
"""

from __future__ import annotations

import math
from datetime import UTC, datetime
from typing import Any, Literal, get_args

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.evaluation.blind import EFFECTIVE_SNAPSHOT_SQL, blind_sample_claim_ids
from packages.evaluation.service import EvaluationRun

#: «при N<20 gate получает insufficient_sample» (§22.2)
MIN_SAMPLE = 20

_GRADE_CASE = (
    "CASE {g} WHEN 'E0' THEN 0 WHEN 'E1' THEN 1 WHEN 'E2' THEN 2 "
    "WHEN 'E3' THEN 3 WHEN 'E4' THEN 4 ELSE 0 END"
)

#: the CLOSED set of §22.2 comparison directions (ARCHITECTURE.md:
#: 2602–2611): "at_least" (pass when ratio >= threshold), "at_most"
#: (pass when ratio <= threshold) or "below" (pass when ratio <
#: threshold, STRICT). Closed at the type level — mypy strict rejects
#: any other value statically — and ``_gate()`` additionally fails
#: closed with ``ValueError`` at runtime for a value that reached it
#: untyped: there is no silent "at_most" default (T7.28, ADR-0015:
#: the class of boundary mismatch where a misspelled direction
#: silently flips the boundary outcome).
GateDirection = Literal["at_least", "at_most", "below"]

#: the same three values as a runtime set (derived from the Literal via
#: ``get_args`` — one source of truth). ``_gate()`` validates against
#: this and fails
#: closed with ``ValueError`` for a value that reached it untyped —
#: the type-level closure is the primary guard (mypy strict rejects
#: any other value statically at the call site); there is no silent
#: "at_most" default (T7.28, ADR-0015).
_GATE_DIRECTIONS: frozenset[str] = frozenset(get_args(GateDirection))

#: direction of each ratio gate (one of ``GateDirection``). The spec
#: wording decides the direction: ARCHITECTURE.md:2607 «due/stale
#: time-sensitive claims <20%» is strictly less, so
#: ``due_stale_time_sensitive`` is "below" (T7.28, ADR-0015:
#: previously "at_most" — exactly 20% passed, changing the boundary
#: outcome 6/30 from spec-failed to passed).
_GATE_DIRECTION: dict[str, GateDirection] = {
    "new_supported_refuted_e2": "at_least",
    "external_temporal_e3": "at_least",
    "eligible_sessions_with_outcome": "at_least",
    "near_duplicate_questions": "at_most",
    "significant_claim_reuse": "at_least",
    "due_stale_time_sensitive": "below",
    "reassessment_slo": "at_least",
    "blind_provenance_path": "at_least",
    "blind_scope": "at_least",
}


async def compute_gates(
    db: AsyncSession, *, run: EvaluationRun, now: datetime | None = None
) -> dict[str, dict[str, Any]]:
    """Compute all 11 §22.2 gates for the run's session window.

    ``now`` fixes the wall-clock instant the time-dependent gate
    (``due_stale_time_sensitive``, the §8.6 freshness rule) is
    evaluated at — a pure function of (domain data, ``now``); defaults
    to the computation instant."""
    ts = now or datetime.now(UTC)
    gates: dict[str, dict[str, Any]] = {}
    gates["new_supported_refuted_e2"] = await _gate_new_e2(db, run)
    gates["external_temporal_e3"] = await _gate_external_e3(db, run)
    gates["eligible_sessions_with_outcome"] = await _gate_sessions(db, run)
    gates["near_duplicate_questions"] = await _gate_near_dup(db, run)
    gates["significant_claim_reuse"] = await _gate_reuse(db, run)
    gates["due_stale_time_sensitive"] = await _gate_due_stale(db, run, ts)
    gates["reassessment_slo"] = await _gate_slo(db, run)
    gates["current_pending_invalid_ancestor"] = await _gate_pending_ancestor(db)
    gates["high_severity_incidents"] = await _gate_incidents(db, run)
    blind = await blind_sample_claim_ids(db, run)
    gates["blind_provenance_path"] = await _gate_blind_provenance(db, blind, run)
    gates["blind_scope"] = await _gate_blind_scope(db, blind, run)
    return gates


_WINDOW_CTE = (
    "WITH window_sessions AS ("
    "  SELECT id FROM sessions WHERE started_at >= :run_start"
    "  AND started_at IS NOT NULL"
    ")"
)


def _grade_at_least(sql_grade: str, grade: str) -> str:
    """SQL boolean: grade >= the given grade."""
    literal = "'" + grade + "'"
    return f"({_GRADE_CASE.format(g=sql_grade)} >= {_GRADE_CASE.format(g=literal)})"


def wilson_ci95(numerator: int, denominator: int) -> tuple[float, float] | None:
    """95% Wilson score interval for the share numerator/denominator.

    §22.2 requires publishing a 95% confidence interval with the blind
    sample / gate results. Reporting only: the gate outcome is decided
    by the point ratio alone, never by the interval. Returns None when
    the denominator is empty (no measurement).
    """
    if denominator <= 0:
        return None
    z = 1.959963984540054  # 95% two-sided
    p = numerator / denominator
    denom = 1.0 + z * z / denominator
    center = (p + z * z / (2.0 * denominator)) / denom
    half = (
        z
        * math.sqrt(p * (1.0 - p) / denominator + z * z / (4.0 * denominator * denominator))
    ) / denom
    return (
        round(max(0.0, center - half), 4),
        round(min(1.0, center + half), 4),
    )


def _gate(
    *,
    numerator: int,
    denominator: int,
    threshold: float,
    direction: GateDirection,
    gate_name: str | None = None,
) -> dict[str, Any]:
    """Apply the three-outcome rule to one ratio gate.

    ``gate_name`` (the gate's key in ``_GATE_DIRECTION``) is reported
    in the ``ValueError`` for an unknown direction. A well-typed
    ``GateDirection`` can never be unknown — the runtime check fails
    closed for a value that reached ``_gate`` untyped (no silent
    "at_most" default; T7.28, ADR-0015)."""
    if direction not in _GATE_DIRECTIONS:
        # a well-typed GateDirection can never fail this check (mypy
        # strict rejects any other value statically at the call site);
        # the guard fails closed for a value that reached _gate
        # untyped — it must NOT silently become a non-strict "at_most"
        name = f"gate {gate_name!r}: " if gate_name is not None else ""
        raise ValueError(
            f"{name}unknown gate direction {direction!r} "
            "(expected 'at_least', 'at_most' or 'below')"
        )
    base: dict[str, Any] = {
        "numerator": numerator,
        "denominator": denominator,
        "threshold": threshold,
    }
    ci = wilson_ci95(numerator, denominator)
    if ci is not None:
        base["ci95"] = {"low": ci[0], "high": ci[1]}
    if denominator < MIN_SAMPLE:
        return {**base, "outcome": "insufficient_sample"}
    ratio = numerator / denominator
    if direction == "at_least":
        ok = ratio >= threshold
    elif direction == "at_most":  # exactly at the threshold passes
        ok = ratio <= threshold
    else:  # "below" — the only remaining member of the closed set;
        # strict: exactly at the threshold fails
        ok = ratio < threshold
    return {**base, "ratio": round(ratio, 4), "outcome": "passed" if ok else "failed"}


async def _scalar(db: AsyncSession, sql: str, params: dict[str, Any]) -> Any:
    return (await db.execute(text(sql), params)).scalar_one()


async def _two(db: AsyncSession, sql: str, params: dict[str, Any]) -> tuple[int, int]:
    row = (await db.execute(text(sql), params)).first()
    return (int(row[0]), int(row[1])) if row is not None else (0, 0)


async def _gate_new_e2(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        {_WINDOW_CTE},
        new_claims AS (
          SELECT c.id FROM claims c
          WHERE c.created_in_session IN (SELECT id FROM window_sessions)
        ),
        cur AS (
          SELECT h.claim_id, a.effective_grade
          FROM claim_assessment_heads h
          JOIN claim_assessments a ON a.id = h.current_assessment_id
          WHERE h.assessment_state = 'current'
            AND h.epistemic_status IN ('supported', 'refuted')
            AND h.claim_id IN (SELECT id FROM new_claims)
            AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
        )
        SELECT count(*),
               count(*) FILTER (WHERE {_grade_at_least("COALESCE(cur.effective_grade, 'E0')", 'E2')})
        FROM cur
    """
    total, e2_plus = await _two(db, sql, {"run_start": run.started_at})
    th = float(run.thresholds.get("new_supported_refuted_e2", 0.80))
    return _gate(
        numerator=e2_plus,
        denominator=total,
        threshold=th,
        direction=_GATE_DIRECTION["new_supported_refuted_e2"],
        gate_name="new_supported_refuted_e2",
    )


async def _gate_external_e3(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        {_WINDOW_CTE},
        new_claims AS (
          SELECT c.id FROM claims c
          WHERE c.created_in_session IN (SELECT id FROM window_sessions)
            AND c.claim_type IN ('external_fact', 'temporal_fact')
        ),
        cur AS (
          SELECT h.claim_id, a.effective_grade
          FROM claim_assessment_heads h
          JOIN claim_assessments a ON a.id = h.current_assessment_id
          WHERE h.assessment_state = 'current'
            AND h.epistemic_status IN ('supported', 'refuted')
            AND h.claim_id IN (SELECT id FROM new_claims)
            AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
        )
        SELECT count(*), count(*) FILTER (WHERE cur.effective_grade = 'E3')
        FROM cur
    """
    total, e3 = await _two(db, sql, {"run_start": run.started_at})
    th = float(run.thresholds.get("external_temporal_e3", 1.00))
    return _gate(
        numerator=e3,
        denominator=total,
        threshold=th,
        direction=_GATE_DIRECTION["external_temporal_e3"],
        gate_name="external_temporal_e3",
    )


async def _gate_sessions(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        {_WINDOW_CTE},
        eligible AS (
          SELECT s.id, s.question_id FROM sessions s
          WHERE s.id IN (SELECT id FROM window_sessions)
            AND s.state IN ('succeeded', 'succeeded_partial', 'failed', 'cancelled')
            AND NOT (s.state = 'cancelled' AND s.abort_requested_at IS NOT NULL)
        ),
        with_outcome AS (
          SELECT DISTINCT s.id FROM eligible s
          WHERE EXISTS (SELECT 1 FROM evidence e
                        WHERE e.created_in_session = s.id)
             OR EXISTS (SELECT 1 FROM claim_revisions r
                        WHERE r.session_id = s.id)
             OR EXISTS (SELECT 1 FROM questions q
                        WHERE q.id = s.question_id
                          AND q.state <> 'candidate')
        )
        SELECT (SELECT count(*) FROM eligible),
               (SELECT count(*) FROM with_outcome)
    """
    total, with_outcome = await _two(db, sql, {"run_start": run.started_at})
    th = float(run.thresholds.get("eligible_sessions_with_outcome", 0.60))
    return _gate(
        numerator=with_outcome,
        denominator=total,
        threshold=th,
        direction=_GATE_DIRECTION["eligible_sessions_with_outcome"],
        gate_name="eligible_sessions_with_outcome",
    )


async def _gate_near_dup(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        {_WINDOW_CTE}
        SELECT
          (SELECT count(DISTINCT s.question_id) FROM sessions s
           WHERE s.id IN (SELECT id FROM window_sessions)
             AND s.question_id IS NOT NULL),
          (SELECT count(DISTINCT a.payload->>'question_id')
           FROM audit_events a
           WHERE a.type = 'repeat_cycle_detected'
             AND a.session_id IN (SELECT id FROM window_sessions))
    """
    selected, flagged = await _two(db, sql, {"run_start": run.started_at})
    th = float(run.thresholds.get("near_duplicate_questions", 0.15))
    return _gate(
        numerator=flagged,
        denominator=selected,
        threshold=th,
        direction=_GATE_DIRECTION["near_duplicate_questions"],
        gate_name="near_duplicate_questions",
    )


async def _gate_reuse(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        WITH cur AS (
          SELECT h.claim_id, a.effective_grade
          FROM claim_assessment_heads h
          JOIN claim_assessments a ON a.id = h.current_assessment_id
          WHERE h.assessment_state = 'current'
            AND h.epistemic_status IN ('supported', 'disputed', 'refuted')
            AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
        ),
        significant AS (
          SELECT claim_id FROM cur
          WHERE {_grade_at_least("COALESCE(effective_grade, 'E0')", 'E2')}
        ),
        refs AS (
          SELECT claim_id, created_in_session FROM evidence
          WHERE claim_id IN (SELECT claim_id FROM significant)
            AND created_in_session IS NOT NULL
          UNION
          SELECT claim_id, session_id FROM claim_revisions
          WHERE claim_id IN (SELECT claim_id FROM significant)
            AND session_id IS NOT NULL
          UNION
          SELECT from_claim_id, created_in_session FROM claim_dependencies
          WHERE from_claim_id IN (SELECT claim_id FROM significant)
            AND created_in_session IS NOT NULL
          UNION
          SELECT to_claim_id, created_in_session FROM claim_dependencies
          WHERE to_claim_id IN (SELECT claim_id FROM significant)
            AND created_in_session IS NOT NULL
        ),
        per_claim AS (
          SELECT claim_id, count(DISTINCT created_in_session) AS n_sessions
          FROM refs GROUP BY claim_id
        )
        SELECT (SELECT count(*) FROM significant),
               (SELECT count(*) FROM per_claim WHERE n_sessions >= 2)
    """
    total, reused = await _two(db, sql, {})
    th = float(run.thresholds.get("significant_claim_reuse", 0.25))
    return _gate(
        numerator=reused,
        denominator=total,
        threshold=th,
        direction=_GATE_DIRECTION["significant_claim_reuse"],
        gate_name="significant_claim_reuse",
    )


async def _gate_due_stale(
    db: AsyncSession, run: EvaluationRun, now: datetime
) -> dict[str, Any]:
    """T7.27 (ADR-0014): the §8.6/T3.7 freshness rule evaluated at the
    gate-computation instant over ``reverify_after`` — the same rule as
    ``packages.memory.freshness.freshness_status`` (``now <
    reverify_after`` → fresh; ``now >= reverify_after`` → due; NULL →
    no deadline by construction, T7.32/ADR-0017). T7.32 (ADR-0017):
    the DENOMINATOR counts only the claims that CAN become due
    (``reverify_after IS NOT NULL``) — a fixed-point claim (explicit
    question date / dateless question with the model's as_of) has no
    deadline and can never be due; including it would let the gate
    pass by composition (more fixed-point claims) instead of by
    freshness health. The stored ``claims.freshness_status`` column is
    NOT read: it is a display cache updated only by write paths, and
    the gate must not depend on whether a background reassessment
    (triggered by an activation flip) ever ran (EVAL-4d: no flip, no
    reassessment, the column stayed 'fresh' for 22/28 overdue claims
    and the gate reported 0/28 passed)."""
    sql = f"""
        SELECT count(*) FILTER (
                 WHERE c.reverify_after IS NOT NULL
             ),
               count(*) FILTER (
                 WHERE c.reverify_after IS NOT NULL
                   AND c.reverify_after <= :now
               )
        FROM claims c
        JOIN claim_assessment_heads h
          ON h.claim_id = c.id AND h.assessment_state = 'current'
             AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
        WHERE c.claim_type = 'temporal_fact'
    """
    total, due_stale = await _two(db, sql, {"now": now})
    th = float(run.thresholds.get("due_stale_time_sensitive", 0.20))
    return _gate(
        numerator=due_stale,
        denominator=total,
        threshold=th,
        direction=_GATE_DIRECTION["due_stale_time_sensitive"],
        gate_name="due_stale_time_sensitive",
    )


async def _gate_slo(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    slo = run.thresholds.get("reassessment_slo_seconds")
    if slo is None:
        # «зафиксировано до run» — the SLO must be fixed before the
        # series; a missing SLO means the measurement did not happen.
        return {
            "numerator": 0,
            "denominator": 0,
            "threshold": None,
            "outcome": "insufficient_sample",
            "detail": "reassessment_slo_seconds not fixed before the run",
        }
    sql = """
        SELECT count(*),
               count(*) FILTER (
                 WHERE (completed_at - enqueued_at)
                       <= make_interval(secs => :slo)
               )
        FROM reassessment_jobs
        WHERE status = 'completed' AND completed_at IS NOT NULL
    """
    total, within = await _two(db, sql, {"slo": float(slo)})
    # «все runnable jobs укладываются в SLO» — the required share is
    # 100%; the SLO itself (seconds) is the fixed window
    gate = _gate(
        numerator=within,
        denominator=total,
        threshold=1.0,
        direction=_GATE_DIRECTION["reassessment_slo"],
        gate_name="reassessment_slo",
    )
    gate["slo_seconds"] = float(slo)
    return gate


async def _gate_pending_ancestor(db: AsyncSession) -> dict[str, Any]:
    # T7.19: both the "bad" (pending/invalid) set and the current-head
    # denominator are the heads of the effective snapshot — the
    # lifecycle of a claim under the effective config (§14.1).
    sql = f"""
        WITH RECURSIVE
        bad AS (
          SELECT claim_id FROM claim_assessment_heads
          WHERE assessment_state IN ('pending', 'invalid')
            AND config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
        ),
        -- claims that (transitively) depend on a bad claim via
        -- evidential edges (from = depender, to = dependee)
        deps AS (
          SELECT d.from_claim_id
          FROM claim_dependencies d
          WHERE d.kind = 'evidential'
            AND d.to_claim_id IN (SELECT claim_id FROM bad)
          UNION
          SELECT d.from_claim_id
          FROM claim_dependencies d
          JOIN deps x ON x.from_claim_id = d.to_claim_id
          WHERE d.kind = 'evidential'
        )
        SELECT
          (SELECT count(*) FROM claim_assessment_heads h
           WHERE h.assessment_state = 'current'
             AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}
             AND h.claim_id IN (SELECT from_claim_id FROM deps)),
          (SELECT count(*) FROM claim_assessment_heads h
           WHERE h.assessment_state = 'current'
             AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL})
    """
    violations, total_current = await _two(db, sql, {})
    base = {"numerator": violations, "denominator": total_current, "threshold": 0}
    if total_current < MIN_SAMPLE:
        return {**base, "outcome": "insufficient_sample"}
    return {**base, "outcome": "passed" if violations == 0 else "failed"}


async def _gate_incidents(db: AsyncSession, run: EvaluationRun) -> dict[str, Any]:
    sql = f"""
        {_WINDOW_CTE}
        SELECT
          (SELECT count(*) FROM audit_events a
           WHERE a.type = 'alert_raised'
             AND a.payload->>'kind' = 'idempotency_key_conflict'
             AND a.session_id IN (SELECT id FROM window_sessions))
          +
          (SELECT count(*) FROM audit_events a
           WHERE a.type = 'alert_raised'
             AND a.payload->>'kind' = 'records_inconsistent'
             AND a.session_id IN (SELECT id FROM window_sessions))
    """
    count = int(await _scalar(db, sql, {"run_start": run.started_at}))
    return {
        "numerator": count,
        "denominator": None,
        "threshold": 0,
        "outcome": "passed" if count == 0 else "failed",
    }


async def _gate_blind_provenance(
    db: AsyncSession, blind: list[Any], run: EvaluationRun
) -> dict[str, Any]:
    th = float(run.thresholds.get("blind_provenance_path", 0.90))
    if not blind:
        return {
            "numerator": 0,
            "denominator": 0,
            "threshold": th,
            "outcome": "insufficient_sample",
        }
    complete = 0
    for cid in blind:
        if await _provenance_complete(db, cid):
            complete += 1
    return _gate(
        numerator=complete,
        denominator=len(blind),
        threshold=th,
        direction=_GATE_DIRECTION["blind_provenance_path"],
        gate_name="blind_provenance_path",
    )


async def _provenance_complete(db: AsyncSession, claim_id: Any) -> bool:
    """claim → current assessment (of the EFFECTIVE snapshot, T7.19)
    → linked evidence → source / artifact."""
    head = (
        await db.execute(
            text(
                "SELECT h.current_assessment_id FROM claim_assessment_heads h "
                "WHERE h.claim_id = :c AND h.assessment_state = 'current' "
                f"AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}"
            ),
            {"c": claim_id},
        )
    ).scalar_one_or_none()
    if head is None:
        return False
    evs = (
        await db.execute(
            text(
                "SELECT e.source_id, e.chunk_id, e.observation_artifact_id "
                "FROM evidence e "
                "JOIN assessment_evidence ae ON ae.evidence_id = e.id "
                "WHERE ae.assessment_id = :a"
            ),
            {"a": head},
        )
    ).all()
    if not evs:
        return False
    for ev in evs:
        src, artifact = ev[0], ev[2]
        if artifact is not None:
            n = int(
                await _scalar(
                    db,
                    "SELECT count(*) FROM artifacts WHERE id = :i",
                    {"i": artifact},
                )
            )
            if n == 0:
                return False
            continue
        if src is None:
            return False
        n = int(
            await _scalar(db, "SELECT count(*) FROM sources WHERE id = :i", {"i": src})
        )
        if n == 0:
            return False
    return True


async def _gate_blind_scope(
    db: AsyncSession, blind: list[Any], run: EvaluationRun
) -> dict[str, Any]:
    th = float(run.thresholds.get("blind_scope", 0.80))
    if not blind:
        return {
            "numerator": 0,
            "denominator": 0,
            "threshold": th,
            "outcome": "insufficient_sample",
        }
    in_scope = 0
    for cid in blind:
        if await _in_scope(db, cid):
            in_scope += 1
    return _gate(
        numerator=in_scope,
        denominator=len(blind),
        threshold=th,
        direction=_GATE_DIRECTION["blind_scope"],
        gate_name="blind_scope",
    )


async def _in_scope(db: AsyncSession, claim_id: Any) -> bool:
    """The current assessment does not go beyond the evidence scope:

    1. ``assessed_scope`` is non-empty (the scope is declared);
    2. a strong status (supported/refuted) requires at least one
       linked evidence;
    3. every linked evidence declares a non-empty ``scope``.
    """
    row = (
        await db.execute(
            text(
                "SELECT h.epistemic_status, a.assessed_scope, "
                "  (SELECT count(*) FROM assessment_evidence ae "
                "   WHERE ae.assessment_id = h.current_assessment_id) AS n_ev, "
                "  (SELECT count(*) FROM assessment_evidence ae "
                "   JOIN evidence e ON e.id = ae.evidence_id "
                "   WHERE ae.assessment_id = h.current_assessment_id "
                "     AND e.scope = '{}'::jsonb) AS n_empty_scope "
                "FROM claim_assessment_heads h "
                "JOIN claim_assessments a ON a.id = h.current_assessment_id "
                "WHERE h.claim_id = :c AND h.assessment_state = 'current' "
                f"  AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL}"
            ),
            {"c": claim_id},
        )
    ).first()
    if row is None:
        return False
    status, scope, n_ev, n_empty = row[0], row[1], int(row[2]), int(row[3])
    if not isinstance(scope, dict) or not scope:
        return False
    if status in ("supported", "refuted") and n_ev == 0:
        return False
    return n_empty == 0
