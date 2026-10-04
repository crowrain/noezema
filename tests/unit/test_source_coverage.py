"""Unit: host-tracked coverage of the question's named sources (T7.21,
EVAL-3d post-mortem, §3.7, §5.4, §11.2, ADR-0010).

The pure module ``apps.orchestrator.source_coverage``: the trusted host
extracts the sources the QUESTION names, tracks per named source
whether it was fetched or errored (a definitive egress answer), and
reports the state — the orchestrator withholds the release to
consolidation while the coverage is incomplete.
"""

from __future__ import annotations

from urllib.parse import quote

from apps.orchestrator.source_coverage import (
    SourceCoverageTracker,
    coverage_key,
    named_source_urls,
)

# ── coverage_key: the canonical matching of named vs fetched URL ──────


def test_coverage_key_ignores_scheme_www_and_trailing_slash() -> None:
    # the EVAL-3d shape: the question names un.org, the model fetches
    # www.un.org (the proxy follows the redirect) — the same named source
    assert coverage_key("https://un.org/en/about-us") == coverage_key(
        "https://www.un.org/en/about-us"
    )
    assert coverage_key("http://un.org/en/about-us") == coverage_key(
        "https://un.org/en/about-us"
    )
    assert coverage_key("https://cbr.ru/") == coverage_key("https://www.cbr.ru/")
    assert coverage_key("https://cbr.ru/eng/currency_base/daily/") == coverage_key(
        "https://cbr.ru/eng/currency_base/daily"
    )


def test_coverage_key_uses_registrable_domain() -> None:
    # PSL-style: co.uk subdomains collapse to the registrable domain —
    # the same normalization the independence groups use (§11.3)
    assert coverage_key("https://www.bbc.co.uk/news") == coverage_key(
        "https://bbc.co.uk/news"
    )
    # different subdomains of DIFFERENT registrable domains stay apart
    assert coverage_key("https://a.example.com/x") != coverage_key(
        "https://b.other.com/x"
    )
    # subdomain of the SAME registrable domain: the same source key
    # (www.un.org and un.org — one named source)
    assert coverage_key("https://ru.wikipedia.org/wiki/X") == coverage_key(
        "https://wikipedia.org/wiki/X"
    )


def test_coverage_key_percent_decodes_path_and_keeps_query() -> None:
    # the corpus v2 shapes: percent-encoded Russian titles
    encoded = "https://ru.wikipedia.org/wiki/%D0%A1%D0%BF%D0%B8%D1%81%D0%BE%D0%BA"
    assert coverage_key(encoded) == coverage_key("https://ru.wikipedia.org/wiki/Список")
    # the host is the registrable domain (ru.wikipedia.org →
    # wikipedia.org — the PSL-style normalization, same as §11.3)
    assert coverage_key(encoded) == "wikipedia.org/wiki/список"
    # different query = different page
    assert coverage_key("https://x.example/p?a=1") != coverage_key("https://x.example/p?a=2")
    assert coverage_key("https://x.example/p") is not None


def test_coverage_key_rejects_non_urls() -> None:
    assert coverage_key("ftp://x.example/a") is None
    assert coverage_key("not a url") is None
    assert coverage_key("") is None
    assert coverage_key("https://") is None


# ── named_source_urls ──────────────────────────────────────────────────


def test_named_source_urls_dedupes_and_keeps_order() -> None:
    q = (
        "Проверь по этим источникам: https://a.example/x и https://b.example/y. "
        "Возвращайся к https://a.example/x при противоречии."
    )
    assert named_source_urls(q) == ("https://a.example/x", "https://b.example/y")
    assert named_source_urls("Локальный вопрос без ссылок") == ()


# ── SourceCoverageTracker ──────────────────────────────────────────────


def test_tracker_pending_until_fetched_or_errored() -> None:
    named = ("https://a.example/x", "https://b.example/y")
    t = SourceCoverageTracker(named)
    assert not t.is_complete
    assert t.uncovered() == named

    # a fetch of an UNNAMED url changes nothing
    t.mark("https://other.example/z", ok=True)
    assert t.uncovered() == named

    # success covers the matching named source (www variant matches)
    t.mark("https://www.a.example/x", ok=True)
    assert t.uncovered() == ("https://b.example/y",)

    # an error covers the second source (the egress answered)
    t.mark("https://b.example/y", ok=False)
    assert t.is_complete
    assert t.uncovered() == ()

    rep = t.report()
    assert rep["fetched"] == ["https://a.example/x"]
    assert rep["errored"] == ["https://b.example/y"]
    assert rep["uncovered"] == []
    assert rep["named"] == list(named)


