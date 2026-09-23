"""Unit: host-derived claim/evidence scope (T7.17, §3.7, §8.7, §11.2).

The pure module ``packages.memory.scope``: the trusted host derives the
claim scope from the QUESTION (reference date + named sources) and each
evidence scope from its PROVENANCE; the rules engine's coverage
predicate compares the canonical dimensions — never the model's
free-form keys. The legacy (model free-form) scopes keep the original
key-by-key predicate."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from packages.domain.models.enums import ClaimDateAnchor
from packages.memory.scope import (
    DATE_ANCHOR_KEY,
    SCOPE_SCHEMA,
    anchor_from_scope,
    derive_claim_as_of,
    derive_claim_scope,
    derive_evidence_scope,
    extract_question_urls,
    is_host_derived,
    legacy_scope_covers,
    parse_question_date,
    question_uses_relative_date,
    scope_covers,
)

QUESTION_UN = (
    "По состоянию на 15 апреля 2026 года: сколько государств-членов "
    "входило в ООН? Ответь строго по этим двум источникам: "
    "https://un.org/en/about-us и "
    "https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН"
)
#: relative-form question (T7.18): «на текущую дату» — the reference
#: date is the SESSION's date on the host's clock, never the model's as_of
QUESTION_EU_RELATIVE = (
    "Сколько стран-членов в Европейском союзе на текущую дату? "
    "Источники: https://en.wikipedia.org/wiki/European_Union и "
    "https://european-union.europa.eu/principles-countries-history/"
    "facts-and-figures-european-union_en"
)
#: no date anchor at all (neither explicit nor relative) — the claim's
#: typed as_of stands (ADR-0007, уточнение T7.18)
QUESTION_EU_TRUE_NO_DATE = (
    "Сколько стран-членов в Европейском союзе? "
    "Источники: https://en.wikipedia.org/wiki/European_Union и "
    "https://european-union.europa.eu/principles-countries-history/"
    "facts-and-figures-european-union_en"
)


# ── date parsing ───────────────────────────────────────────────────────


def test_parse_question_date_russian_month_form() -> None:
    assert parse_question_date(QUESTION_UN) == date(2026, 4, 15)
    assert (
        parse_question_date("По состоянию на 1 января 2026 года: сколько стран в ЕС?")
        == date(2026, 1, 1)
    )
    assert parse_question_date("15 апреля 2026") == date(2026, 4, 15)


def test_parse_question_date_other_forms() -> None:
    assert parse_question_date("as of 2026-04-15 how many?") == date(2026, 4, 15)
    assert parse_question_date("на 15.04.2026 сколько?") == date(2026, 4, 15)
    assert parse_question_date("15 April 2026") == date(2026, 4, 15)


def test_parse_question_date_no_date() -> None:
    # the EXPLICIT parser does not see relative forms («на текущую дату»
    # is not a fixed date) — that is a T7.18 relative anchor, handled in
    # question_uses_relative_date / derive_claim_scope
    assert parse_question_date(QUESTION_EU_RELATIVE) is None
    assert parse_question_date("Сколько стран в ЕС на текущую дату?") is None
    assert parse_question_date(QUESTION_EU_TRUE_NO_DATE) is None
    assert parse_question_date("") is None


def test_question_uses_relative_date_closed_set() -> None:
    """T7.18: the closed deterministic set of relative reference-date
    forms. The corpus (question-set-v2.jsonl) actually uses «на текущую
    дату» and «сейчас»; the rest of the closed set is covered too."""
    relative = [
        "Какова ключевая ставка Банка России на текущую дату?",
        "Сколько стран-членов в Европейском союзе на текущую дату?",
        "Сколько стран в ЕС на сегодня?",
        "Сколько стран в ЕС сейчас?",
        "Верно ли, что сейчас в Европейском союзе 27 стран-членов?",
        "Какая версия Python сейчас последняя стабильная?",
        "На текущий момент сколько стран в ЕС?",
        "Какая текущая версия Python?",
        "How many EU members as of the current date?",
        "How many EU members as of today?",
        "How many members are currently in the EU?",
    ]
    for q in relative:
        assert question_uses_relative_date(q), q
    # an EXPLICIT date (no relative form) is not a relative anchor
    assert not question_uses_relative_date(
        "По состоянию на 15 апреля 2026 года: сколько стран в ЕС?"
    )
    # no date at all is not a relative anchor
    assert not question_uses_relative_date("Сколько планет в Солнечной системе?")
    assert not question_uses_relative_date(QUESTION_EU_TRUE_NO_DATE)
    assert not question_uses_relative_date("")


def test_parse_question_date_invalid_calendar_and_leftmost() -> None:
    assert parse_question_date("31 февраля 2026") is None
    # the leftmost valid match wins (the reference date precedes the body)
    assert parse_question_date("15 апреля 2026 и 1.1.2020") == date(2026, 4, 15)
    assert parse_question_date("1.1.2020, потом 15 апреля 2026") == date(2020, 1, 1)


# ── URL extraction ─────────────────────────────────────────────────────


def test_extract_question_urls() -> None:
    assert extract_question_urls(QUESTION_UN) == (
        "https://un.org/en/about-us",
        "https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН",
    )
    # deduplicated, order kept, trailing punctuation stripped
    assert extract_question_urls("см https://a.example/x. и https://a.example/x") == (
        "https://a.example/x",
    )
    assert extract_question_urls("без ссылок") == ()


# ── claim scope derivation ─────────────────────────────────────────────


def test_derive_claim_scope_from_question() -> None:
    scope = derive_claim_scope(question=QUESTION_UN, as_of=None)
    assert is_host_derived(scope)
    assert scope["scope_schema"] == SCOPE_SCHEMA
    assert scope["as_of"] == "2026-04-15"
    # registrable domains (ru.wikipedia.org → wikipedia.org)
    assert scope["source_domains"] == ["un.org", "wikipedia.org"]


def test_derive_claim_scope_question_date_wins_over_model_as_of() -> None:
    # the operator's question date is the trusted anchor: the model's
    # typed as_of cannot shift the reference date
    scope = derive_claim_scope(
        question=QUESTION_UN,
        as_of=datetime(2026, 5, 1, 12, 0, tzinfo=UTC),
    )
    assert scope["as_of"] == "2026-04-15"


def test_derive_claim_scope_model_as_of_without_question_date() -> None:
    # ADR-0007 уточнение (T7.18): a question with NO date anchor at all
    # (neither explicit nor relative) keeps the claim's typed as_of — a
    # host-validated structure, not free-form
    scope = derive_claim_scope(
        question=QUESTION_EU_TRUE_NO_DATE,
        as_of=datetime(2026, 9, 17, 13, 0, tzinfo=UTC),
    )
    assert scope["as_of"] == "2026-09-17"
    assert scope["source_domains"] == ["wikipedia.org", "europa.eu"]


def test_derive_claim_scope_relative_date_uses_session_date() -> None:
    """T7.18 (the defect fix): a relative-form question («на текущую
    дату») anchors the reference date to the SESSION's date on the
    host's clock. The model's typed as_of — in the FUTURE or in the
    PAST — cannot shift the reference date either way."""
    # model says TOMORROW → the reference stays the session date
    scope = derive_claim_scope(
        question=QUESTION_EU_RELATIVE,
        as_of=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
        session_date=date(2026, 9, 18),
    )
    assert scope["as_of"] == "2026-09-18"
    assert scope["source_domains"] == ["wikipedia.org", "europa.eu"]
    # model says the PAST → the reference still stays the session date
    scope_past = derive_claim_scope(
        question=QUESTION_EU_RELATIVE,
        as_of=datetime(2026, 9, 1, 12, 0, tzinfo=UTC),
        session_date=date(2026, 9, 18),
    )
    assert scope_past["as_of"] == "2026-09-18"


def test_derive_claim_scope_explicit_date_wins_over_relative_and_session() -> None:
    # an EXPLICIT date in the question is primary: it beats both the
    # relative form and the session date
    q = "По состоянию на 15 апреля 2026, сейчас сколько стран в ЕС?"
    scope = derive_claim_scope(
        question=q,
        as_of=datetime(2026, 9, 19, tzinfo=UTC),
        session_date=date(2026, 9, 18),
    )
    assert scope["as_of"] == "2026-04-15"


def test_derive_claim_scope_no_question_no_as_of() -> None:
    scope = derive_claim_scope(question=None, as_of=None)
    assert is_host_derived(scope)
    assert scope["as_of"] is None
    assert scope["source_domains"] == []


def test_derive_claim_scope_naive_as_of_treated_as_utc() -> None:
    scope = derive_claim_scope(
        question=None,
        as_of=datetime(2026, 9, 17, 5, 0),  # naive
    )
    assert scope["as_of"] == "2026-09-17"


# ── T7.30 (ADR-0016): the claim row's reference datetime ───────────────


def test_derive_claim_as_of_explicit_date_wins() -> None:
    # priority 1: the explicit question date beats BOTH the model's
    # typed as_of and the session date; the ANCHOR is explicit (T7.32,
    # ADR-0017: a claim about a fixed point — no reverify deadline)
    ref = derive_claim_as_of(
        question="По состоянию на 15 апреля 2026 года: сколько стран в ЕС?",
        as_of=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        session_date=date(2026, 9, 22),
    )
    assert ref.as_of == datetime(2026, 4, 15, tzinfo=UTC)
    assert ref.anchor is ClaimDateAnchor.EXPLICIT


def test_derive_claim_as_of_relative_form_uses_session_date() -> None:
    # priority 2: a relative form anchors on the SESSION's date on the
    # host's clock (midnight UTC) — the model's typed as_of (in the
    # future OR the past) does not shift it; the ANCHOR is relative
    # (T7.32, ADR-0017: a claim about the present — the only claim
    # class that gets a reverify deadline)
    session_date = date(2026, 9, 22)
    for model_as_of in (
        datetime(2026, 9, 23, 12, 0, tzinfo=UTC),  # tomorrow
        datetime(2026, 6, 15, 0, 0, tzinfo=UTC),  # the EVAL-4d artifact
    ):
        ref = derive_claim_as_of(
            question="Какова ключевая ставка Банка России на текущую дату?",
            as_of=model_as_of,
            session_date=session_date,
        )
        assert ref.as_of == datetime(2026, 9, 22, tzinfo=UTC)
        assert ref.anchor is ClaimDateAnchor.RELATIVE


def test_derive_claim_as_of_relative_form_without_session_date_falls_back() -> None:
    # fail-closed: a relative form with NO session date available
    # (session_date=None) has no host anchor — the model's typed as_of
    # stands (the same fallback the scope has since T7.18); the branch
    # that PRODUCED the value is the model fallback, so the ANCHOR is
    # none (T7.32, ADR-0017: no deadline — never a deadline computed
    # from a value the branch does not own)
    ref = derive_claim_as_of(
        question="Какова ключевая ставка Банка России на текущую дату?",
        as_of=datetime(2026, 6, 15, tzinfo=UTC),
        session_date=None,
    )
    assert ref.as_of == datetime(2026, 6, 15, tzinfo=UTC)
    assert ref.anchor is ClaimDateAnchor.NONE


def test_derive_claim_as_of_dateless_question_keeps_model_as_of() -> None:
    # priority 3: a question with NO date anchor (neither explicit nor
    # relative) keeps the model's typed as_of — the Sputnik-1 / release
    # event date (ADR-0016, fallback unchanged from T7.17/T7.18); the
    # ANCHOR is none (T7.32, ADR-0017: a fixed point — no reverify
    # deadline, the "forever overdue" class is gone)
    ref = derive_claim_as_of(
        question="Когда был запущен Спутник-1?",
        as_of=datetime(1957, 10, 4, 19, 28, 34, tzinfo=UTC),
        session_date=date(2026, 9, 22),
    )
    assert ref.as_of == datetime(1957, 10, 4, 19, 28, 34, tzinfo=UTC)
    assert ref.anchor is ClaimDateAnchor.NONE
    # naive model as_of is treated as UTC
    ref_naive = derive_claim_as_of(
        question="Когда был запущен Спутник-1?",
        as_of=datetime(1957, 10, 4, 19, 28, 34),
        session_date=date(2026, 9, 22),
    )
    assert ref_naive.as_of == datetime(1957, 10, 4, 19, 28, 34, tzinfo=UTC)
    assert ref_naive.anchor is ClaimDateAnchor.NONE


def test_derive_claim_as_of_no_question_no_as_of_is_none() -> None:
    # no question at all (no anchor to derive from) and no model
    # as_of: the reference datetime is None and the ANCHOR is none
    ref = derive_claim_as_of(question=None, as_of=None, session_date=date(2026, 9, 22))
    assert ref.as_of is None
    assert ref.anchor is ClaimDateAnchor.NONE


def test_derive_claim_scope_date_is_the_reference_datetime_date_part() -> None:
    # one function, one source of truth (T7.30): the claim scope's
    # reference date is the DATE part of derive_claim_as_of — for all
    # three priorities, explicit / relative / dateless fallback
    model = datetime(2026, 6, 15, tzinfo=UTC)
    session_date = date(2026, 9, 22)
    for question in (
        "По состоянию на 15 апреля 2026 года: сколько стран в ЕС?",
        "Какова ключевая ставка Банка России на текущую дату?",
        "Когда был запущен Спутник-1?",
    ):
        ref = derive_claim_as_of(
            question=question, as_of=model, session_date=session_date
        )
        scope = derive_claim_scope(
            question=question, as_of=model, session_date=session_date
        )
        assert scope["as_of"] == ref.as_of.date().isoformat()


def test_derive_claim_scope_carries_the_date_anchor() -> None:
    # T7.32 (ADR-0017): the claim scope persists the ANCHOR of the
    # branch of derive_claim_as_of (date_anchor) — the write paths
    # (commit, reassessment) derive the reverify deadline from it.
    # One function, one source of truth: the scope's anchor is the
    # same value the direct call returns — no re-derivation.
    model = datetime(2026, 6, 15, tzinfo=UTC)
    session_date = date(2026, 9, 22)
    cases = (
        (
            "По состоянию на 15 апреля 2026 года: сколько стран в ЕС?",
            ClaimDateAnchor.EXPLICIT,
        ),
        ("Какова ключевая ставка Банка России на текущую дату?", ClaimDateAnchor.RELATIVE),
        ("Когда был запущен Спутник-1?", ClaimDateAnchor.NONE),
    )
    for question, expected_anchor in cases:
        ref = derive_claim_as_of(
            question=question, as_of=model, session_date=session_date
        )
        scope = derive_claim_scope(
            question=question, as_of=model, session_date=session_date
        )
        assert ref.anchor is expected_anchor
        assert scope[DATE_ANCHOR_KEY] == expected_anchor.value


def test_anchor_from_scope_reads_stored_anchor() -> None:
    # T7.32 (ADR-0017): the persistence seam — a stored host scope
    # carries the anchor verbatim
    for anchor in ClaimDateAnchor:
        scope = derive_claim_scope(
            question="Какова ключевая ставка Банка России на текущую дату?",
            as_of=datetime(2026, 6, 15, tzinfo=UTC),
            session_date=date(2026, 9, 22),
        )
        scope[DATE_ANCHOR_KEY] = anchor.value
        assert anchor_from_scope(scope) is anchor
    # a scope with a GARBAGE value is not trusted — the conservative
    # default applies (a deadline is kept, never "valid forever" on
    # the strength of bad data)
    assert anchor_from_scope({"date_anchor": "garbage"}) is ClaimDateAnchor.RELATIVE


def test_anchor_from_scope_legacy_scope_defaults_to_relative() -> None:
    # T7.32 (ADR-0017): a LEGACY scope (pre-ADR-0017, no date_anchor
    # key — including the empty scope of a claim with no stored
    # assessment) is treated as relative — the conservative default:
    # the deadline is kept/refreshed rather than the claim declared
    # valid forever on missing data
    assert anchor_from_scope({}) is ClaimDateAnchor.RELATIVE
    assert anchor_from_scope({"x": 1}) is ClaimDateAnchor.RELATIVE
    assert (
        anchor_from_scope(
            {"scope_schema": "host-scope-v1", "as_of": "2026-01-01", "source_domains": []}
        )
        is ClaimDateAnchor.RELATIVE
    )


# ── evidence scope derivation ──────────────────────────────────────────


def test_derive_evidence_scope_from_provenance() -> None:
    scope = derive_evidence_scope(
        source_domain="un.org",
        observed_at=datetime(2026, 9, 17, 13, 49, 14, tzinfo=UTC),
    )
    assert is_host_derived(scope)
    assert scope["as_of"] == "2026-09-17T13:49:14+00:00"
    assert scope["source_domain"] == "un.org"


def test_derive_evidence_scope_normalizes_timezone() -> None:
    # a +03:00 retrieval time is stored in UTC
    scope = derive_evidence_scope(
        source_domain=None,
        observed_at=datetime(2026, 9, 17, 16, 0, 0, tzinfo=timezone(timedelta(hours=3))),
    )
    assert scope["as_of"] == "2026-09-17T13:00:00+00:00"
    assert scope["source_domain"] is None


def test_derive_evidence_scope_no_observation_time() -> None:
    scope = derive_evidence_scope(source_domain=None, observed_at=None)
    assert scope["as_of"] is None
    assert scope["source_domain"] is None


# ── coverage: canonical (host-derived) scopes ──────────────────────────


def _claim_scope(**kw) -> dict:
    base = {
        "question": None,
        "as_of": None,
    }
    base.update(kw)
    return derive_claim_scope(**base)


def _evidence_scope(**kw) -> dict:
    base = {
        "source_domain": None,
        "observed_at": None,
    }
    base.update(kw)
    return derive_evidence_scope(**base)


def test_canonical_coverage_same_subject_and_date() -> None:
    # the required case (T7.17 test 1): claim and evidence about the
    # SAME subject (domain) and date — covered, whatever keys the model
    # invented elsewhere
    claim = _claim_scope(question=QUESTION_UN)
    evidence = _evidence_scope(
        source_domain="un.org",
        observed_at=datetime(2026, 9, 17, 13, 49, tzinfo=UTC),
    )
    assert scope_covers(evidence, claim)


def test_canonical_coverage_other_named_source_also_covers() -> None:
    claim = _claim_scope(question=QUESTION_UN)
    evidence = _evidence_scope(
        source_domain="wikipedia.org",
        observed_at=datetime(2026, 9, 17, 13, 49, tzinfo=UTC),
    )
    assert scope_covers(evidence, claim)


def test_canonical_coverage_different_subject_fails_closed() -> None:
    # the required fail-closed case (T7.17 test 2): the evidence's
    # source is NOT among the question's named sources — a different
    # subject; the grade must not be lifted
    claim = _claim_scope(question=QUESTION_UN)
    evidence = _evidence_scope(
        source_domain="other.example",
        observed_at=datetime(2026, 9, 17, 13, 49, tzinfo=UTC),
    )
    assert not scope_covers(evidence, claim)


def test_canonical_coverage_evidence_without_source_fails_closed() -> None:
    claim = _claim_scope(question=QUESTION_UN)
    evidence = _evidence_scope(
        source_domain=None,
        observed_at=datetime(2026, 9, 17, 13, 49, tzinfo=UTC),
    )
    assert not scope_covers(evidence, claim)


def test_canonical_coverage_retrieved_before_as_of_fails_closed() -> None:
    # the required fail-closed case (T7.17 test 2): a source retrieved
    # BEFORE the claim's reference date cannot speak about that date
    claim = _claim_scope(question=QUESTION_UN)  # as_of 2026-04-15
    evidence = _evidence_scope(
        source_domain="un.org",
        observed_at=datetime(2026, 4, 1, 12, 0, tzinfo=UTC),
    )
    assert not scope_covers(evidence, claim)


def test_canonical_coverage_same_day_is_covered() -> None:
    # a source retrieved ON the reference date (any time after
    # midnight UTC) covers it
    claim = _claim_scope(question=QUESTION_UN)  # as_of 2026-04-15
    evidence = _evidence_scope(
        source_domain="un.org",
        observed_at=datetime(2026, 4, 15, 6, 30, tzinfo=UTC),
    )
    assert scope_covers(evidence, claim)


def test_canonical_coverage_missing_observation_time_fails_closed() -> None:
    claim = _claim_scope(question=QUESTION_UN)  # has a date
    evidence = _evidence_scope(source_domain="un.org", observed_at=None)
    assert not scope_covers(evidence, claim)


def test_canonical_coverage_no_dimensions_is_vacuous() -> None:
    # a question naming no date and no sources: no canonical dimension
    # is declared, any evidence with a provenance covers
    claim = _claim_scope(question=None, as_of=None)
    assert scope_covers(
        _evidence_scope(source_domain=None, observed_at=None), claim
    )


def test_canonical_claim_with_legacy_evidence_scope_fails_closed() -> None:
    # a host-derived claim scope cannot be covered by a legacy
    # (model free-form) evidence scope: the dimensions are simply not
    # there — fail closed, never a silent pass
    claim = _claim_scope(question=QUESTION_UN)
    legacy_evidence = {"область": "ООН", "as_of": "2026-04-15"}
    assert not scope_covers(legacy_evidence, claim)


# ── coverage: legacy (model free-form) scopes — unchanged predicate ────


def test_legacy_coverage_key_by_key_is_unchanged() -> None:
    # the original predicate over the model's free-form dicts:
    # different key spellings for the same subject/date did NOT match
    # (the T7.15 defect — preserved for legacy rows, not re-graded)
    assert not legacy_scope_covers(
        {"область": "ЕС", "as_of": "2026-01-01"},
        {"регион": "ЕС", "на дату": "2026-01-01"},
    )
    assert legacy_scope_covers({"expr": "6*7"}, {"expr": "6*7"})
    assert legacy_scope_covers({"expr": "6*7", "extra": 1}, {"expr": "6*7"})
    assert not legacy_scope_covers({"expr": "6*8"}, {"expr": "6*7"})


def test_scope_covers_dispatches_on_claim_scope_origin() -> None:
    legacy_claim = {"регион": "ЕС", "на дату": "2026-01-01"}
    legacy_evidence = {"регион": "ЕС", "на дату": "2026-01-01"}
    assert scope_covers(legacy_evidence, legacy_claim)  # legacy path
    canonical_claim = _claim_scope(question=QUESTION_UN)
    canonical_evidence = _evidence_scope(
        source_domain="un.org",
        observed_at=datetime(2026, 9, 17, tzinfo=UTC),
    )
    assert scope_covers(canonical_evidence, canonical_claim)  # canonical path
    # and the legacy claim is NOT re-graded by the canonical predicate
    assert not scope_covers(canonical_evidence, legacy_claim)
