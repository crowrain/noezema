"""T7.83 (ADR-0033): чистый детектор «дубля по значению» в кураторском предложении.

Defect shape (stand .92, session f452a978, config-v19): the curator summary says it
reverified two claims already visible in the context pack, but both proposal ops
arrive without `existing_claim_id`. Byte-exact dedup (T7.9) cannot see a paraphrase,
so the commit writes a SECOND claim carrying the same value of the same indicator for
the same period — and the answer card shows «5,59%» twice with contradictory badges.

Host definition of a value duplicate (conservative, deterministic, no LLM):
a new op (no `existing_claim_id`) and a context-pack candidate are value duplicates
when all three hold:

* equal `claim_type`;
* equal NON-EMPTY set of significant numbers in the two statements — decimal tokens
  with digit boundaries (`\\d+(?:[.,]\\d+)+`, the same boundary semantics as
  assertion_window): «5,59» ≠ «5,6», and «105,59» is ONE token, never a hit on «5,59».
  Integers without a decimal separator are NOT values (years and dates stay out);
* compatible period: the new statement's years (plus its `as_of` year) form a NON-EMPTY
  set that is a SUBSET of the candidate's years (plus its `as_of` year) — the candidate
  may name extra years («опубликовано … 2026» belongs to the candidate, not to the period).

Fixture pair proof (SELECT from noezema-dev): e189c80d ↔ 9266248e is a duplicate;
391c4388 ↔ 6d9a12ff is NOT (values {14.5} vs {13.1, 15.6}).

Decision policy (variant (i)/(ii) of T7.83): exactly ONE candidate matches strictly and
the op's evidence links are all `supports` → the host converts the op into a reverify of
that candidate (variant (i): the new supporting evidence joins the candidate's union and
can only keep or raise its grade — see ADR-0033; the anchor's as_of/scope are preserved
by packages/memory/reverify.py). No strict match, several matches, or counter-evidence in
the links → the op is left alone with an honest reason recorded (variant (ii) territory:
without new supporting evidence nothing justifies touching the candidate, and a dispute
must remain a dispute). Zero suspects → silent: the payload stays byte-identical to pre-T7.83.

The module decides; applying the conversion (staged copies + audit annotation) is the
host's job in apps/orchestrator/orchestrator.py — ops with an explicit `existing_claim_id`
are never passed here (T7.34 already owns them).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

from packages.domain.models.base import JsonDict

#: decimal value token: maximal run of digits joined by ',' or '.', with digit
#: boundaries — mirrors apps/orchestrator/assertion_window.py::_numeric_boundary_ok
#: semantics (a value never starts or ends inside another number).
_VALUE_TOKEN_RE = re.compile(r"(?<![0-9])(\d+(?:[.,]\d+)+)(?![0-9])")

#: a period year: a standalone 4-digit token (1000–2099) outside any value token;
#: «год-одиночка» inside the text counts, digits inside a decimal value never do.
_YEAR_TOKEN_RE = re.compile(r"(?<!\d)(1[0-9]{3}|20[0-9]{2})(?!\d)")


def _canonical_value(token: str) -> str | None:
    """Canonical form of one decimal token: separators → '.', float round-trip.

    Returns None for a token that cannot be canonicalized (e.g. «0,53.59») — the
    caller then treats the whole statement as unanalyzable (conservative silence)."""
    normalized = token.replace(",", ".")
    try:
        value = float(normalized)
    except ValueError:
        return None
    if value != value or value in (float("inf"), float("-inf")):
        return None
    return repr(value)


def statement_values(statement: str) -> frozenset[str]:
    """Set of significant decimal values; empty set = nothing significant to match.

    A statement containing an unanalyzable numeric run is treated as having NO
    values (conservative: never declare a duplicate on a text we cannot read)."""
    values: set[str] = set()
    for m in _VALUE_TOKEN_RE.finditer(statement):
        canonical = _canonical_value(m.group(1))
        if canonical is None:
            return frozenset()
        values.add(canonical)
    return frozenset(values)


def statement_years(statement: str) -> frozenset[int]:
    """Standalone 4-digit years in the text, after masking decimal value tokens
    (so a year glued to a value — «12,2026» — is neither a value nor a year)."""
    masked = _VALUE_TOKEN_RE.sub(" ", statement)
    return frozenset(int(m.group(1)) for m in _YEAR_TOKEN_RE.finditer(masked))


@dataclass(frozen=True)
class ValueDuplicateClaim:
    """One claim op of the proposal WITHOUT an explicit existing_claim_id."""

    index: int
    statement: str
    claim_type: str
    as_of_year: int | None
    has_support_link: bool
    has_counter_link: bool


@dataclass(frozen=True)
class ValueDuplicateCandidate:
    """One claim of the session's context pack that has a head in this snapshot."""

    claim_id: str
    statement: str
    claim_type: str
    as_of_year: int | None
    relied: bool = False


