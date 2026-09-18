"""Claim/evidence scope — derived by the trusted host (T7.17, §3.7,
§8.7, §11.2).

The scope the rules engine checks is NOT the model's free-form dict.
The model proposes a claim statement (and a free-form scope hint); the
TRUSTED HOST derives:

- the CLAIM scope from the session's QUESTION — the operator-trusted
  input: the reference date it names and the sources it names — and
  the claim's typed ``as_of``;
- each EVIDENCE scope from its PROVENANCE: the registrable domain and
  the retrieval time of the durable ``sources`` row, or the
  observation time for non-source evidence.

The model's free-form scope stays in the staging payload and the audit
(the proposal, untrusted) and can neither satisfy nor fail coverage.
This is the T7.15 finding: with a free-form curator the claim and the
evidence got DIFFERENT keys for the same dimension (``регион`` vs
``область``, ``на дату`` vs ``as_of``), the key-by-key coverage never
matched, and external/temporal claims stalled at E1 unless the
QUESTION itself dictated the scope object verbatim — a hint the frozen
corpus (50 questions) does not contain and cannot get.

Canonical scope shape (``host-scope-v1``):

    claim scope:    {"scope_schema": "host-scope-v1",
                     "as_of": "2026-01-01" | None,
                     "source_domains": ["example.org", ...]}
    evidence scope: {"scope_schema": "host-scope-v1",
                     "as_of": "2026-09-16T13:49:14+00:00" | None,
                     "source_domain": "example.org" | None}

Coverage (deterministic, fail-closed):

- the claim's reference date ``D``: the evidence must have been
  OBSERVED at or after ``D`` — a source retrieved before the reference
  date cannot speak about that date (a source retrieved at or after it
  is the best available evidence about the state at ``D`` — the
  corpus convention for "по состоянию на" questions over stable facts);
- the claim's source domains (named in the question): the evidence's
  registrable domain must be among them — the question's source scope,
  the same granularity as the §11.3 source-independence groups.

Legacy scopes (pre-``host-scope-v1`` — the model's free-form dicts the
old engine stored on claims/evidence/assessments) are evaluated with
the ORIGINAL key-by-key predicate, unchanged: an engine upgrade must
never silently re-grade existing knowledge in either direction.
"""

from __future__ import annotations

import re
from datetime import UTC, date, datetime

from packages.domain.models.base import JsonDict
from packages.memory.independence import registrable_domain

#: the marker of a host-derived (canonical) scope; stored on the
#: claim/evidence scope dicts and the assessment's assessed_scope
SCOPE_SCHEMA_KEY = "scope_schema"
SCOPE_SCHEMA = "host-scope-v1"

#: the claim scope's reference date (ISO "YYYY-MM-DD", UTC)
CLAIM_AS_OF_KEY = "as_of"
#: the claim scope's source domains named in the question (registrable)
CLAIM_SOURCE_DOMAINS_KEY = "source_domains"
#: the evidence scope's observation time (ISO-8601, UTC)
EVIDENCE_AS_OF_KEY = "as_of"
#: the evidence scope's source's registrable domain
EVIDENCE_SOURCE_DOMAIN_KEY = "source_domain"

_RU_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}
_EN_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}


def _month_pattern(months: dict[str, int]) -> tuple[re.Pattern[str], dict[str, int]]:
    return (
        re.compile(r"(\d{1,2})\s+(" + "|".join(months) + r")\s+(\d{4})", re.IGNORECASE),
        months,
    )


_RU_DAY_MONTH_YEAR = _month_pattern(_RU_MONTHS)
_EN_DAY_MONTH_YEAR = _month_pattern(_EN_MONTHS)
_DMY = re.compile(r"(\d{1,2})[.](\d{1,2})[.](\d{4})\b")
_YMD = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

#: a date form: (pattern, "named" | "dmy" | "ymd", month table)
_DATE_FORMS: tuple[tuple[re.Pattern[str], str, dict[str, int]], ...] = (
    (_RU_DAY_MONTH_YEAR[0], "named", _RU_DAY_MONTH_YEAR[1]),
    (_EN_DAY_MONTH_YEAR[0], "named", _EN_DAY_MONTH_YEAR[1]),
    (_DMY, "dmy", {}),
    (_YMD, "ymd", {}),
)

_URL_RE = re.compile(r"https?://[^\s\"'<>)\]]+", re.IGNORECASE)


def is_host_derived(scope: JsonDict) -> bool:
    """True when the scope dict was derived by the trusted host
    (carries the ``host-scope-v1`` marker) — not the model's free-form
    proposal."""
    return scope.get(SCOPE_SCHEMA_KEY) == SCOPE_SCHEMA


def parse_question_date(text: str) -> date | None:
    """The reference date named in a question (deterministic, closed
    pattern set: Russian/English month names, ``d.m.y``, ISO ``y-m-d``).
    The leftmost valid match wins; invalid calendar dates are skipped.
    No date → None (the claim's typed as_of stands)."""
    best: tuple[int, date] | None = None
    for pattern, form, months in _DATE_FORMS:
        for m in pattern.finditer(text):
            d = _date_from_match(m, form, months)
            if d is not None and (best is None or m.start() < best[0]):
                best = (m.start(), d)
    return best[1] if best is not None else None


