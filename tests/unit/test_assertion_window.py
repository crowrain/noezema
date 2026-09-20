"""Unit: question-dependent assertion-fragment selection (T7.16 + T7.22, §6.4).

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

The pages live in the repository as test fixtures
(``tests/fixtures/artifacts``, content-addressed: sha256 of the file ==
the file name) so the required tests run on every machine (CI included)
without the host's ``eval3b-data``/``eval3d-data`` artifact stores.
The EU fixture is a 20 000-char FRAGMENT (leading prefix) of the full
EVAL-3b artifact ``c98cc08e...``: the measured fact offsets (6682
infobox, 8378 lead) are preserved, the "27" figure stays beyond char
6000, and the leading 2 000-char prefix (navigation + TOC) carries no
"27" — the test's preconditions are identical to the full page's. The
T7.22 fixtures are the same kind of leading-prefix fragments of the
EVAL-3d artifacts (fact offsets preserved; the full-page windows
reproduce verbatim on the fragments — verified during the task).

T7.22 (ADR-0011): the fragment can carry up to TWO non-overlapping
budget windows (``select_assertion_windows``) — the T7.16 term-density
window plus a value window. EVAL-3d group A (12 claims, ADR-0010 §2):
both sources were fetched, the curator attached one, because the single
term window missed the assertion in 7 of 12 — the fact sat in the
lead/infobox, the first paragraph, or a data widget. One test per
measured miss class: the fact must land inside the joined fragment.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

from apps.orchestrator.assertion_window import (
    AssertionWindow,
    extract_terms,
    select_assertion_window,
    select_assertion_windows,
)
from packages.cognition.tokenizer import estimate_tokens

# the saved EVAL-3b page where the fact lies past char 6000 (the test
# is REQUIRED to run on this real saved page, not a synthetic string):
# en.wikipedia.org/wiki/European_Union, the "27" member-state fact at
# offset 6682 of the normalized text. Fixture = the 20 000-char leading
# fragment of the full artifact c98cc08e42cf68dc8063a42306d5c45c317ca24
# e244b95091aaae398a9c6fd27 (fact offsets preserved, see module
# docstring); the fixture's own sha256 is its file name.
EU_WIKIPEDIA_SHA = "190748699055e8e6cc6fc22c48558b10b2767f098d053e09149e80866cdf95d7"
EUROPA_SHA = "f63ce302797d46a7d934fa7202fbf9aed07007fde29ce3ad3bed308d7c46f62e"
UN_ORG_SHA = "14b070da98f1de9ef58f0591d552083473aa3544f10c7212a32da8eb91dd82eb"
CBR_SHA = "7ba64093e4f4ded43a712c89a61ac5e11e26c51d700e09e9bfd7be27ad80971c"
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
    """Every fixture is content-addressed (sha256 of the file == the
    file name): the test data is the saved page fragment itself, not a
    re-encoding."""
    for sha in (
        EU_WIKIPEDIA_SHA,
        RU_CONSTITUTION_SHA,
        WORLD_POPULATION_SHA,
        UN_LIST_SHA,
        HABR_SHA,
    ):
        data = (ARTIFACTS / sha[:2] / sha).read_bytes()
        assert hashlib.sha256(data).hexdigest() == sha, f"fixture {sha[:12]} corrupt"


# ── T7.22 (ADR-0011): the second, value-anchored window ────────────────
#
# EVAL-3d group A (ADR-0010 §2): 12 external/temporal claims where BOTH
# sources were fetched but the curator attached one — the T7.16 single
# term window missed the assertion. The measured miss classes (one test
# each below; the questions are the exact EVAL-3d session questions and
# the host adds the plan, as observation_to_evidence does):
#
#   0ba8a00c  ru.wikipedia Конституция РФ — the fact ("12 декабря 1993",
#             the infobox) is past a term window that landed in the
#             2020-amendments section (terms denser there);
#   2c372922  en.wikipedia World_population — the fact ("8.3 billion",
#             the lead) is past a term window in the body (the
#             dominant "popula" ×459 was cap-dropped, "world" anchored
#             the wrong section);
#   299b5a8c  un.org about-us — cross-language: a Russian question has
#             ZERO term matches on the English page → the T7.8 leading
#             prefix (navigation), the fact ("Guterres … 1 January
#             2017") sits just past char 2000;
#   1920edf7  cbr.ru — the fact ("14,00%", the rate widget) is 300+
#             chars past the term window's edge (the phone-number block
#             ahead of it is term-denser);
#   b56208f3  habr.com news — the fact ("18.6", the first paragraph) is
#             past a term window in the body (the dominant "postgr" ×39
#             was cap-dropped);
#   aa2626c5  ru.wikipedia Список государств — the TOC repeats the
#             question's terms and the term window IS the TOC; the fact
#             ("193 государства") is past it;
#   e75aa02b  en.wikipedia European_Union (follow-up question) — the
#             fact ("27 member states", the lead) is past a term window
#             in the body ("europe" ×986 was cap-dropped).
#
# The remaining 5 group-A claims (41886053, 424f071c, 5b22871b,
# 700e4c0f, 8bbbb06a) are UNFIXABLE by any window: the exact claim value
# is absent from the second page (rounded "6,3%" vs "6,33%", "4 июля
# 2020" absent, "53,5948" vs "53,59", "84,24" vs "84,20", "19:28:34"
# absent) — the curator correctly does not attach; ADR-0011 §3.

#: the EVAL-3d group-A pages (fixtures: leading-prefix fragments of the
#: run artifacts, content-addressed; fact offsets preserved)
RU_CONSTITUTION_SHA = (
    "111b30cc048a9957669d6c516d53a7cd09899c335ba276e42459bc4dfb213d47"
)  # 24 000-char fragment of 46f3f5c3… (ru.wikipedia Конституция РФ)
WORLD_POPULATION_SHA = (
    "bfa5537b91bae2b6e33ad9c5ca253c2c97ba5d281bd976191fc6c90a00b57f69"
)  # 12 000-char fragment of 34c33c37… (en.wikipedia World_population)
UN_LIST_SHA = "d46798895a0b1a2a5c0b4ff8f3cf4313d86c2fb4e2f2159c05d287dc473b8a34"
HABR_SHA = "21ef2c6f90e315e879613eaa56f91df44d8a76701bd9be1d4f4d8c35e7562870"
#: the plan the host appends to the question (orchestrator PLAN_TEMPLATE)
PLAN = "Исследовать вопрос, собрать evidence инструментами, предложить claims."


def _norm(s: str) -> str:
    """Whitespace-normalized lowercase (the normalized page text wraps
    sentences with newlines: "12 декабря\\n1993" is one phrase)."""
    return " ".join(s.split()).lower()


def _group_a_windows(sha: str, question: str) -> list[AssertionWindow]:
    """The host path: select_assertion_windows over the stored page with
    question + host plan (as observation_to_evidence builds the input)."""
    return select_assertion_windows(_read_artifact(sha), f"{question}\n{PLAN}", BUDGET)


def _fragments_joined(windows: list[AssertionWindow]) -> str:
    return " ".join(w.text for w in windows)


def test_group_a_infobox_fact_beyond_term_window() -> None:
    """0ba8a00c: ru.wikipedia Конституция РФ. The fact "12 декабря 1993"
    is in the infobox/lead; the T7.16 term window lands in the
    2020-amendments section (its terms are denser there). The value
    window must cover the infobox fact."""
    q = (
        "В каком году принята действующая Конституция Российской Федерации "
        "и с какой даты действуют последние изменения? Установи это "
        "утверждение строго по этим двум источникам: "
        "https://ru.wikipedia.org/wiki/Конституция_Российской_Федерации и "
        "http://duma.gov.ru/legislative/documents/constitution"
    )
    text = _read_artifact(RU_CONSTITUTION_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("12 декабря 1993") not in _norm(primary.text), (
        "precondition: the T7.16 window must MISS the fact (the measured "
        "EVAL-3d miss)"
    )
    windows = _group_a_windows(RU_CONSTITUTION_SHA, q)
    assert len(windows) == 2
    assert _norm("12 декабря 1993") in _norm(_fragments_joined(windows))


def test_group_a_lead_fact_when_term_window_in_body() -> None:
    """2c372922: en.wikipedia World_population. The fact "8.3 billion"
    is in the lead; the dominant "popula" (×459 on the full page) is
    cap-dropped and the term window lands in a body section. The value
    window must cover the lead fact."""
    q = (
        "Какова численность населения Земли на текущую дату? Установи это "
        "утверждение строго по этим двум источникам: "
        "https://www.worldometers.info/world-population и "
        "https://en.wikipedia.org/wiki/World_population"
    )
    text = _read_artifact(WORLD_POPULATION_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("8.3 billion") not in _norm(primary.text), (
        "precondition: the T7.16 window must MISS the fact"
    )
    windows = _group_a_windows(WORLD_POPULATION_SHA, q)
    assert len(windows) == 2
    assert _norm("8.3 billion") in _norm(_fragments_joined(windows))


def test_group_a_cross_language_fact_beyond_prefix() -> None:
    """299b5a8c: un.org about-us. A Russian question about an English
    page: ZERO term matches → the T7.16 fallback is the leading prefix
    (navigation), and the fact ("the 9th occupant … 1 January 2017")
    sits just past char 2000. The value window (no term signal at all —
    the pure number signal) must REPLACE the prefix and cover the fact."""
    q = (
        "Кто является действующим Генеральным секретарём ООН на текущую "
        "дату и с какого года он занимает этот пост? Установи это "
        "утверждение строго по этим двум источникам: https://un.org/en/"
        "about-us и https://ru.wikipedia.org/wiki/Антониу_Гутерриш"
    )
    text = _read_artifact(UN_ORG_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start == -1, "precondition: cross-language → prefix fallback"
    assert _norm("1 january 2017") not in _norm(primary.text)
    windows = _group_a_windows(UN_ORG_SHA, q)
    assert len(windows) == 1, "the value window replaces the prefix fallback"
    assert windows[0].start >= 0, "the leading prefix (navigation) must not be kept"
    frag = _norm(_fragments_joined(windows))
    assert "guterres" in frag
    assert "1 january 2017" in frag


def test_group_a_fact_just_past_term_window_edge() -> None:
    """1920edf7: cbr.ru. The fact "14,00%" (the rate widget) sits just
    past the term window's edge — the phone-number block ahead of the
    widget is term-denser. The value window must cover the widget."""
    q = (
        "Какова, согласно этим двумя источникам (https://cbr.ru/ и "
        "https://www.consultant.ru/legalnews/32063), ключевая ставка Банка "
        "России на текущую дату? Сверь ответ с ранее зафиксированным "
        "значением."
    )
    text = _read_artifact(CBR_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("14,00") not in _norm(primary.text), (
        "precondition: the T7.16 window must MISS the fact (300+ chars short)"
    )
    windows = _group_a_windows(CBR_SHA, q)
    assert len(windows) == 2
    assert _norm("14,00") in _norm(_fragments_joined(windows))


def test_group_a_first_paragraph_when_dominant_term_dropped() -> None:
    """b56208f3: habr.com news. The fact "18.6" is in the first
    paragraph; the dominant "postgr" (×39) is cap-dropped and the term
    window lands in the body. The value window must cover the first
    paragraph."""
    q = (
        "В прошлой сессии было установлено, какая версия PostgreSQL сейчас "
        "последняя стабильная. Перепроверь это утверждение, строго сверив "
        "обе страницы: https://postgresql.org/ и https://habr.com/ru/news/"
        "1080592."
    )
    text = _read_artifact(HABR_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("18.6") not in _norm(primary.text), (
        "precondition: the T7.16 window must MISS the fact"
    )
    windows = _group_a_windows(HABR_SHA, q)
    assert len(windows) == 2
    assert _norm("18.6") in _norm(_fragments_joined(windows))


def test_group_a_toc_repeating_question_terms() -> None:
    """aa2626c5: ru.wikipedia Список государств. The TOC repeats the
    question's terms ("государств", "членов", "список") and the term
    window IS the TOC; the fact "193 государства" is past it. The value
    window must skip the TOC (chrome marker + TOC indexes are not
    values) and cover the lead fact."""
    q = (
        "Сколько государств-членов в ООН на текущую дату? Установи это "
        "утверждение строго по этим двумя источниками: https://un.org/en/"
        "about-us и https://ru.wikipedia.org/wiki/Список_государств_—_членов_ООН"
    )
    text = _read_artifact(UN_LIST_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("193") not in _norm(primary.text), (
        "precondition: the T7.16 window (the TOC) must MISS the fact"
    )
    windows = _group_a_windows(UN_LIST_SHA, q)
    assert len(windows) == 2
    assert _norm("193") in _norm(_fragments_joined(windows))


def test_group_a_eu_followup_lead_fact() -> None:
    """e75aa02b: en.wikipedia European_Union with the FOLLOW-UP question.
    The fact "27 member states" is in the lead; the dominant "europe"
    (×986) is cap-dropped and the term window lands in a body section.
    The value window must cover the lead fact (the T7.16 regression test
    above pins the ANCHOR question, where the term window already lands
    on the fact — this test pins the follow-up variant that missed)."""
    q = (
        "В прошлой сессии было установлено число стран-членов ЕС. "
        "Перепроверь его, строго сверив оба источников: "
        "https://en.wikipedia.org/wiki/European_Union и "
        "https://european-union.europa.eu/principles-countries-history/"
        "facts-and-figures-european-union_en."
    )
    text = _read_artifact(EU_WIKIPEDIA_SHA)
    primary = select_assertion_window(text, f"{q}\n{PLAN}", BUDGET)
    assert primary.start >= 0
    assert _norm("27 member") not in _norm(primary.text), (
        "precondition: the T7.16 window must MISS the fact"
    )
    windows = _group_a_windows(EU_WIKIPEDIA_SHA, q)
    assert len(windows) == 2
    assert _norm("27 member") in _norm(_fragments_joined(windows))


def test_windows_non_overlapping_and_bounded() -> None:
    """Invariant on every group-A fixture: ≤ 2 windows, each ≤ BUDGET
    chars, and the windows never overlap (the joined fragment is two
    disjoint slices — the curator sees a gap marker, not duplicated
    text)."""
    for sha, q in (
        (RU_CONSTITUTION_SHA, "В каком году принята Конституция РФ? ru.wikipedia.org"),
        (WORLD_POPULATION_SHA, "Какова численность населения Земли? en.wikipedia.org"),
        (UN_LIST_SHA, "Сколько государств-членов в ООН? ru.wikipedia.org"),
        (HABR_SHA, "Какая версия PostgreSQL? habr.com"),
        (EU_WIKIPEDIA_SHA, "Сколько стран-членов ЕС? en.wikipedia.org"),
        (UN_ORG_SHA, "Кто Генеральный секретарь ООН? un.org"),
        (CBR_SHA, "Какова ключевая ставка? cbr.ru"),
        (EUROPA_SHA, "Сколько стран-членов ЕС? europa.eu"),
    ):
        windows = select_assertion_windows(_read_artifact(sha), q, BUDGET)
        assert 1 <= len(windows) <= 2, f"{sha[:12]}: {len(windows)} windows"
        for w in windows:
            assert 0 < len(w.text) <= BUDGET, f"{sha[:12]}: window {len(w.text)}"
        for a, b in itertools.pairwise(windows):
            assert a.start + len(a.text) <= b.start or b.start + len(b.text) <= a.start, (
                f"{sha[:12]}: windows overlap: {a.start}..{a.start + len(a.text)} "
                f"and {b.start}..{b.start + len(b.text)}"
            )


def test_single_window_mode_matches_t716() -> None:
    """max_windows=1 must be EXACTLY the T7.16 behavior (the primary
    window alone) — the old payload shape for any consumer that keeps
    one window."""
    for sha, q in (
        (EU_WIKIPEDIA_SHA, "Сколько стран-членов ЕС? en.wikipedia.org"),
        (CBR_SHA, "Какова ключевая ставка? cbr.ru"),
        (UN_ORG_SHA, "Кто Генеральный секретарь ООН? un.org"),
    ):
        text = _read_artifact(sha)
        single = select_assertion_windows(text, q, BUDGET, max_windows=1)
        assert single == [select_assertion_window(text, q, BUDGET)]


def test_no_numbers_keeps_primary_window_only() -> None:
    """A page with question terms but NO numbers at all: no value
    candidate exists → the fragment degrades to the T7.16 primary
    window alone."""
    text = ("абвгд " * 1_000) + "дежжз здесь" + ("абвгд " * 1_000)
    primary = select_assertion_window(text, "вопрос про дежжз", BUDGET)
    assert primary.start >= 0
    assert select_assertion_windows(text, "вопрос про дежжз", BUDGET) == [primary]


def test_cross_language_value_window_replaces_prefix() -> None:
    """Synthetic cross-language case: no term matches (prefix fallback)
    but a value present → the value window replaces the prefix."""
    text = ("nav item " * 500) + "the value is 193 states as of 2026" + ("more text " * 500)
    q = "сколько государств членов"  # no latin terms → no match on the page
    primary = select_assertion_window(text, q, BUDGET)
    assert primary.start == -1
    windows = select_assertion_windows(text, q, BUDGET)
    assert len(windows) == 1
    assert windows[0].start >= 0
    assert _norm("193 states") in _norm(windows[0].text)


def test_short_text_single_window() -> None:
    """Text shorter than the budget: the whole text, ONE window (the
    T7.16 behavior)."""
    windows = select_assertion_windows("hello world", "вопрос про hello", BUDGET)
    assert windows == [AssertionWindow(text="hello world", start=0)]


def test_windows_validation() -> None:
    import pytest

    with pytest.raises(ValueError, match="budget"):
        select_assertion_windows("abc", "вопрос", 0)
    with pytest.raises(ValueError, match="max_windows"):
        select_assertion_windows("abc" * 5_000, "вопрос про abc", BUDGET, max_windows=0)


def test_two_evidence_fragments_fit_the_claims_evidence_budget() -> None:
    """The T7.22 budget bound (ADR-0011 §4): the fragment of ONE
    source_assertion evidence is ≤ 2*BUDGET + separator chars (two
    non-overlapping windows). Two such evidence on ONE claim — the
    worst case for the curator's claims_evidence section (8192 tokens,
    context.py) — must fit WITH MARGIN, so the fragments never displace
    the section's knowledge lines or push the other curator sections
    (pending_claims 1024, contradictions 3072 — reserved independently).

    Measured on the real worst-case fixture pair (the two EU pages —
    the most token-dense fragments of the corpus): each evidence block
    (the ≤1000-char meta line + the "[текст фрагмента]" line + the
    joined fragment, as _evidence_lines renders it) is far below the
    section budget, and the pair leaves ≥ 50% of the section free."""
    # the worst-case pair (measured 2026-09-19, ADR-0011 §4): the EU
    # Wikipedia fixture (837-token fragment) and the cbr.ru fixture
    worst_case = [
        (
            EU_WIKIPEDIA_SHA,
            "Сколько стран-членов в Европейском союзе на текущую дату? Установи "
            "это утверждение строго по этим двум источникам: "
            "https://en.wikipedia.org/wiki/European_Union и "
            "https://european-union.europa.eu/principles-countries-history/"
            "facts-and-figures-european-union_en",
        ),
        (
            CBR_SHA,
            "Какова ключевая ставка Банка России на текущую дату? Установи это "
            "утверждение строго по этим двум источникам: https://cbr.ru/ и "
            "https://www.consultant.ru/legalnews/32063",
        ),
    ]
    # the meta line is capped at 1000 chars by the orchestrator
    # (_evidence_lines _cap_args) — the worst case is a full 1000 chars
    meta = ("url: https://example.org/ " + "x" * 940)[:1_000]
    total = 0
    for sha, q in worst_case:
        windows = select_assertion_windows(_read_artifact(sha), q, BUDGET)
        fragment = "\n[…]\n".join(w.text for w in windows)
        assert len(fragment) <= 2 * BUDGET + len("\n[…]\n")
        block = f"[0] source_assertion {sha[:16]} {meta}\n    [текст фрагмента]\n{fragment}"
        total += estimate_tokens(block)
    # the §22.1 claims_evidence budget (packages/cognition/context.py)
    claims_evidence_budget = 8_192
    assert total <= claims_evidence_budget, (
        f"two evidence fragments ({total} tokens) must fit the "
        f"claims_evidence section ({claims_evidence_budget} tokens)"
    )
    # with margin: even the TWO fragments leave at least half the
    # section for the claim's knowledge lines (no displacement)
    assert total <= claims_evidence_budget // 2, (
        f"two evidence fragments ({total} tokens) must leave ≥ 50% of "
        f"the claims_evidence section free (no displacement)"
    )
