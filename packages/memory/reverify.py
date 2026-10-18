"""T7.73 (ADR-0018, уточнение): reference date and scope of a REVERIFY or
a dedup-REUSE of an EXISTING claim.

The reference date of a claim is host-derived on every commit
(ADR-0016 §4, T7.30): ``derive_claim_as_of`` takes the date the question
names, else the session's date for a relative question, else the model's
typed ``as_of``. For a NEW claim that is the whole rule. For an operation
that attaches to an EXISTING claim (reverify by ``existing_claim_id``, or
the T7.9 dedup reuse) applying it blindly was the T7.73 defect found on
the dev stand: a checking session whose question names no date and whose
proposal carries ``as_of: null`` (exactly what curator-v7 rule 7 and
ADR-0018 §"Операция" teach) produced ``ClaimAsOf(None, NONE)``, the claim
row's established reference date was overwritten with NULL, and the rules
engine — correctly — reported ``as_of_missing`` and cut an E3 supported
claim down to E1 hypothesis. The badge dropped while the evidence base
grew: the downgrade had no evidentiary reason at all.

The rule this module implements (ADR-0018 уточнение T7.73):

1. **The session carries an anchor → the anchor re-derives.** When the
   reverifying question names a date explicitly or anchors it relatively
   («сейчас», «на текущую дату»), the prescribed behaviour stands:
   ADR-0016 §4 re-derivation, and with it ADR-0017 — a confirmed
   reverify of a relative claim is a new verification moment, so its
   deadline shifts. Such a move is recorded in the commit audit
   (``anchor_date_changed``) — never silent.
2. **Nothing new is carried → the anchor's own date stands.** A session
   that brings no date does not get to erase one: the existing ``as_of``
   is kept TOGETHER WITH its persisted ``date_anchor`` (from the current
   head's assessed scope, ``anchor_from_scope``) — so a relative claim
   does not silently become evergreen and keeps getting a refreshed
   deadline, while an explicit/none claim stays deadline-less.
3. **The proposal carries a DIFFERENT non-empty date → no substitution.**
   ADR-0018: the value of an existing claim is the anchor's; changing it
   is a revision (or counterevidence + a new claim), not a reverify. The
   stored date stands and the rejected value is recorded in the audit
   (``as_of_conflict``) — visible, reviewable, not applied. A proposal
   date that fills a gap (the anchor had none) is applied normally.

The reverify SCOPE merges, for the same honesty reason: the claim scope's
declared source domains are the UNION of what the previous head declared
and what this question names. Keeping only the new question's domains
would make the anchor's own older evidence fail ``_canonical_covers`` and
reproduce the same dishonest downgrade through a different door.

Pure module: no DB, no clock (the caller passes the session date), one
call site per branch in ``packages/memory/service.py``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import ClaimDateAnchor
from packages.memory.scope import (
    CLAIM_AS_OF_KEY,
    CLAIM_SOURCE_DOMAINS_KEY,
    DATE_ANCHOR_KEY,
    anchor_from_scope,
    derive_claim_as_of,
)


@dataclass(frozen=True)
class ReverifyReference:
    """What the reference date of an EXISTING claim becomes after this
    reverify/reuse commit — and why (the audit note is derived from it).

    - ``carried_existing`` — the anchor's own date was kept because the
      session carried nothing to replace it;
    - ``proposed_conflict`` — a non-empty proposal date that differs from
      the anchor's date and therefore was NOT applied (ADR-0018: a value
      change is a revision, not a reverify);
    - ``changed_from`` — the previous date when the session DID carry an
      anchor and legitimately moved it (recorded in the audit).
    """

    as_of: datetime | None
    anchor: ClaimDateAnchor
    carried_existing: bool = False
    proposed_conflict: datetime | None = None
    changed_from: datetime | None = None


def _utc(value: datetime) -> datetime:
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _iso(value: datetime | None) -> str | None:
    return _utc(value).isoformat() if value is not None else None


def resolve_reverify_reference(
    *,
    question: str | None,
    proposal_as_of: datetime | None,
    session_date: date | None,
    existing_as_of: datetime | None,
    existing_scope: JsonDict | None,
) -> ReverifyReference:
    """The reference date (and its anchor) of an existing claim after a
    reverify/reuse commit. See the module docstring for the three cases.

    ``session_date`` is the session's start date on the host clock — the
    same value ``derive_claim_as_of``/``derive_claim_scope`` are given
    (T7.18, ADR-0007); it is passed in, never read from a clock here.
    """
    derived = derive_claim_as_of(
        question=question, as_of=proposal_as_of, session_date=session_date
    )
    stored = _utc(existing_as_of) if existing_as_of is not None else None

    if derived.anchor in (ClaimDateAnchor.EXPLICIT, ClaimDateAnchor.RELATIVE):
        # Case 1: the session itself carries a date anchor — the prescribed
        # re-derivation (ADR-0016 §4), with the ADR-0017 freshness shift.
        moved = derived.as_of.date() if derived.as_of is not None else None
        return ReverifyReference(
            as_of=derived.as_of,
            anchor=derived.anchor,
            changed_from=stored if stored is not None and stored.date() != moved else None,
        )

    if derived.as_of is not None:
        # Case 3: a dateless question, the proposal carries a date.
        if stored is None:
            return ReverifyReference(as_of=derived.as_of, anchor=derived.anchor)
        if stored.date() == derived.as_of.date():
            # The same reference day: the anchor's own value stands
            # (the exact instant it was established with).
            return ReverifyReference(
                as_of=stored,
                anchor=anchor_from_scope(existing_scope or {}),
                carried_existing=True,
            )
        return ReverifyReference(
            as_of=stored,
            anchor=anchor_from_scope(existing_scope or {}),
            carried_existing=True,
            proposed_conflict=derived.as_of,
        )

    # Case 2: neither the question nor the proposal carries a date — the
    # anchor's established reference date (and its persisted anchor kind)
    # is kept. An anchor with no date at all stays without one.
    if stored is not None:
        return ReverifyReference(
            as_of=stored,
            anchor=anchor_from_scope(existing_scope or {}),
            carried_existing=True,
        )
    return ReverifyReference(as_of=None, anchor=derived.anchor)


def merge_reverify_scope(
    *,
    question_scope: JsonDict,
    existing_scope: JsonDict | None,
    reference: ReverifyReference,
) -> JsonDict:
    """The claim scope a reverify/reuse commit stores: the host-derived
    scope of THIS question (T7.17/T7.18 — the model's free-form scope is
    never used), with the resolved reference date/anchor overlaid and the
    source domains UNIONED with what the anchor's head declared."""
    scope = dict(question_scope)
    scope[CLAIM_AS_OF_KEY] = (
        reference.as_of.date().isoformat() if reference.as_of is not None else None
    )
    scope[DATE_ANCHOR_KEY] = reference.anchor.value
    domains: list[str] = [str(d) for d in (scope.get(CLAIM_SOURCE_DOMAINS_KEY) or []) if str(d)]
    for domain in (existing_scope or {}).get(CLAIM_SOURCE_DOMAINS_KEY) or []:
        text = str(domain)
        if text and text not in domains:
            domains.append(text)
    # sorted, as `derive_claim_scope` sorts them: the merged scope must not
    # depend on which session happened to be the last one to touch the claim
    scope[CLAIM_SOURCE_DOMAINS_KEY] = sorted(set(domains))
    return scope


def reverify_as_of_audit(
    reference: ReverifyReference, *, existing_as_of: datetime | None
) -> JsonDict:
    """The additive audit note of the ``claim_reverified`` event: what the
    anchor's date was, whether it was kept, and every value this commit
    refused to substitute or did move. Every change is named — an
    assessment downgrade must be traceable to a real reason."""
    note: JsonDict = {
        "anchor_kept": reference.carried_existing,
        "stored_as_of": _iso(existing_as_of),
    }
    if reference.proposed_conflict is not None:
        note["as_of_conflict"] = _iso(reference.proposed_conflict)
    if reference.changed_from is not None and reference.as_of is not None:
        note["anchor_date_changed"] = {
            "from": _iso(reference.changed_from),
            "to": _iso(reference.as_of),
        }
    return note
