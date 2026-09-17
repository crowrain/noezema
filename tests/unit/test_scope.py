"""Unit: host-derived claim/evidence scope (T7.17, §3.7, §8.7, §11.2).

The pure module ``packages.memory.scope``: the trusted host derives the
claim scope from the QUESTION (reference date + named sources) and each
evidence scope from its PROVENANCE; the rules engine's coverage
predicate compares the canonical dimensions — never the model's
free-form keys. The legacy (model free-form) scopes keep the original
key-by-key predicate."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

from packages.memory.scope import (
    SCOPE_SCHEMA,
    derive_claim_scope,
    derive_evidence_scope,
    extract_question_urls,
    is_host_derived,
    legacy_scope_covers,
    parse_question_date,
    scope_covers,
)

QUESTION_UN = (
    "По состоянию на 15 апреля 2026 года: сколько государств-членов "
    "входило в ООН? Ответь строго по этим двум источникам: "
    "https://un.org/en/about-us и "
    "https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН"
)
QUESTION_EU_NO_DATE = (
    "Сколько стран-членов в Европейском союзе на текущую дату? "
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
    assert parse_question_date(QUESTION_EU_NO_DATE) is None
    assert parse_question_date("Сколько стран в ЕС на текущую дату?") is None
    assert parse_question_date("") is None


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
    scope = derive_claim_scope(
        question=QUESTION_EU_NO_DATE,
        as_of=datetime(2026, 9, 17, 13, 0, tzinfo=UTC),
    )
    assert scope["as_of"] == "2026-09-17"
    assert scope["source_domains"] == ["wikipedia.org", "europa.eu"]


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