def _date_from_match(m: re.Match[str], form: str, months: dict[str, int]) -> date | None:
    parts = m.groups()
    try:
        if form == "named":
            day, month, year = int(parts[0]), months[parts[1].lower()], int(parts[2])
        elif form == "dmy":
            day, month, year = int(parts[0]), int(parts[1]), int(parts[2])
        else:  # ymd
            year, month, day = int(parts[0]), int(parts[1]), int(parts[2])
        if not 1000 <= year <= 9999:
            return None
        return date(year, month, day)
    except (ValueError, KeyError):
        return None


def extract_question_urls(text: str) -> tuple[str, ...]:
    """The http(s) URLs named in a question (deduplicated, order kept).
    Trailing punctuation is stripped."""
    urls: list[str] = []
    for m in _URL_RE.finditer(text):
        url = m.group(0).rstrip(".,;:!?")
        if url not in urls:
            urls.append(url)
    return tuple(urls)


def derive_claim_scope(
    *,
    question: str | None,
    as_of: datetime | None,
) -> JsonDict:
    """The canonical scope of one claim (host-scope-v1).

    - ``as_of``: the reference date the QUESTION names (the operator's
      input, trusted) — otherwise the claim's typed ``as_of``
      (host-validated, structured);
    - ``source_domains``: the registrable domains of the sources the
      question names (empty = the question names no sources — no source
      constraint).
    """
    day: date | None = None
    if question:
        day = parse_question_date(question)
    if day is None and as_of is not None:
        day = as_of.date() if as_of.tzinfo is None else as_of.astimezone(UTC).date()
    domains: list[str] = []
    if question:
        for url in extract_question_urls(question):
            domain = registrable_domain(url)
            if domain and domain not in domains:
                domains.append(domain)
    return {
        SCOPE_SCHEMA_KEY: SCOPE_SCHEMA,
        CLAIM_AS_OF_KEY: day.isoformat() if day is not None else None,
        CLAIM_SOURCE_DOMAINS_KEY: domains,
    }


def derive_evidence_scope(
    *,
    source_domain: str | None,
    observed_at: datetime | None,
) -> JsonDict:
    """The canonical scope of one evidence row (host-scope-v1) from its
    provenance: the registrable domain of the durable source and the
    observation time (the source's retrieval time; the commit time for
    non-source evidence)."""
    at: str | None = None
    if observed_at is not None:
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=UTC)
        at = observed_at.astimezone(UTC).isoformat()
    return {
        SCOPE_SCHEMA_KEY: SCOPE_SCHEMA,
        EVIDENCE_AS_OF_KEY: at,
        EVIDENCE_SOURCE_DOMAIN_KEY: source_domain,
    }


def scope_covers(evidence_scope: JsonDict, claim_scope: JsonDict) -> bool:
    """The rules engine's coverage predicate (§8.7 "предикат покрытия
    claim scope"), keyed on the CLAIM scope's origin:

    - host-derived claim scope → the canonical check over the
      host-derived dimensions (the evidence scope is checked by CONTENT:
      a legacy free-form evidence scope simply lacks the dimensions and
      fails closed);
    - any other claim scope (legacy, the model's free-form dict) → the
      ORIGINAL key-by-key predicate, unchanged.
    """
    if is_host_derived(claim_scope):
        return _canonical_covers(evidence_scope, claim_scope)
    return legacy_scope_covers(evidence_scope, claim_scope)


def _canonical_covers(evidence_scope: JsonDict, claim_scope: JsonDict) -> bool:
    """Fail-closed coverage of a host-derived claim scope.

    Every canonical dimension the claim declares must be covered by the
    evidence; a dimension the evidence cannot show (no observation
    time, no source domain) is NOT covered — never a silent pass."""
    claim_day = _parse_claim_date(claim_scope.get(CLAIM_AS_OF_KEY))
    if claim_day is not None:
        evidence_at = _parse_evidence_time(evidence_scope.get(EVIDENCE_AS_OF_KEY))
        if evidence_at is None:
            return False
        if evidence_at < claim_day:
            return False
    domains = claim_scope.get(CLAIM_SOURCE_DOMAINS_KEY)
    if domains:
        domain = evidence_scope.get(EVIDENCE_SOURCE_DOMAIN_KEY)
        if domain is None or domain not in domains:
            return False
    return True


def legacy_scope_covers(evidence_scope: JsonDict, claim_scope: JsonDict) -> bool:
    """The ORIGINAL (pre-T7.17) key-by-key predicate over the model's
    free-form scopes: every claim key is present in the evidence scope
    with a compatible (equal-or-unset) value. Kept for legacy rows so
    that an engine upgrade never re-grades existing knowledge."""
    for key, value in claim_scope.items():
        if key not in evidence_scope:
            return False
        if value is not None and evidence_scope[key] not in (None, value):
            return False
    return True


def _parse_claim_date(value: object) -> datetime | None:
    """The claim's reference date as midnight UTC (the temporal
    comparison anchor)."""
    if not isinstance(value, str) or not value:
        return None
    try:
        d = date.fromisoformat(value)
    except ValueError:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=UTC)


def _parse_evidence_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        at = datetime.fromisoformat(value)
    except ValueError:
        return None
    if at.tzinfo is None:
        at = at.replace(tzinfo=UTC)
    return at.astimezone(UTC)