def _period(
    statement: str, as_of_year: int | None
) -> frozenset[int]:
    years: set[int] = set(statement_years(statement))
    if as_of_year is not None:
        years.add(as_of_year)
    return frozenset(years)


def _entry(
    claim: ValueDuplicateClaim,
    *,
    action: str,
    target: str | None,
    reason: str,
) -> JsonDict:
    return {
        "claim_index": claim.index,
        "target": target,
        "action": action,
        "reason": reason,
    }


def find_value_duplicates(
    claims: Sequence[ValueDuplicateClaim],
    candidates: Sequence[ValueDuplicateCandidate],
) -> list[JsonDict]:
    """Decisions ONLY for ops that have same-type/same-value suspects; everything
    else stays silent (no entry → no payload key → old audits byte-identical).

    Ordering note: `relied` orders nothing and exempts nothing — the exactly-one
    rule stands on its own; ordering only makes the audit deterministic."""
    ordered_candidates = sorted(candidates, key=lambda c: (not c.relied, c.claim_id))
    decisions: list[JsonDict] = []
    for claim in claims:
        new_values = statement_values(claim.statement)
        if not new_values:
            continue  # integers/years only — conservative silence (T7.9 owns byte-exact dups)
        new_period = _period(claim.statement, claim.as_of_year)
        suspects = [
            candidate
            for candidate in ordered_candidates
            if candidate.claim_type == claim.claim_type
            and statement_values(candidate.statement) == new_values
        ]
        if not suspects:
            continue
        strict = [
            candidate
            for candidate in suspects
            if new_period and new_period <= _period(candidate.statement, candidate.as_of_year)
        ]
        if len(strict) >= 2:
            decisions.append(
                _entry(
                    claim,
                    action="kept",
                    target=None,
                    reason=(
                        "ambiguous value duplicate: "
                        f"{len(strict)} candidates share type, values and period "
                        f"({', '.join(candidate.claim_id for candidate in strict)}) — "
                        "the op is left as proposed"
                    ),
                )
            )
            continue
        if len(strict) == 1:
            target = strict[0]
            if claim.has_counter_link:
                decisions.append(
                    _entry(
                        claim,
                        action="kept",
                        target=None,
                        reason=(
                            f"value duplicate of {target.claim_id}, but the op carries "
                            "counterevidence links — a dispute stays a dispute, the "
                            "candidate is not touched"
                        ),
                    )
                )
                continue
            if not claim.has_support_link:
                decisions.append(
                    _entry(
                        claim,
                        action="kept",
                        target=None,
                        reason=(
                            f"value duplicate of {target.claim_id}, but the op has no "
                            "supporting evidence — nothing new to add to the candidate"
                        ),
                    )
                )
                continue
            decisions.append(
                _entry(
                    claim,
                    action="reverified",
                    target=target.claim_id,
                    reason=(
                        f"value duplicate of {target.claim_id}: same claim type "
                        f"({claim.claim_type}) and same value set "
                        f"{sorted(new_values)}; period {sorted(new_period)} is covered by "
                        "the candidate — converted to a reverify so the value is written once"
                    ),
                )
            )
            continue
        decisions.append(
            _entry(
                claim,
                action="kept",
                target=None,
                reason=(
                    f"value {sorted(new_values)} matches candidate(s) "
                    f"{', '.join(candidate.claim_id for candidate in suspects)} by type and "
                    f"values, but the period {sorted(new_period)} is not covered — left as proposed"
                ),
            )
        )
    return decisions
