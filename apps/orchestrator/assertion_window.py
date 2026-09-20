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

T7.22 (ADR-0011, §6.4): the single term-density window misses the
assertion in a measured 7 of 12 EVAL-3d group-A cases — the fact lives
in the lead/infobox (Wikipedia), the first paragraph (a news page), or
a data widget (cbr.ru), a region the term density skips (or lands 200–300
chars short of) while the term-densest region is the TOC (which repeats
the question's words) or an unrelated body section. ``select_assertion_windows``
adds a SECOND, non-overlapping "value" window: the budget window around
the number-position where a significant number (a data value, not a
date/year/TOC index) co-occurs with the question's terms inside the
fact zone (the first ``_FACT_ZONE_DECAY`` chars — beyond that, the deep
number-dense regions on long pages are data tables, not assertions).
The primary window and its selection are unchanged.
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


# ── T7.22 (ADR-0011): the second, value-anchored window ─────────────────


#: T7.22: fact-zone decay for the value window's score. The fact the
#: question is about is stated FIRST on the page (lead/infobox); on the
#: measured EVAL-3d corpus all seven recoverable group-A facts sit at
#: offsets ≤ 10k, while the deep number-dense regions past ~12k on the
#: long pages (Wikipedia country stats tables, historical population
#: tables, UN accession lists) are data tables, not assertions. The
#: candidate score decays linearly to zero at this offset, so a deep
#: table can never outrank a lead fact. Derived from the EVAL-3d
#: measurements (ADR-0011 §2).
_FACT_ZONE_DECAY = 16_000

#: T7.22: weight of one significant number in the value-window score
#: (relative to one distinct question term). A fact is a VALUE ("193
#: государства", "8.3 billion", "14,00%") — the number is its anchor —
#: so a region with two values beats a region with one more term and
#: no values; but a single value without any term co-occurrence is not
#: enough to override the term signal (the 2:1 ratio keeps the terms
#: dominant, the same tie-break discipline as _NUMBER_BONUS_MAX).
_VALUE_WEIGHT = 2.0

#: digit runs (lowercased text)
_NUMBER_RE = re.compile(r"\d+")

#: maximal dot-joined groups ("27.07.2026" is ONE match, "8.3" is one,
#: "8,000,000,000" is nine separate digit runs — the comma is not a dot)
_DOTTED_RE = re.compile(r"\d+(?:\.\d+)*")

#: a dot-separated Russian date ("27.07.2026") — a date, not a value
_ISO_DATE_RE = re.compile(r"^\d{1,2}\.\d{1,2}\.\d{4}$")

#: a MediaWiki TOC section index ("1.1 Background") is a decimal whose
#: digit groups are all single-digit and which is immediately followed
#: by the section title (a capitalized word). A data value with the
#: same shape ("8.3 billion", "66.2% Christianity") is followed by a
#: lowercase word or a punctuation mark — the one reliable
#: deterministic separator between the two.
_TOC_INDEX_AFTER = re.compile(r"\s+[A-ZА-ЯЁ]")

#: T7.22: TOC chrome markers (in the lowercased normalized text). A
#: span containing one of these is a table-of-contents / navigation
#: block, never an assertion region: the TOC repeats the page's section
#: titles (which echo the question's words) and its section indexes
#: (which look like data values). Measured on the EVAL-3d corpus:
#: en.wikipedia carries "move to sidebar" / "toggle ... subsection",
#: ru.wikipedia carries «переместить в боковую панель» /
#: «Отобразить/Скрыть подраздел».
_TOC_MARKERS = (
    "move to sidebar",
    "переместить в боковую панель",
    "отобразить",
    "подраздел",
)


def _is_table_of_contents(span: str) -> bool:
    """True when the span is a TOC / navigation block (see
    _TOC_MARKERS). ``span`` is lowercased normalized text."""
    if any(marker in span for marker in _TOC_MARKERS):
        return True
    return "toggle" in span and "subsection" in span


def _significant_numbers(span: str) -> int:
    """Count the DATA VALUES in the span (T7.22, ADR-0011).

    A significant number is what an assertion actually states:
    - 3+ digits, unless a plausible year (1900–2099): "193", "370",
      "8,000,000,000" (each comma-separated digit run counts);
    - a decimal value ("8.3", "66.2", "18.6") — except a dot-separated
      date ("27.07.2026") and a TOC section index ("1.1 Background");
    - any length immediately before "%": "4,0%" → the "0" before "%".

    Excluded on purpose: 4-digit years (the question is almost always
    about a value, and a page of dates — a Wikipedia TOC's
    "(1948–1957)" entries, an accession list — must not look dense),
    1–2 digit integers without a value context (TOC indexes, list
    numbers, "27" in "27 member states" carries no weight of its own —
    the co-occurring question term localizes it), and ISO dates.
    """
    n = 0
    for m in _DOTTED_RE.finditer(span):
        s = m.group()
        parts = s.split(".")
        if len(parts) > 1:
            if _ISO_DATE_RE.match(s):
                continue
            if all(len(p) == 1 for p in parts) and _TOC_INDEX_AFTER.match(
                span[m.end() : m.end() + 8]
            ):
                continue
            n += 1
        else:
            v = int(s)
            if (len(s) >= 3 and not (len(s) == 4 and 1900 <= v <= 2099)) or (
                span[m.end() : m.end() + 1] == "%"
            ):
                n += 1
    return n


def _fact_region_candidates(
    text: str, question: str, match_span: int
) -> list[tuple[int, float, int, int]]:
    """Value-window candidates (T7.22): ``(pos, score, terms, values)``
    for every number-position anchor ``pos`` in the fact zone
    (``pos < _FACT_ZONE_DECAY``).

    score = (distinct question terms in the ``2*match_span`` span
            + _VALUE_WEIGHT * significant numbers in the span)
            * (1 - pos / _FACT_ZONE_DECAY)

    The terms are the RAW ``extract_terms`` output — WITHOUT the
    page-wide dominant-term cap and WITHOUT the _CHROME filter that
    protect the primary window: the value window is a secondary anchor,
    and the cap would erase the only signal on cross-language pages
    (where every question term is "dominant" or absent), while the
    decay keeps a deep number-dense table from outranking the lead.
    ``score`` is 0.0 for anchors with neither signal (they are filtered
    out by the caller, along with TOC spans).
    """
    tl = text.lower()
    terms = extract_terms(question) if question else []
    anchors = [m.start() for m in _NUMBER_RE.finditer(tl) if m.start() < _FACT_ZONE_DECAY]
    out: list[tuple[int, float, int, int]] = []
    for pos in anchors:
        span = tl[pos : pos + 2 * match_span]
        t = sum(1 for s in terms if s in span)
        sig = _significant_numbers(span)
        if t == 0 and sig == 0:
            continue
        score = (t + _VALUE_WEIGHT * sig) * (1.0 - pos / _FACT_ZONE_DECAY)
        out.append((pos, score, t, sig))
    return out


def select_assertion_windows(
    text: str,
    question: str,
    budget: int,
    *,
    max_windows: int = 2,
    match_span: int = _MATCH_SPAN,
    lead: int = _LEAD,
) -> list[AssertionWindow]:
    """Pick up to ``max_windows`` NON-OVERLAPPING ``budget``-char
    fragments for the ``source_assertion`` payload (T7.22, ADR-0011).

    Window 1 — the T7.16 term-density window
    (``select_assertion_window``, unchanged): where the question's
    content terms are densest.
    Window 2 — the value window: the budget window around the best
    fact-zone candidate of ``_fact_region_candidates``. EVAL-3d group A
    (ADR-0010 §2 / ADR-0011 §2) — 12 claims where the second source was
    fetched but the curator attached one — showed the term window misses
    the assertion in 7 of 12: the fact sits in the lead/infobox, the
    first paragraph, or a data widget, and the term-densest region is
    the TOC or an unrelated body section.

    Fallbacks (each degrades to the T7.16 behavior):
    - text shorter than the budget → the whole text, one window;
    - no value candidate (no numbers at all, or every candidate is a
      TOC span or scores 0) → the primary window alone;
    - the primary window is the leading-prefix fallback (``start=-1``,
      no term matched — a cross-language page) and a value window
      exists → the value window REPLACES the prefix: the prefix is
      navigation chrome and the value window is the only content
      signal.

    Deterministic: ties go to the EARLIEST candidate; the value window
    never overlaps the primary window (the next-best non-overlapping
    candidate is chosen instead).
    """
    if budget <= 0:
        raise ValueError(f"budget must be positive, got {budget}")
    if max_windows < 1:
        raise ValueError(f"max_windows must be >= 1, got {max_windows}")
    if len(text) <= budget:
        return [AssertionWindow(text=text, start=0)]
    primary = select_assertion_window(
        text, question, budget, match_span=match_span, lead=lead
    )
    if max_windows == 1:
        return [primary]

    best_start = -1
    best_score = 0.0
    tl = text.lower()
    for pos, score, _t, _sig in _fact_region_candidates(text, question, match_span):
        if score <= 0.0:
            continue
        if _is_table_of_contents(tl[pos : pos + 2 * match_span]):
            continue
        start = max(0, pos - lead)
        end = min(len(text), start + budget)
        if primary.start >= 0 and start < primary.start + budget and end > primary.start:
            continue  # overlaps the primary window → next-best candidate
        if score > best_score:
            best_start = start
            best_score = score
    if best_start < 0:
        return [primary]
    if primary.start < 0:
        # cross-language fallback: the prefix is chrome, keep only the
        # value window (it is the only content signal on the page)
        return [AssertionWindow(text=text[best_start : best_start + budget], start=best_start)]
    return [
        primary,
        AssertionWindow(text=text[best_start : best_start + budget], start=best_start),
    ]
