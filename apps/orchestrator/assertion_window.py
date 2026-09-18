"""Question-dependent assertion-fragment selection (T7.16, §6.4).

T7.8 added ``assertion_text`` to the ``source_assertion`` payload as the
first ``SOURCE_ASSERTION_TEXT_BUDGET`` characters of the NORMALIZED page
text. On a typical full page (Wikipedia, government portals, legal news
sites) the first 2000 characters are site navigation, not content: the
fact the question is about sits thousands of characters deeper, so the
curator (a fresh chat call that only sees this fragment) receives
navigation instead of the assertion.

This module finds the region of the normalized text with the HIGHEST
DENSITY of the question/plan's content terms and returns the budgeted
window around that region — the same 2000-char budget, but aimed where
the assertion actually is (the fact at offset 6682 in the EU Wikipedia
page is reachable inside a 2000-char window).

Pure, host-side, deterministic. The selected fragment is NOT part of
the §14.3 identity (identity stays over the original content hash, so
dedupe semantics are unchanged).
"""

from __future__ import annotations

import re
import urllib.parse
from dataclasses import dataclass

#: the window (in chars of the lowercased normalized text) over which
#: term matches are counted for the density score. A fact sentence plus
#: its context fits inside this; a wider window would let unrelated
#: term mentions in the page body dilute the score.
#:
#: Derived from the EVAL-3b corpus measurements (docs/eval/
#: EVAL-3b-postmortem.md): on 7 real pages the fact sits within a few
#: hundred chars of the co-occurring question terms, and the 2000-char
#: budget window centered on the best region covers the fact with
#: margin. 400 (instead of 500) keeps the "view history" chrome pair
#: from tying the infobox region on the EU Wikipedia page.
_MATCH_SPAN = 400

#: how many chars before the best match the budget window starts.
#: Non-zero so the window carries the sentence start (a fact like "The
#: European Union has 27 member states" is readable from its subject),
#: not just a mid-sentence slice.
_LEAD = 300

#: score bonus for a dense region that sits NEAR the beginning of the
#: text. The infobox/lead of a page is where a fact about the page
#: subject lives first (Wikipedia: the infobox "Membership: 27 members"
#: precedes the body); a pure density tie-break by earliest position
#: would still walk into body mentions when the infobox region ties.
#: The bonus decays over the first ~8000 chars, so a fact at 6.6k
#: still outranks a denser but irrelevant region at 30k, while an
#: infobox at ~1k beats a body mention at ~8.3k on a tie.
_START_BONUS_DECAY = 8_000

#: the start-proximity bonus is a TIE-BREAK only: it must never outweigh
#: a single extra DISTINCT term in the dense region. The score is
#: (distinct terms) + bonus; a bonus < 1 keeps the term count dominant.
_START_BONUS_MAX = 0.5

#: floor for the page-wide signal filter: a term matching at most this
#: often is kept no matter how many other term matches the page has
#: (the rarest terms are the most localizing).
_MIN_TERM_FREQ = 4

#: chrome stems: page furniture that repeats on EVERY page of a site
#: (Wikipedia's "view history", "tools", "union" in the infobox AND the
#: body, "python" on python.org). They carry zero information about
#: WHERE the fact is, and their density peaks in the navigation, not
#: the content. Dropped before the density search.
_CHROME = frozenset(
    {
        "histor",  # "view history" / "history" links (Wikipedia chrome)
        "countr",  # "countries" / "country" — too generic to localize
        "princi",  # "principles" — a section heading, not the fact
        "figure",  # "figures" — a section heading, not the fact
        "packag",  # "packages" — chocolatey/python.org nav
        "download",  # "download" — python.org nav
        "wiki",  # "wikipedia" / "wiki" — site identity
    }
)

#: tokens shorter than this are pure noise for term matching
_MIN_TERM_LEN = 4

