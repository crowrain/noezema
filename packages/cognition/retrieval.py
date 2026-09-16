"""Claim retrieval for the context pack (T3.9, §5.4, §5.4.2).

Hybrid search for the MVP: PostgreSQL full-text (``ts_rank``) +
significance (grade level, confidence) + freshness + link to the question.
Embeddings are off in the bootstrap config (``embeddings.enabled=False``);
pgvector would be an ADR-gated extension, not part of v1.

The §5.4.2 guarantee is enforced HERE, at the retrieval/truncation seam:

- current claims spend the ``claims_evidence`` section budget;
- pending/invalid claims spend a SEPARATE small limit
  (``pending_claims``) and can never displace current knowledge;
- a pending/invalid line carries its label ON THE SAME LINE as the
  statement, and the label counts toward the token budget — if the budget
  cannot fit the labeled line, the claim is excluded entirely, never
  unlabeled;
- only pending/invalid claims directly relevant to the question are
  included (the background re-assessment backlog is the worker's job).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import RowMapping, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import (
    AssessmentState,
    EffectiveGrade,
    EpistemicStatus,
    FreshnessStatus,
)

PENDING_LABEL = "[без действующей оценки: pending]"
INVALID_LABEL = "[оценка недействительна: invalid]"

#: minimum ts_rank for a pending/invalid claim to count as "directly
#: relevant" to the question (§5.4.2)
RELEVANCE_THRESHOLD = 0.1

#: floor below which a ts_rank is treated as "no match" (float noise)
MIN_RELEVANCE = 1e-9

_FRESHNESS_SCORE = {
    FreshnessStatus.FRESH.value: 1.0,
    FreshnessStatus.UNKNOWN.value: 0.7,
    FreshnessStatus.DUE.value: 0.4,
    FreshnessStatus.STALE.value: 0.1,
}


@dataclass(frozen=True)
class RetrievedClaim:
    claim_id: uuid.UUID
    statement: str
    claim_type: str
    assessment_state: AssessmentState
    epistemic_status: EpistemicStatus | None
    grade: EffectiveGrade | None
    confidence: float | None
    freshness: FreshnessStatus
    relevance: float
    score: float
    #: the §5.4.2 label for non-current claims ("" for current)
    label: str = ""
    evidence_lines: tuple[str, ...] = ()

    @property
    def line(self) -> str:
        """The exact line that goes into the context (label + statement,
        same line, §5.4.2). The claim ID prefix (T4.1) lets the curator
        reference the claim in `dependencies` proposals; the label and
        the ID share the line, so the §5.4.2 all-or-nothing rule covers
        both."""
        prefix = f"{self.label} " if self.label else ""
        status_text = self.epistemic_status.value if self.epistemic_status is not None else "unknown"
        meta = f" ({status_text}"
        if self.grade is not None:
            meta += f", {self.grade.value}"
        if self.confidence is not None:
            meta += f", p={self.confidence:.2f}"
        meta += ")"
        return f"[c:{self.claim_id}] {prefix}{self.statement}{meta}"


@dataclass(frozen=True)
class RetrievalResult:
    current: list[RetrievedClaim] = field(default_factory=list)
    pending_invalid: list[RetrievedClaim] = field(default_factory=list)


def _row_to_claim(row: RowMapping, state: AssessmentState) -> RetrievedClaim:
    label = ""
    if state is AssessmentState.PENDING:
        label = PENDING_LABEL
    elif state is AssessmentState.INVALID:
        label = INVALID_LABEL
    grade = EffectiveGrade(row["effective_grade"]) if row["effective_grade"] else None
    epistemic = EpistemicStatus(row["epistemic_status"]) if row["epistemic_status"] else None
    relevance = float(row["relevance"])
    significance = (grade.level if grade is not None else 0) * 0.15 + (
        row["confidence"] if row["confidence"] is not None else 0.0
    )
    freshness = _FRESHNESS_SCORE.get(row["freshness_status"], 0.5)
    score = relevance + significance + freshness
    raw_id = row["id"]
    claim_id = raw_id if isinstance(raw_id, uuid.UUID) else uuid.UUID(str(raw_id))
    return RetrievedClaim(
        claim_id=claim_id,
        statement=row["statement"],
        claim_type=row["claim_type"],
        assessment_state=state,
        epistemic_status=epistemic,
        grade=grade,
        confidence=row["confidence"],
        freshness=FreshnessStatus(row["freshness_status"]),
        relevance=relevance,
        score=score,
        label=label,
    )


async def retrieve(
    db: AsyncSession,
    question_text: str,
    *,
    snapshot_id: uuid.UUID,
    current_limit: int = 20,
    pending_limit: int = 5,
) -> RetrievalResult:
    """Retrieve current + (separately) pending/invalid claims for the
    question, under the effective config snapshot (pointer equality,
    §14.1).

    Matching is PARTIAL (a claim sharing any question word is a
    candidate), ranked by ``ts_rank`` + significance; the strict
    all-words AND of ``plainto_tsquery`` would drop near-misses.
    """
    # Cross-lingual ranking (ADR-0006 rev): ``statement`` is indexed
    # with the ``russian`` config, ``search_statements`` (the
    # model-provided English renderings) with ``english``; a query in
    # either language matches (plainto_tsquery yields an empty tsquery
    # for a foreign-language query → ts_rank 0 → GREATEST picks the
    # language that actually matched).
    query = text(
        """
        SELECT c.id, c.statement, c.claim_type, c.freshness_status,
               h.assessment_state, h.epistemic_status,
               a.effective_grade, a.confidence,
               GREATEST(
                 ts_rank(to_tsvector('russian', c.statement),
                         plainto_tsquery('russian', :q)),
                 ts_rank(to_tsvector('english',
                         coalesce((SELECT string_agg(s, ' ')
                                   FROM jsonb_array_elements_text(
                                     coalesce(c.search_statements, '[]'::jsonb)) s),
                                   '')),
                         plainto_tsquery('english', :q))
               ) AS relevance
        FROM claims c
        JOIN claim_assessment_heads h
          ON h.claim_id = c.id
         AND h.config_snapshot_id = :snap
        LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
        WHERE GREATEST(
                ts_rank(to_tsvector('russian', c.statement),
                        plainto_tsquery('russian', :q)),
                ts_rank(to_tsvector('english',
                        coalesce((SELECT string_agg(s, ' ')
                                  FROM jsonb_array_elements_text(
                                    coalesce(c.search_statements, '[]'::jsonb)) s),
                                  '')),
                        plainto_tsquery('english', :q))) > 0
        ORDER BY relevance DESC
        LIMIT :limit
        """
    )
    params = {"q": question_text, "snap": snapshot_id}

    result = await db.execute(query, {**params, "limit": current_limit + pending_limit})
    all_rows = result.mappings().all()

    # §8.6 ancestor check: while a dependency invalidation barrier is
    # open, the claims in its closure are NOT current — even if their
    # head row still says current (the batch has not reached them yet).
    from packages.memory.cascade import protected_claim_ids

    protected = await protected_claim_ids(db)

    current: list[RetrievedClaim] = []
    pending_invalid: list[RetrievedClaim] = []
    # MIN_RELEVANCE is the noise floor: ts_rank returns ~1e-20 (not 0)
    # for a non-matching / partial-AND tsquery, so the SQL `> 0` filter
    # never actually filters — this floor is the real match/no-match
    # boundary (a full query match ranks ≥ ~1e-2, partial AND matches
    # rank ~1e-20 = no match by design, pre-existing semantics).
    for row in all_rows:
        if float(row["relevance"]) < MIN_RELEVANCE:
            continue  # float-noise "match" = no match
        state = AssessmentState(row["assessment_state"])
        claim = _row_to_claim(row, state)
        if state is AssessmentState.CURRENT:
            if claim.claim_id in protected:
                continue  # open barrier ancestor protection (§8.6)
            current.append(claim)
        else:
            # §5.4.2: only directly relevant pending/invalid claims
            if claim.relevance >= RELEVANCE_THRESHOLD and len(pending_invalid) < pending_limit:
                pending_invalid.append(claim)

    current.sort(key=lambda c: c.score, reverse=True)
    pending_invalid.sort(key=lambda c: c.score, reverse=True)
    return RetrievalResult(current=current, pending_invalid=pending_invalid)
