"""Host-tracked coverage of the sources NAMED IN THE QUESTION (T7.21,
EVAL-3d post-mortem, §3.7, §5.4, §11.2, ADR-0010).

The question is the operator-trusted input of the session. A question
may name the sources the answer must rest on (the EVAL-3 corpus v2
convention: «строго по этим двум источникам: URL1 и URL2»). Whether those
named sources were actually fetched is a grade-relevant input (two
independent source groups are the precondition of the E3 rule for
external/temporal claims), and per §3.7/§11.2 such inputs are produced
by the TRUSTED HOST, not the model:

- the host extracts the named URLs (``extract_question_urls``, T7.17);
- the host tracks, per named source, whether it was FETCHED (a
  successful ``research.fetch`` of a matching URL) or ERRORED (a failed
  ``research.fetch`` of a matching URL — the source was requested and
  the egress gave a definitive answer, so further retries cannot help);
- the host withholds the release of the session to CONSOLIDATION until
  every named source is fetched or errored (the explorer's ``complete``
  is rejected with a host observation; the budget bounds the loop).

A named source is matched to a fetch by ``coverage_key``: the
registrable domain (the same PSL-style normalization as the
source-independence groups, §11.3 — ``www.`` is not a different
source) plus the percent-decoded path (and query, when present),
case-folded. The scheme is NOT part of the key (``http://`` and
``https://`` of the same host+path are the same named source).

The module is pure (no DB, no LLM): the orchestrator owns one tracker
per session and feeds it the executed ``research.fetch`` calls.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from urllib.parse import unquote, urlsplit

from packages.memory.independence import registrable_domain
from packages.memory.scope import extract_question_urls

#: a fetch of this status counts as "the named source was requested and
#: got a definitive answer" (a timeout, a non-200, an SSRF refusal):
#: the source is covered, the session may complete
STATUS_PENDING = "pending"
STATUS_FETCHED = "fetched"
STATUS_ERRORED = "errored"


def named_source_urls(question_text: str) -> tuple[str, ...]:
    """The http(s) URLs the question names (deduplicated, order kept;
    the closed ``extract_question_urls`` extractor, T7.17)."""
    return extract_question_urls(question_text)


def coverage_key(url: str) -> str | None:
    """The canonical key of a named source / fetched URL, or None when
    the value is not an http(s) URL with a host.

    - host: the registrable domain (PSL-style; ``www.`` stripped) —
      the same normalization the source-independence groups use, so a
      fetch of ``www.un.org/...`` covers the named ``un.org/...``;
    - path: percent-decoded, case-folded, trailing slashes stripped
      (``/en/about-us/`` == ``/en/about-us``);
    - query: percent-decoded, case-folded, kept (different query =
      different page);
    - scheme: not part of the key (http/https of the same host+path is
      the same named source).
    """
    parts = urlsplit(url.strip())
    if parts.scheme.lower() not in ("http", "https") or not parts.hostname:
        return None
    domain = registrable_domain(url)
    if domain is None:
        return None
    path = unquote(parts.path or "").lower().rstrip("/")
    query = unquote(parts.query or "").lower() if parts.query else ""
    key = f"{domain}{path}"
    if query:
        key = f"{key}?{query}"
    return key


@dataclass
class SourceCoverageTracker:
    """Per-session coverage of the question's named sources.

    ``mark`` is fed every EXECUTED ``research.fetch`` (success or
    error) that the policy engine and the repeat guard let through;
    a call refused before execution (policy deny, repeat denial) never
    marks anything — it requested no source.

    A named URL that ``coverage_key`` cannot normalize is UNTRACKABLE
    (defense-in-depth: the ``extract_question_urls`` extractor only
    yields http(s) URLs, so this branch is unreachable from the
    orchestrator). Untrackable URLs do not block the gate — an
    unfetched source can never produce evidence, so ignoring it cannot
    inflate a grade — but they are reported separately
    (``report()["untracked"]``) so the audit stays transparent.
    """

    named: tuple[str, ...]
    _status: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self._named_keys: list[tuple[str, str]] = []
        self._untracked: list[str] = []
        for url in self.named:
            key = coverage_key(url)
            if key is None:
                self._untracked.append(url)
                continue
            self._named_keys.append((key, url))
            if key not in self._status:
                self._status[key] = STATUS_PENDING

    def mark(self, fetched_url: str, ok: bool) -> None:
        """Register an executed ``research.fetch`` of ``fetched_url``.

        Success covers the matching named source as ``fetched``; a
        failure covers it as ``errored``. Once covered, a source stays
        covered (a later success cannot un-cover an errored one and
        vice versa — the egress already answered).
        """
        key = coverage_key(fetched_url)
        if key is None or self._status.get(key) != STATUS_PENDING:
            return
        self._status[key] = STATUS_FETCHED if ok else STATUS_ERRORED

    def uncovered(self) -> tuple[str, ...]:
        """The named sources still ``pending`` (the first spelling in
        question order per source; two spellings of the same source —
        same ``coverage_key`` — count as one pending source)."""
        seen: set[str] = set()
        out: list[str] = []
        for key, url in self._named_keys:
            if self._status[key] == STATUS_PENDING and key not in seen:
                seen.add(key)
                out.append(url)
        return tuple(out)

    @property
    def is_complete(self) -> bool:
        return not self.uncovered()

    def report(self) -> dict[str, Any]:
        """The auditable state: named / fetched / errored / uncovered
        (original URL spellings, question order)."""
        by_url: dict[str, str] = {}
        for key, url in self._named_keys:
            by_url[url] = self._status[key]
        return {
            "named": list(self.named),
            "fetched": [u for u in self.named if by_url.get(u) == STATUS_FETCHED],
            "errored": [u for u in self.named if by_url.get(u) == STATUS_ERRORED],
            "uncovered": list(self.uncovered()),
            "untracked": list(self._untracked),
        }