#: Russian + English function words (lowercase). They appear in every
#: question and every page, so they carry zero information about WHERE
#: the answer is; dropping them sharpens the density signal.
_STOP = frozenset(
    [
        # Russian
        "и", "в", "на", "по", "с", "о", "а", "но", "или", "не", "что", "как", "это",
        "когда", "где", "кто", "какой", "сколько", "много", "мало", "очень", "тот",
        "этот", "такой", "мой", "твой", "наш", "их", "его", "её", "за", "из", "до",
        "от", "у", "к", "над", "под", "между", "через", "против", "без", "при", "об",
        "обо", "же", "бы", "ли", "ни", "то", "насколько", "можно", "нужно", "надо",
        "есть", "быть", "был", "была", "было", "были", "стал", "стала", "стало",
        "стали", "ответь", "установи", "строго", "этим", "двум", "источникам",
        "источнику", "источника", "источниками", "текущую", "дату", "сейчас", "номер",
        "номеру", "версию", "версия", "какойто", "какая", "какое", "каких", "https",
        "http", "www", "org", "com", "ru", "en",
        # English
        "the", "a", "an", "of", "in", "on", "at", "to", "for", "and", "or", "but",
        "is", "are", "was", "were", "be", "been", "being", "what", "which", "who",
        "how", "why", "when", "where", "that", "this", "these", "those", "it",
        "its", "as", "by", "with", "from", "up", "about", "into", "through",
        "during", "before", "after", "above", "below", "between", "out", "off",
        "over", "under", "again", "then", "once", "here", "there", "all", "any",
        "both", "each", "few", "more", "most", "other", "some", "such", "no",
        "nor", "not", "only", "own", "same", "so", "than", "too", "very", "can",
        "will", "just", "should", "now", "do", "does", "did", "doing", "done",
        "you", "your", "we", "our", "they", "their", "tell", "me", "answer",
        "state", "set", "according", "strictly", "these", "two", "sources",
        "source", "current", "date", "today", "number", "version",
    ]
)

#: the question/plan text may carry URLs (the corpus questions name the
#: exact sources); the path segments are strong term candidates
#: (e.g. /wiki/European_Union → "european", "union")
_URL_RE = re.compile(r"https?://[^\s)\]>]+")
_URL_PATH_SEG_RE = re.compile(r"[/_\-]")

#: lowercase runs (Latin + Cyrillic + digits)
_TOKEN_RE = re.compile(r"[a-zа-яё0-9]+")


@dataclass(frozen=True, slots=True)
class AssertionWindow:
    """The selected fragment and its anchor in the normalized text.

    ``start`` is the offset of the fragment in the original text
    (``-1`` when the text is shorter than the budget and no search was
    needed, or when no question term matched at all — in both cases the
    fragment is the leading prefix, the T7.8 behavior).
    """

    text: str
    start: int


def _prefix_stem(term: str) -> str:
    """Short prefix stem for case- and inflection-tolerant matching.

    Russian case/number endings and English "-s"/"-ed"/"-ing" all live
    at the END of the word, so a prefix stem keeps the stem matchable:
    "союз" matches "союзе", "states" matches "state". The prefix length
    is scaled so the stem stays specific enough not to collide with
    unrelated words (len 7+ → 6, len 5-6 → 5, shorter → whole word).
    """
    if len(term) >= 7:
        return term[:6]
    if len(term) >= 5:
        return term[:5]
    return term


#: a fact region is where the question's terms co-occur WITH A NUMBER:
#: "27 members", "3.14.7", "14,00%" — the assertion is a value, and
#: the number is its anchor. The number bonus is a TIE-BREAK (it must
#: never outweigh a single extra distinct term).
_NUMBER_BONUS_MAX = 0.5


def _has_number(window: str) -> bool:
    return any(c.isdigit() for c in window)


def extract_terms(question: str) -> list[str]:
    """Content terms of the question/plan: lowercase, stop-filtered,
    deduplicated by prefix stem (the stem is the search key, so
    "союзе" and "союза" collapse to one search).

    Sources:
    - content words of the question body (len >= _MIN_TERM_LEN, not a
      stop word, not pure digits);
    - path segments of any URL in the question (cross-language anchor:
      a Russian question about en.wikipedia.org/wiki/European_Union gets
      the latin terms "european", "union").
    """
    raw: set[str] = set()
    q = _URL_RE.sub(" ", question.lower())
    for tok in _TOKEN_RE.findall(q):
        if len(tok) >= _MIN_TERM_LEN and tok not in _STOP and not tok.isdigit():
            raw.add(tok)
    for url in _URL_RE.findall(question):
        path = urllib.parse.urlparse(url).path
        for seg in _URL_PATH_SEG_RE.split(path):
            seg = seg.strip().lower()
            if len(seg) >= _MIN_TERM_LEN and seg not in _STOP and not seg.isdigit():
                raw.add(seg)
    seen: dict[str, None] = {}
    for t in sorted(raw):
        seen.setdefault(_prefix_stem(t), None)
    return list(seen)