def test_tracker_once_covered_stays_covered() -> None:
    t = SourceCoverageTracker(("https://a.example/x",))
    t.mark("https://a.example/x", ok=False)  # errored first
    t.mark("https://a.example/x", ok=True)  # a later success cannot un-cover
    rep = t.report()
    assert rep["errored"] == ["https://a.example/x"]
    assert rep["fetched"] == []
    assert t.is_complete
    # and vice versa: an error after a success does not un-cover
    t2 = SourceCoverageTracker(("https://a.example/x",))
    t2.mark("https://a.example/x", ok=True)
    t2.mark("https://a.example/x", ok=False)
    assert t2.report()["fetched"] == ["https://a.example/x"]


def test_tracker_duplicate_named_urls_collapse_in_uncovered() -> None:
    t = SourceCoverageTracker(("https://a.example/x", "https://www.a.example/x"))
    assert t.uncovered() == ("https://a.example/x",)  # one source, one entry
    t.mark("https://a.example/x", ok=True)
    assert t.is_complete


def test_tracker_untrackable_named_url_is_reported_and_does_not_block() -> None:
    # a named URL that coverage_key cannot normalize (not an http(s)
    # URL) is UNTRACKABLE: it does not block the gate (an unfetched
    # source can never produce evidence — no grade inflation), but it
    # is reported separately so the audit stays transparent. The
    # orchestrator's extractor only yields http(s) URLs, so this branch
    # is defense-in-depth.
    t = SourceCoverageTracker(("ftp://a.example/x", "https://b.example/y"))
    assert t.uncovered() == ("https://b.example/y",)
    t.mark("https://b.example/y", ok=True)
    assert t.is_complete
    rep = t.report()
    assert rep["untracked"] == ["ftp://a.example/x"]
    assert rep["uncovered"] == []


# ── T7.57: the Go question — a parenthesis-URL is covered by its download ─


#: byte-identical to the smoke corpus line (question-set-smoke.jsonl) —
#: SMOKE-V14 §4.3: the old extractor cut the wiki URL at its first «)»,
#: the canonical download of the FULL address did not match the truncated
#: named source, and the session died budget-exhausted without a claim
GO_QUESTION = (
    "Какой номер носит последняя стабильная версия Go на текущую дату? "
    "Установи это утверждение строго по этим двум источникам: "
    "https://go.dev/dl/ и https://ru.wikipedia.org/wiki/Go_(язык_программирования)"
)
GO_WIKI = "https://ru.wikipedia.org/wiki/Go_(язык_программирования)"


def test_go_question_named_sources_contain_the_full_parenthesized_url() -> None:
    # the coverage requirement the session gets: BOTH full spellings, the
    # wiki one with its parentheses intact (T7.57)
    assert named_source_urls(GO_QUESTION) == ("https://go.dev/dl/", GO_WIKI)


def test_coverage_key_normalizes_cyrillic_and_parens_of_a_wiki_title() -> None:
    # extraction yields the literal title (Cyrillic + parentheses); a real
    # download is percent-encoded — coverage_key must collapse them onto ONE
    # key, otherwise the canonical fetch never covers the named source
    encoded = "https://ru.wikipedia.org/wiki/Go_(" + quote("язык_программирования") + ")"
    assert coverage_key(GO_WIKI) == "wikipedia.org/wiki/go_(язык_программирования)"
    assert coverage_key(encoded) == coverage_key(GO_WIKI)
    # even a percent-encoded parenthesis decodes to the same key
    fully_encoded = "https://ru.wikipedia.org/wiki/Go_%28" + quote("язык_программирования") + "%29"
    assert coverage_key(fully_encoded) == coverage_key(GO_WIKI)


def test_go_tracker_is_closed_by_canonical_fetches() -> None:
    named = named_source_urls(GO_QUESTION)
    t = SourceCoverageTracker(named)
    assert t.uncovered() == named

    # the canonical downloads a real session performs (SMOKE-V14/V14B):
    # go.dev and the percent-encoded full wiki URL with literal parentheses
    encoded = "https://ru.wikipedia.org/wiki/Go_(" + quote("язык_программирования") + ")"
    t.mark("https://go.dev/dl/", ok=True)
    assert t.uncovered() == (GO_WIKI,)  # the wiki source is still pending
    t.mark(encoded, ok=True)  # the canonical download COVERS the named source
    rep = t.report()
    assert rep["fetched"] == list(named)  # report carries the question's spellings
    assert rep["uncovered"] == []
    assert rep["untracked"] == []
    assert t.is_complete
