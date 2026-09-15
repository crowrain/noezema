"""Conservative source independence groups (T3.5, extended in T4.7).

Sources are grouped so that two sources in the SAME group are NOT
independent evidence for each other. The grouping is deliberately
conservative (§11.3: it may UNDER-estimate independence, never
over-grade):

v1 (T3.5) — ``group_sources``:

- PSL-style registrable domain (a small built-in multi-part suffix list;
  unknown suffix shape falls back to the last two labels);
- URI normalization (scheme/host case, default ports, fragments,
  trailing slash, ``www.``);
- text overlap (word-set Jaccard above the threshold merges groups);
- a source with unknown lineage (no URI and no content hash) lands in a
  single shared ``unknown`` group — never a false independence.

v2 (T4.7) — ``group_source_graph``: adds the full source graph,
§11.3/§14:

- identical ``content_hash`` (100% text overlap — one document, N
  mirrors, is one group);
- canonical/parent source (``parent_source_id``) merges child and
  parent, and two children of one parent merge through it;
- valid ``source_dependency_edges`` (link to a primary source, derived
  content, quote, republish) merge the pair;
- valid ``source_graph_corrections``: kind ``merge`` asserts a merge
  with a provenance chain; kind ``split`` is the correction of a false
  merge — it cancels the DIRECT edge/correction merge basis between the
  pair only (v1 decision: a split can never undo the algorithmic
  facts — shared domain, parent, content hash, text overlap — those
  are data, not graph relations; to change them the data changes).
  A valid split beats a conflicting explicit merge (fail-closed: the
  conservative direction is to keep the groups separate).
  An operator ATTESTATION never splits a group — only a correction
  with a verifiable chain does (§11.3).

The snapshot (algorithm version, thresholds, fingerprints) is stored
with the assessment so a future algorithm change cannot silently
re-grade old knowledge.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from packages.domain.canonical import canonical_sha256
from packages.memory.evidence import (
    INDEPENDENCE_ALGORITHM_VERSION,
    SOURCE_GRAPH_ALGORITHM_VERSION,
    URI_NORMALIZER_VERSION,
)

#: small built-in multi-part public suffixes (conservative MVP subset)
MULTI_PART_SUFFIXES: frozenset[str] = frozenset(
    {
        "co.uk", "org.uk", "gov.uk", "ac.uk", "net.uk", "sch.uk",
        "com.au", "net.au", "org.au", "edu.au", "gov.au",
        "com.br", "org.br", "gov.br",
        "com.cn", "org.cn", "gov.cn", "net.cn",
        "co.jp", "ne.jp", "or.jp", "ac.jp", "go.jp",
        "co.in", "org.in", "net.in", "ac.in", "gov.in",
        "com.mx", "org.mx", "gob.mx",
        "com.tr", "org.tr", "gov.tr",
        "com.tw", "org.tw", "gov.tw",
        "co.kr", "or.kr",
        "com.hk", "org.hk", "gov.hk",
        "com.sg", "org.sg", "gov.sg",
        "co.nz", "org.nz",
        "com.ar", "com.co", "com.pe", "com.pl",
        "uk.com", "au.com", "br.com", "cn.com", "jp.com",
        "kr.com", "se.com", "za.com",
    }
)

TEXT_OVERLAP_THRESHOLD = 0.8

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)

DEFAULT_PORTS = {"http": 80, "https": 443, "ftp": 21}


def psl_fingerprint() -> str:
    return canonical_sha256(sorted(MULTI_PART_SUFFIXES))


def normalize_uri(uri: str) -> str:
    """Normalize a URI for domain extraction (§14.3, T3.5)."""
    parts = urlsplit(uri.strip())
    scheme = parts.scheme.lower()
    host = parts.hostname.lower() if parts.hostname else ""
    if host.startswith("www."):
        host = host[4:]
    port = parts.port
    if port is not None and port == DEFAULT_PORTS.get(scheme):
        port = None
    netloc = host + (f":{port}" if port else "")
    path = parts.path or ""
    if len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    query = f"?{parts.query}" if parts.query else ""
    return f"{scheme}://{netloc}{path}{query}"


def registrable_domain(uri: str) -> str | None:
    """The PSL-style registrable domain of a URI (conservative).

    Find the longest (up to 3 labels) trailing match in the built-in
    multi-part suffix list; the registrable domain is one label above it.
    With no match, the registrable domain is the last two labels."""
    parts = urlsplit(normalize_uri(uri))
    host = parts.hostname or ""
    if not host:
        return None
    if host.replace(".", "").isdigit() or ":" in host:  # IP literal
        return host
    labels = host.split(".")
    if len(labels) < 2:
        return host
    for n in (3, 2):
        if len(labels) >= n and ".".join(labels[-n:]) in MULTI_PART_SUFFIXES:
            if len(labels) > n:
                return ".".join(labels[-n - 1 :])
            return ".".join(labels[-n:])
    return ".".join(labels[-2:])


def _word_set(text: str) -> frozenset[str]:
    return frozenset(w.lower() for w in _WORD_RE.findall(text))


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    union = len(a | b)
    return len(a & b) / union if union else 0.0


@dataclass(frozen=True)
class IndependenceInput:
    """One source as the grouping algorithm sees it."""

    source_id: Any
    canonical_uri: str | None = None
    content_hash: str | None = None
    sample_text: str | None = None


@dataclass(frozen=True)
class IndependenceResult:
    algorithm_version: str
    thresholds: dict[str, float]
    psl_fingerprint: str
    uri_normalizer_version: str
    groups: dict[Any, str]  # source_id -> group_id
    bases: dict[Any, str]  # source_id -> why it is in its group


def group_sources(sources: list[IndependenceInput]) -> IndependenceResult:
    """Conservative grouping: returns group_id per source_id.

    Two sources are in one group when: same registrable domain, or text
    overlap above the threshold, or both have unknown lineage."""
    parent: dict[Any, Any] = {s.source_id: s.source_id for s in sources}

    def find(x: Any) -> Any:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: Any, b: Any) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    by_domain: dict[str, list[Any]] = {}
    unknown: list[Any] = []
    texts: dict[Any, frozenset[str]] = {}

    for s in sources:
        domain = registrable_domain(s.canonical_uri) if s.canonical_uri else None
        if domain is None and s.content_hash is None:
            unknown.append(s.source_id)
            continue
        if domain is not None:
            by_domain.setdefault(domain, []).append(s.source_id)
        if s.sample_text:
            texts[s.source_id] = _word_set(s.sample_text)

    for ids in by_domain.values():
        for other in ids[1:]:
            union(ids[0], other)

    ids = list(texts)
    for i in range(len(ids)):
        for j in range(i + 1, len(ids)):
            if _jaccard(texts[ids[i]], texts[ids[j]]) >= TEXT_OVERLAP_THRESHOLD:
                union(ids[i], ids[j])

    for sid in unknown[1:]:
        union(unknown[0], sid)

    # stable group names: g0, g1, ... in first-seen order
    group_name: dict[Any, str] = {}
    groups: dict[Any, str] = {}
    bases: dict[Any, str] = {}
    for s in sources:
        root = find(s.source_id)
        if root not in group_name:
            group_name[root] = f"g{len(group_name)}"
        gid = group_name[root]
        groups[s.source_id] = gid
        if s.source_id in unknown:
            bases[s.source_id] = "unknown_lineage"
        else:
            domain = registrable_domain(s.canonical_uri) if s.canonical_uri else None
            bases[s.source_id] = f"domain:{domain}" if domain else "content_cluster"

    return IndependenceResult(
        algorithm_version=INDEPENDENCE_ALGORITHM_VERSION,
        thresholds={"text_overlap": TEXT_OVERLAP_THRESHOLD},
        psl_fingerprint=psl_fingerprint(),
        uri_normalizer_version=URI_NORMALIZER_VERSION,
        groups=groups,
        bases=bases,
    )


# ── T4.7: the full source graph (§11.3, §14) ──────────────────────────


@dataclass(frozen=True)
class SourceGraphInput:
    """One source as the full-graph algorithm sees it (T4.7)."""

    source_id: Any
    canonical_uri: str | None = None
    content_hash: str | None = None
    sample_text: str | None = None
    parent_source_id: Any = None


@dataclass(frozen=True)
class SourceGraphEdge:
    """A valid source_dependency_edges row (kind ∈ the §14 closed set)."""

    edge_id: Any
    from_id: Any
    to_id: Any
    kind: str


@dataclass(frozen=True)
class SourceGraphCorrection:
    """A source_graph_corrections row (validity decided by the caller's
    query — pass only what the algorithm should see, but keep ``valid``
    explicit so an invalid row is a no-op even if it slips through)."""

    correction_id: Any
    from_id: Any
    to_id: Any
    kind: str  # 'merge' | 'split'
    valid: bool = True


def _short(x: Any) -> str:
    return str(x)[:12]


def group_source_graph(
    sources: list[SourceGraphInput],
    edges: Sequence[SourceGraphEdge] = (),
    corrections: Sequence[SourceGraphCorrection] = (),
) -> IndependenceResult:
    """Conservative grouping over the FULL source graph (T4.7, §11.3).

    Merge bases, in recording priority: canonical/parent source,
    explicit merge correction, dependency edge, shared registrable
    domain, identical content hash, text overlap above the threshold.
    A valid ``split`` correction cancels the direct edge/correction
    basis between its pair (and beats a conflicting explicit merge);
    it can never undo an algorithmic fact (domain/parent/content/text).
    Lineage-unknown sources (no URI, no content hash, no parent, no
    sample text) share one conservative group. Deterministic: the input
    order fixes the group names (callers sort by source id)."""
    ids = [s.source_id for s in sources]
    by_id: dict[Any, SourceGraphInput] = {s.source_id: s for s in sources}
    node_ids: set[Any] = set(ids)

    parent: dict[Any, Any] = {i: i for i in ids}

    def find(x: Any) -> Any:
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a: Any, b: Any) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    # v1 facts (domain / text overlap / unknown lineage)
    by_domain: dict[str, list[Any]] = {}
    unknown: list[Any] = []
    texts: dict[Any, frozenset[str]] = {}
    by_content: dict[str, list[Any]] = {}
    for s in sources:
        domain = registrable_domain(s.canonical_uri) if s.canonical_uri else None
        if domain is None and s.content_hash is None:
            unknown.append(s.source_id)
            continue
        if domain is not None:
            by_domain.setdefault(domain, []).append(s.source_id)
        if s.content_hash is not None:
            by_content.setdefault(s.content_hash, []).append(s.source_id)
        if s.sample_text:
            texts[s.source_id] = _word_set(s.sample_text)

    for group in (*by_domain.values(), *by_content.values()):
        for other in group[1:]:
            union(group[0], other)

    ordered = list(texts)
    for i in range(len(ordered)):
        for j in range(i + 1, len(ordered)):
            if _jaccard(texts[ordered[i]], texts[ordered[j]]) >= TEXT_OVERLAP_THRESHOLD:
                union(ordered[i], ordered[j])

    # parent links (only when the parent is a node of this graph)
    for s in sources:
        if s.parent_source_id is not None and s.parent_source_id in node_ids:
            union(s.source_id, s.parent_source_id)

    # corrections and edges: a valid split on the pair (either direction)
    # cancels the DIRECT merge basis between them — the conservative
    # direction (§11.3: never over-grade independence)
    def _pair(a: Any, b: Any) -> frozenset[Any]:
        return frozenset((a, b))

    splits: set[frozenset[Any]] = {
        _pair(c.from_id, c.to_id)
        for c in corrections
        if c.valid and c.kind == "split"
    }
    for c in corrections:
        if c.valid and c.kind == "merge" and _pair(c.from_id, c.to_id) not in splits:
            union(c.from_id, c.to_id)
    for e in edges:
        if e.from_id in node_ids and e.to_id in node_ids and _pair(e.from_id, e.to_id) not in splits:
            union(e.from_id, e.to_id)

    # unknown lineage: one shared conservative group
    for sid in unknown[1:]:
        union(unknown[0], sid)

    # stable group names in first-seen order (input order = sorted by id)
    group_name: dict[Any, str] = {}
    groups: dict[Any, str] = {}
    for s in sources:
        root = find(s.source_id)
        if root not in group_name:
            group_name[root] = f"g{len(group_name)}"
        groups[s.source_id] = group_name[root]

    member_ids: dict[Any, set[Any]] = {}
    for s in sources:
        member_ids.setdefault(groups[s.source_id], set()).add(s.source_id)

    # the basis that ties each source into its group (recording order)
    bases: dict[Any, str] = {}
    for s in sources:
        sid = s.source_id
        if sid in unknown:
            bases[sid] = "unknown_lineage"
            continue
        same = member_ids[groups[sid]]
        basis: str | None = None
        if (
            s.parent_source_id is not None
            and s.parent_source_id in same
            and s.parent_source_id != sid
        ):
            basis = f"parent:{_short(s.parent_source_id)}"
        elif any(
            c.valid
            and c.kind == "merge"
            and {c.from_id, c.to_id} <= same | {sid}
            and sid in (c.from_id, c.to_id)
            and _pair(c.from_id, c.to_id) not in splits
            for c in corrections
        ):
            basis = "correction:merge"
        elif any(
            e.from_id in same and e.to_id in same and sid in (e.from_id, e.to_id)
            and _pair(e.from_id, e.to_id) not in splits
            for e in edges
        ):
            e0 = next(e for e in edges if {e.from_id, e.to_id} <= same | {sid} and sid in (e.from_id, e.to_id))
            basis = f"edge:{e0.kind}:{_short(e0.edge_id)}"
        else:
            domain = registrable_domain(s.canonical_uri) if s.canonical_uri else None
            if domain is not None and any(
                (registrable_domain(o.canonical_uri) if o.canonical_uri else None) == domain
                for o in (by_id[m] for m in same if m != sid)
            ):
                basis = f"domain:{domain}"
            elif s.content_hash is not None and any(
                by_id[m].content_hash == s.content_hash for m in same if m != sid
            ):
                basis = f"content:{_short(s.content_hash)}"
            elif s.sample_text and any(
                o.sample_text
                and _jaccard(_word_set(s.sample_text), _word_set(o.sample_text))
                >= TEXT_OVERLAP_THRESHOLD
                for o in (by_id[m] for m in same if m != sid)
            ):
                basis = "text_overlap"
        if basis is None:
            basis = "single"
        bases[sid] = basis

    return IndependenceResult(
        algorithm_version=SOURCE_GRAPH_ALGORITHM_VERSION,
        thresholds={"text_overlap": TEXT_OVERLAP_THRESHOLD},
        psl_fingerprint=psl_fingerprint(),
        uri_normalizer_version=URI_NORMALIZER_VERSION,
        groups=groups,
        bases=bases,
    )
