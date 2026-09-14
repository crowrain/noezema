"""Conservative source independence groups for the local corpus (T3.5).

Sources are grouped so that two sources in the SAME group are NOT
independent evidence for each other. The grouping is deliberately
conservative:

- PSL-style registrable domain (a small built-in multi-part suffix list;
  unknown suffix shape falls back to the last two labels);
- URI normalization (scheme/host case, default ports, fragments,
  trailing slash, ``www.``);
- text overlap (word-set Jaccard above the threshold merges groups);
- a source with unknown lineage (no URI and no content hash) lands in a
  single shared ``unknown`` group — never a false independence.

The snapshot (algorithm version, thresholds, fingerprints) is stored with
the assessment so a future algorithm change cannot silently re-grade old
knowledge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from packages.domain.canonical import canonical_sha256
from packages.memory.evidence import (
    INDEPENDENCE_ALGORITHM_VERSION,
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