def select_assertion_window(
    text: str,
    question: str,
    budget: int,
    *,
    match_span: int = _MATCH_SPAN,
    lead: int = _LEAD,
) -> AssertionWindow:
    """Pick the ``budget``-char fragment of ``text`` to carry in the
    ``source_assertion`` payload.

    Strategy: find the position where the question's content terms are
    DENSEST (most distinct stems matched inside a ``match_span``-char
    sliding window), and start the budget window ``lead`` chars before
    that position. The two-pointer pass is O(n·terms) with n = number of
    term matches — a normalized page is at most a few hundred KB, so
    this is a few ms of host-side work per fetch.

    Fallbacks (the T7.8 leading-prefix behavior):
    - empty/missing question, or no content terms in it;
    - no term matches anywhere in the text;
    - text shorter than the budget (the whole text IS the fragment).

    Deterministic: ties go to the EARLIEST best position.
    """
    if budget <= 0:
        raise ValueError(f"budget must be positive, got {budget}")
    if len(text) <= budget:
        return AssertionWindow(text=text, start=0)
    terms = extract_terms(question) if question else []
    if not terms:
        return AssertionWindow(text=text[:budget], start=-1)

    tl = text.lower()
    # page-wide signal filter: a term that occurs everywhere on the
    # page (site chrome — "view history" on Wikipedia, "python" on
    # python.org, "union" in the infobox AND the body) cannot localize
    # the fact, so it is dropped from the density search.
    #
    # The cap is RELATIVE to the question's TOTAL term coverage on the
    # page: the chrome terms produce orders of magnitude more matches
    # than the content terms that carry the fact ("facts" ×1, "union"
    # ×454 on the EU page). Keeping the terms with at most half of the
    # total match count drops the chrome while the content terms
    # survive; a term absent from the page is dropped as well (it
    # matches nothing and cannot localize).
    counts = {s: tl.count(s) for s in terms if tl.count(s) > 0}
    if not counts:
        return AssertionWindow(text=text[:budget], start=-1)
    total = sum(counts.values())
    cap = max(_MIN_TERM_FREQ, total // 2)
    kept = sorted(
        (s for s, c in counts.items() if c <= cap and s not in _CHROME),
        key=lambda s: (counts[s], s),
    )
    if not kept:
        return AssertionWindow(text=text[:budget], start=-1)

    matches: list[int] = []
    term_of: list[str] = []
    for stem in kept:
        i = tl.find(stem)
        while i >= 0:
            matches.append(i)
            term_of.append(stem)
            i = tl.find(stem, i + 1)
    order = sorted(range(len(matches)), key=matches.__getitem__)
    positions = [matches[i] for i in order]
    stems = [term_of[i] for i in order]

    # sliding window over the sorted match positions: for each left
    # anchor, count the DISTINCT stems within [anchor, anchor + span].
    # The two pointers advance monotonically → O(n matches).
    #
    # Score = distinct stems
    #       + a number bonus (a fact is a VALUE: "27 members", "3.14.7",
    #         "14,00%" — the number is its anchor; tie-break only)
    #       + a decaying bonus for proximity to the start of the text
    #         (the infobox/lead bias above).
    # Ties (same score) go to the EARLIEST position.
    #
    # The number bonus is checked in a WIDER context than the density
    # span (a fact sentence plus its neighbors): the number "27" sits a
    # few hundred chars after the "union" match in the EU infobox, and
    # the 400-char span around "union" alone would miss it.
    num_context = 1_500
    best_score = 0.0
    best_pos = positions[0]
    r = 0
    present: dict[str, int] = {}
    for left in range(len(positions)):
        lo = positions[left]
        hi = lo + 2 * match_span
        while r < len(positions) and positions[r] <= hi:
            s = stems[r]
            present[s] = present.get(s, 0) + 1
            r += 1
        num_bonus = (
            _NUMBER_BONUS_MAX if _has_number(tl[lo : hi + 1 + num_context]) else 0.0
        )
        start_bonus = (
            _START_BONUS_MAX * (1.0 - lo / _START_BONUS_DECAY)
            if lo < _START_BONUS_DECAY
            else 0.0
        )
        score = len(present) + num_bonus + start_bonus
        if score > best_score:
            best_score = score
            best_pos = lo
        sl = stems[left]
        present[sl] -= 1
        if present[sl] == 0:
            del present[sl]

    start = max(0, best_pos - lead)
    return AssertionWindow(text=text[start : start + budget], start=start)
