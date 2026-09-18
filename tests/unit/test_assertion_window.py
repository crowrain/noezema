"""Unit: question-dependent assertion-fragment selection (T7.16, §6.4).

The pure module ``apps/orchestrator/assertion_window.py``: find the
densest region of the question/plan's content terms in the normalized
page text and cut the budgeted window around it (the T7.8 leading
prefix was navigation on most full pages; the fact sat up to ~6.7k
chars deeper — measured on the EVAL-3b corpus, docs/eval/
EVAL-3b-postmortem.md).

The REAL-page test below runs the same host path that observation_to_evidence
uses (select_assertion_window over the stored artifact text) on a saved
EVAL-3b page where the fact lies past char 6000 — en.wikipedia.org
/wiki/European_Union (the "27" member-state fact is at offset 6682 of
the normalized text).

The page lives in the repository as a test fixture
(``tests/fixtures/artifacts``, content-addressed: sha256 of the file ==
the file name) so the required test runs on every machine (CI included)
without the host's ``eval3b-data`` artifact store. The EU fixture is a
20 000-char FRAGMENT (leading prefix) of the full EVAL-3b artifact
``c98cc08e...``: the measured fact offsets (6682 infobox, 8378 lead)
are preserved, the "27" figure stays beyond char 6000, and the leading
2 000-char prefix (navigation + TOC) carries no "27" — the test's
preconditions are identical to the full page's.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from apps.orchestrator.assertion_window import (
    AssertionWindow,
    extract_terms,
    select_assertion_window,
)

# the saved EVAL-3b page where the fact lies past char 6000 (the test
# is REQUIRED to run on this real saved page, not a synthetic string):
# en.wikipedia.org/wiki/European_Union, the "27" member-state fact at
# offset 6682 of the normalized text. Fixture = the 20 000-char leading
# fragment of the full artifact c98cc08e42cf68dc8063a42306d5c45c317ca24
# e244b95091aaae398a9c6fd27 (fact offsets preserved, see module
# docstring); the fixture's own sha256 is its file name.
EU_WIKIPEDIA_SHA = "190748699055e8e6cc6fc22c48558b10b2767f098d053e09149e80866cdf95d7"
ARTIFACTS = Path(__file__).resolve().parent.parent / "fixtures" / "artifacts"
BUDGET = 2_000  # == apps.orchestrator.evidence.SOURCE_ASSERTION_TEXT_BUDGET


def _eu_question() -> str:
    return (
        "Сколько стран-членов в Европейском союзе на текущую дату? Установи это "
        "утверждение строго по этим двум источникам: "
        "https://en.wikipedia.org/wiki/European_Union и "
        "https://european-union.europa.eu/principles-countries-history/"
        "facts-and-figures-european-union_en"
    )


def _read_artifact(sha: str) -> str:
    return (ARTIFACTS / sha[:2] / sha).read_text(encoding="utf-8")


# ── real saved page (the required one) ─────────────────────────────────


def test_real_page_fact_beyond_6000_is_captured() -> None:
    """The EU Wikipedia page: the "27" member-state fact is at offset
    6682 of the normalized text — far beyond the 2000-char leading
    prefix. The question-dependent window must land on it: the fragment
    carries "27" together with the member-state wording, and the
    leading prefix (navigation) does NOT."""
    text = _read_artifact(EU_WIKIPEDIA_SHA)
    assert len(text) > 8_000, "corpus page unexpectedly short"
    # preconditions from the post-mortem measurement: the fact "27" is
    # at offset 6682 (the infobox "Membership: 27 members") — past char
    # 6000, and the leading 2000-char prefix (navigation + TOC)
    # carries NO occurrence of "27" at all
    assert "27" in text[6_000:]
    assert "27" not in text[:BUDGET], (
        "precondition: the fact figure must lie beyond the leading prefix"
    )

    window = select_assertion_window(text, _eu_question(), BUDGET)
    assert len(window.text) <= BUDGET
    assert window.start >= 0, "a term match was expected → non-prefix window"
    # the fact is INSIDE the fragment: the member-state figure and its
    # context ("27 member states" — the body sentence at offset 8378,
    # or the infobox "27 members" at offset 6682)
    frag = window.text.lower()
    assert "27" in window.text, "the fact '27' must be inside the fragment"
    assert "27 member" in frag, (
        "the fragment must carry '27 member(s)' — the fact with its context"
    )
    # and the window is QUESTION-DEPENDENT: it is not the T7.8 leading
    # prefix (the leading 2000 chars on this page are navigation + TOC
    # with no "27" at all)
    assert window.start > 0, (
        f"the window must not be the leading prefix, got start={window.start}"
    )


# ── term extraction ────────────────────────────────────────────────────


def test_extract_terms_stops_and_url_segments() -> None:
    q = _eu_question()
    terms = extract_terms(q)
    # content terms survive (as prefix stems)
    assert any(t.startswith("европ") for t in terms)
    assert any(t.startswith("стран") for t in terms)
    # URL path segments are latin anchors (deduplicated by stem:
    # "european" and "europe" collapse to the 6-char stem "europe")
    assert "europe" in terms
    assert "union" in terms
    # function words are dropped
    assert not any(t.startswith("на") or t.startswith("по") or t.startswith("the") for t in terms)
    # deterministic and deduplicated by stem
    assert terms == extract_terms(q)


# ── pure behavior of the window selection ──────────────────────────────


def test_short_text_returns_whole_text() -> None:
    w = select_assertion_window("hello world", "вопрос про hello", BUDGET)
    assert w == AssertionWindow(text="hello world", start=0)


def test_empty_or_termless_question_falls_back_to_prefix() -> None:
    text = "x" * 5_000
    assert select_assertion_window(text, "", BUDGET) == AssertionWindow(
        text=text[:BUDGET], start=-1
    )
    # a question made only of stop words yields no terms
    assert select_assertion_window(text, "по на из то это", BUDGET) == AssertionWindow(
        text=text[:BUDGET], start=-1
    )


def test_no_term_match_falls_back_to_prefix() -> None:
    text = ("абвгд " * 2_000).strip()
    w = select_assertion_window(text, "вопрос про zzzqqq", BUDGET)
    assert w == AssertionWindow(text=text[:BUDGET], start=-1)


def test_window_covers_dense_region_and_not_navigation() -> None:
    nav = ("NAV NAV " * 200).strip()  # 1200 chars of navigation
    filler = ("filler " * 200).strip()  # 1000 chars of unrelated body
    fact = "The European Union has 27 member states including France."
    text = nav + "\n" + filler + "\n" + fact
    question = "Сколько стран-членов в Европейском союзе? european union member states"
    w = select_assertion_window(text, question, 800)
    assert "27 member states" in w.text
    assert w.start > len(nav), "the window must skip the navigation prefix"


def test_window_is_bounded_by_budget() -> None:
    text = "а" * 10_000
    fact_at = 9_000
    text = text[:fact_at] + "факт термин" + text[fact_at + 10 :]
    w = select_assertion_window(text, "вопрос про термин факт", BUDGET)
    assert len(w.text) <= BUDGET
    assert "термин" in w.text


def test_earlier_wins_when_density_ties() -> None:
    # the same single term at two spots, both far enough out that the
    # start-proximity bonus has decayed: the earlier region wins
    text = ("z" * 10_000) + "target one" + ("z" * 10_000) + "target two"
    w = select_assertion_window(text, "вопрос про target", 400)
    assert "one" in w.text
    assert "two" not in w.text


def test_start_bias_prefers_infobox_over_body_mention() -> None:
    # a fact region near the start (the infobox) wins over a denser
    # body mention far out, when the difference in distinct terms does
    # not exceed the proximity bonus — the Wikipedia EU case: infobox
    # "27 members" at ~1k vs the body mention at ~8.3k
    # the infobox region matches 2 question terms ("europe", "union");
    # the far body region matches 3 ("europe", "union", "member") — so
    # the body wins by one distinct term: the proximity bonus is a
    # tie-break only and must NOT override a real density difference
    nav = ("nav " * 30)
    infobox = nav + "Membership: 27 members of the European Union"
    body = ("unrelated filler text " * 300)
    text = infobox + "\n" + body + "\nEuropean Union is a union of 27 member states"
    question = "Сколько стран в Европейском союзе? european union member states"
    w = select_assertion_window(text, question, 800)
    assert "27 member states" in w.text, (
        "a denser far region (one more distinct term) must win over the "
        "nearer infobox: the proximity bonus is a tie-break only"
    )

    # and when the density IS tied (both regions match the same terms),
    # the nearer region wins: two regions with identical term coverage,
    # the nearer one is preferred
    text2 = (
        nav + "European Union union facts"  # near: europe + union
        + "\n" + body + "\n"
        + "European Union union"  # far: europe + union (same coverage)
    )
    w2 = select_assertion_window(text2, question, 800)
    assert "facts" in w2.text, (
        "on a density tie the nearer (infobox-side) region must win"
    )


def test_budget_must_be_positive() -> None:
    import pytest

    with pytest.raises(ValueError, match="budget"):
        select_assertion_window("abc", "вопрос", 0)


def test_corpus_facts_are_reachable_from_their_pages() -> None:
    """The remaining measured corpus pages: each fact position from the
    post-mortem measurement is inside the window selected for that
    question (guards the density heuristic against the whole corpus,
    not only the required Wikipedia page)."""
    cases = [
        # (sha, question, needle)
        (
            "f63ce302797d46a7d934fa7202fbf9aed07007fde29ce3ad3bed308d7c46f62e",
            _eu_question(),
            "27",
        ),  # european-union.europa.eu, fact 1996
        (
            "14b070da98f1de9ef58f0591d552083473aa3544f10c7212a32da8eb91dd82eb",
            "Сколько государств-членов в ООН на текущую дату? Установи это "
            "утверждение строго по этим двум источникам: https://un.org/en/about-us "
            "и https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН",
            "member",
        ),  # un.org, fact ~193-1009
        (
            "82bfee73e706bf06eb25a59993dcb0bf59d84e7516d4c24354aa3222d35c9f39",
            "Какая версия Python является последней стабильной на текущую дату? "
            "Установи это утверждение строго по этим двум источникам: "
            "https://www.python.org/downloads/ и "
            "https://community.chocolatey.org/packages/python314",
            "3.14",
        ),  # python.org, fact 1346
        (
            "7ba64093e4f4ded43a712c89a61ac5e11e26c51d700e09e9bfd7be27ad80971c",
            "Какова ключевая ставка Банка России на текущую дату? Установи это "
            "утверждение строго по этим двум источникам: https://cbr.ru/ и "
            "https://www.consultant.ru/legalnews/32063",
            "14,00",
        ),  # cbr.ru, fact 2511
    ]
    for sha, question, needle in cases:
        text = _read_artifact(sha)
        w = select_assertion_window(text, question, BUDGET)
        assert needle.lower() in w.text.lower(), (
            f"fact {needle!r} must be inside the window for {sha[:12]}"
        )


def test_artifact_integrity() -> None:
    """The fixture is content-addressed (sha256 of the file == the file
    name): the test data is the saved EVAL-3b page fragment itself, not
    a re-encoding."""
    data = (ARTIFACTS / EU_WIKIPEDIA_SHA[:2] / EU_WIKIPEDIA_SHA).read_bytes()
    assert hashlib.sha256(data).hexdigest() == EU_WIKIPEDIA_SHA
