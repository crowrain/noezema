"""Question actors: closed catalog of named actors and per-session actor-search tracking (T7.137).

Why this module exists (ADR-0036 §10, measurement of 2026-10-27): sessions whose QUESTION names
several catalog actors burn steps on repeated AGGREGATE searches — one query naming four or five
banks at once. The fixture replay (manager's fixture, 71 finished sessions, 423 executed searches)
shows 41 aggregate searches of which 22 (54 %) were followed by no read at all, versus 30 % for
single-actor searches; twenty-six of them are second-and-later aggregates in the same session.
The orchestrator gate next to the near-duplicate gate denies the next aggregate `web.search`
once one aggregate has already executed; this module holds the recognition and the per-session
tracker (pure, no DB/network — AGENTS §4), the gate itself stays in `orchestrator._explorer_loop`.

The catalog is CLOSED and lives IN CODE, decided against configs: it names the actors the operator
put in the benchmark questions (Bank of Russia, Sber/SberCIB, VTB, Alfa-Bank, Gazprombank,
Rosstat, hh.ru, SuperJob, Minek, Minfin). A question naming an actor outside the catalog is NOT
gated at all — the gate can only fire on catalog names; the limitation and the upgrade path are
recorded in ADR-0036 §10. Recognition reuses the SAME normalization as the near-duplicate gate
(`content_word_stems` / `normalize_search_query`), so «Банка России», «SberCIB», «Альфа-Банка»,
«Газпромбанка» are found by the same stem logic the gates already trust; bare «банк» without a
qualifier is deliberately NOT an actor.

Amendment T7.137a (measurement of 2026-10-28, ADR-0036 §10a): a fixed-prefix stem is a truncation,
and a truncated base is not always the name — «Газпром» collapses to the stem of «Газпромбанк», so
«Газпром нефть» and «акции Газпрома» named Gazprombank; «альфа» named Alfa-Bank in
«альфа-коэффициент»; and the same truncation MISSED names the stand really saw: mixed-script
«СберCIB» mixed-script form, «в Сбере», singular «Центральный банк» without «России», «Минэк».
For every name whose truncated base is ambiguous (Sberbank, Alfa-Bank, Gazprombank) recognition now
goes through EXPLICIT CLOSED WORD TEMPLATES — the same device `_BANK_OF_RUSSIA_RE` already used —
and not through stems; the unambiguous names keep stem recognition. The templates are the one truth
about Sberbank name forms: the vitrine forecast series (apps/web/forecast_series.py, T7.138 2б)
bridges «Сбербанк»/«Сбера» to the `sbercib` producer through `names_sberbank`, it does not keep its
own copy of the forms.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Final

from apps.orchestrator.tool_context import content_word_stems

#: «Банк России» and its inflections: adjacency of the two words, singular only (the same
#: precedent as `research_hints._OFFICIAL_CENTRAL_BANK_RE` — «банки России» is not the marker).
_BANK_OF_RUSSIA_RE: Final = re.compile(r"\bбанк(?:а|у|ом|е)?\s+россии\b")

#: T7.137a: the same actor named without the «России» half — «Центральный банк» in the SINGULAR.
#: Adjective endings are listed one by one on purpose: the plural «центральные банки» (and
#: «центральных банков») stays a non-marker, as «банки России» already was.
_CENTRAL_BANK_SINGULAR_RE: Final = re.compile(
    r"\bцентральн(?:ый|ого|ому|ым|ом|ая|ой|ую|ое)\s+банк(?:а|у|ом|е)?\b"
)

#: T7.137a: closed word templates instead of stems for names whose truncated base is ambiguous.
#: Sberbank — the forms the stand saw («Сбербанк», «Сбера», «в Сбере», «Сберу», mixed-script
#: «СберCIB»); `(?![а-яё])` and the «индекс» refusal keep «СберИндекс» (dictionary key `sberindex`,
#: a DIFFERENT producer) out of this template.
SBERBANK_NAME: Final[str] = "Сбербанк"
_SBERBANK_RE: Final = re.compile(
    r"(?<![а-яё])сбер(?:банк[а-яё]{0,4}|а|е|у|ом)?(?![а-яё])(?!\s*индекс[а-яё]*)"
    r"|(?<![a-z])sber[\s-]*cib\b|(?<![a-z])sberbank\b",
    re.IGNORECASE,
)
_ALFA_BANK_RE: Final = re.compile(
    r"(?<![а-яё])альфа[\s\-–—]?банк[а-яё]*(?![а-яё])|(?<![a-z])alfa[\s-]?bank\b",
    re.IGNORECASE,
)
_GAZPROMBANK_RE: Final = re.compile(
    r"(?<![а-яё])газпромбанк[а-яё]*(?![а-яё])|(?<![a-z])gazprom[\s-]?bank\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class CatalogActor:
    """One closed-catalog actor: display name for notes, recognized word stems, and an optional
    adjacency pattern for two-word names whose parts alone are ordinary words («банк», «россии»).
    T7.137a: a name whose truncated base is ambiguous carries a CLOSED WORD TEMPLATE instead of
    stems; such an entry has no stems at all, so nothing but the template can name it."""

    name: str
    stems: frozenset[str]
    phrase: re.Pattern[str] | None = None


def _actor(name: str, words: str, phrase: re.Pattern[str] | None = None) -> CatalogActor:
    """Catalog entry from a space-separated list of known word forms (stemmed once, here)."""
    return CatalogActor(name=name, stems=frozenset(content_word_stems(words)), phrase=phrase)


def _actor_by_template(name: str, template: re.Pattern[str]) -> CatalogActor:
    """Catalog entry recognized ONLY by its closed word template (T7.137a)."""
    return CatalogActor(name=name, stems=frozenset(), phrase=template)


#: The closed catalog (order is stable: notes list actors in this order). Forms are the ones the
#: stand actually saw in question texts and search queries; stems collapse the rest of the cases.
#: Sberbank, Alfa-Bank and Gazprombank are template-only since T7.137a (see the module docstring):
#: their bases «сбер», «альфа», «газпром» are shared with other names, so a stem over-names them.
ACTOR_CATALOG: Final[tuple[CatalogActor, ...]] = (
    _actor(
        "Банк России",
        "ЦБ Центробанк Центробанка cbr",
        re.compile(_BANK_OF_RUSSIA_RE.pattern + "|" + _CENTRAL_BANK_SINGULAR_RE.pattern),
    ),
    _actor_by_template("Сбербанк", _SBERBANK_RE),
    _actor("ВТБ", "ВТБ"),
    _actor_by_template("Альфа-Банк", _ALFA_BANK_RE),
    _actor_by_template("Газпромбанк", _GAZPROMBANK_RE),
    _actor("Росстат", "Росстат Росстата"),
    _actor("hh.ru", "hh HeadHunter"),
    _actor("SuperJob", "SuperJob"),
    _actor("Минэкономразвития", "Минэкономразвития Минэк"),
    _actor("Минфин", "Минфин Минфина"),
)


def names_sberbank(text: str) -> bool:
    """Does a text name Sberbank by one of the closed name forms of the catalog entry?
    Exported for the vitrine forecast series (T7.138 2б, `apps/web/forecast_series.py`): the bridge
    «Сбербанк» -> producer `sbercib` must recognize name forms the SAME way the catalog does, so
    there is one truth about those forms and no second normalization dictionary."""
    if not text:
        return False
    return _SBERBANK_RE.search(text.lower().replace("ё", "е")) is not None


def named_actors(text: str) -> tuple[str, ...]:
    """Actors of the closed catalog named in a text, in catalog order.

    Stem matching (same collapse as the near-duplicate gate) plus the «Банк России» adjacency.
    Bare «банк», «банки», «банков» alone match nothing: no actor stem is a common word — this is
    the property the fixture measurement leans on («аналитики банков» names no catalog actor)."""
    if not text:
        return ()
    stems = content_word_stems(text)
    lowered = text.lower().replace("ё", "е")
    return tuple(
        actor.name
        for actor in ACTOR_CATALOG
        if (actor.stems & stems) or (actor.phrase is not None and actor.phrase.search(lowered))
    )


def named_question_actors(question_text: str) -> tuple[str, ...]:
    """Actors the QUESTION names — the set the gate and the step-context block are built on."""
    return named_actors(question_text)


def named_query_actors(query: str) -> tuple[str, ...]:
    """Actors a SEARCH QUERY names (same recognition as the question text)."""
    return named_actors(query)


#: Threshold of the multi-actor rule (ADR-0036 §10): the gate fires only on questions naming at
#: least this many catalog actors, and an AGGREGATE query is one naming at least this many of
#: them. Fixed in code (not a config threshold) by decision: it is part of the rule's meaning,
#: and configs were off-limits for this task.
MULTI_ACTOR_MIN: Final = 3

#: A query counts as a SEPARATE search of an actor when it names that actor and at most this
#: many other named actors (one aggregate query never marks anyone covered).
SEPARATE_SEARCH_MAX_ACTORS: Final = 2


@dataclass
class ActorSearchTracker:
    """Per-session tracker (mirrors the `session_search_queries` list of the near-duplicate gate):
    which question-named actors already had a SEPARATE search, and whether one aggregate search
    has already EXECUTED (armed by successful searches only — the same mechanism as the
    near-duplicate gate, so a failed search arms nothing and cannot be «read» afterwards).

    `repeat_refusal` is the pure decision of the orchestrator gate; `uncovered` feeds the
    step-context block and the denial note."""

    named: tuple[str, ...]
    covered: set[str] = field(default_factory=set)
    aggregate_query: str | None = None
    aggregate_step: int | None = None

    def _query_actors(self, query: str) -> set[str]:
        return set(named_query_actors(query)) & set(self.named)

    def is_aggregate(self, query: str) -> bool:
        """Query names at least MULTI_ACTOR_MIN of the actors the QUESTION named."""
        return len(self._query_actors(query)) >= MULTI_ACTOR_MIN

    def repeat_refusal(self, query: str) -> tuple[str, int] | None:
        """None if the search is legal; otherwise (prior aggregate query, its step) — the denial
        payload data. Silent for questions naming < MULTI_ACTOR_MIN actors, before the first
        executed aggregate, and for every query naming <= SEPARATE_SEARCH_MAX_ACTORS of them."""
        if len(self.named) < MULTI_ACTOR_MIN or self.aggregate_step is None:
            return None
        if not self.is_aggregate(query):
            return None
        return (self.aggregate_query or "", self.aggregate_step)

    def mark_executed(self, query: str, step: int) -> None:
        """Record an EXECUTED (successful) web.search of this session: coverage grows only from
        separate searches; the first aggregate arms the gate."""
        found = self._query_actors(query)
        if len(found) > SEPARATE_SEARCH_MAX_ACTORS:
            if self.aggregate_step is None and found:
                self.aggregate_query, self.aggregate_step = query, step
        elif found:
            self.covered |= found

    def uncovered(self) -> tuple[str, ...]:
        """Question-named actors without a separate search yet, in catalog order."""
        return tuple(name for name in self.named if name not in self.covered)


def uncovered_for(
    named: Sequence[str], executed_queries: Sequence[tuple[str, int]]
) -> tuple[str, ...]:
    """Helper for tests/notes: uncovered actors after replaying (query, step) searches. The
    orchestrator keeps its own `ActorSearchTracker`; this is the same rule as a pure function."""
    tracker = ActorSearchTracker(named=tuple(named))
    for query, step in executed_queries:
        tracker.mark_executed(query, step)
    return tracker.uncovered()
