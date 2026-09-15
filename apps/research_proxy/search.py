"""Research backends per mode (T6.2, stage 5, §5.12.1).

- the local index: committed knowledge (claims) searched with the same
  Russian FTS as the context builder — available in EVERY mode, no
  egress;
- the curated upstream: SearXNG queried through the SSRF-guarded fetch
  client; every upstream request is journaled (upstream log) and the
  request rate is limited.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MIN_RELEVANCE = 1e-9
_LOCAL_LIMIT = 10


@dataclass(frozen=True)
class LocalHit:
    claim_id: str
    statement: str
    claim_type: str
    relevance: float


@dataclass(frozen=True)
class UpstreamHit:
    url: str
    title: str
    content: str


@dataclass(frozen=True)
class SearchResults:
    mode: str
    profile: str
    local: tuple[LocalHit, ...]
    upstream: tuple[UpstreamHit, ...]
    upstream_logged: bool


async def search_local(db: AsyncSession, query: str) -> tuple[LocalHit, ...]:
    """FTS over committed claim statements (no egress, every mode).

    The same matching as the context builder: Russian config,
    ``plainto_tsquery``, ``ts_rank`` — a claim matches when it shares
    words with the query; ranked by relevance.
    """
    rows = (
        (
            await db.execute(
                text(
                    """
                    SELECT c.id, c.statement, c.claim_type,
                           ts_rank(to_tsvector('russian', c.statement),
                                   plainto_tsquery('russian', :q)) AS relevance
                    FROM claims c
                    WHERE ts_rank(to_tsvector('russian', c.statement),
                                  plainto_tsquery('russian', :q)) > :min_rel
                    ORDER BY relevance DESC, c.statement
                    LIMIT :limit
                    """
                ),
                {"q": query, "min_rel": MIN_RELEVANCE, "limit": _LOCAL_LIMIT},
            )
        )
        .mappings()
        .all()
    )
    return tuple(
        LocalHit(
            claim_id=str(r["id"]),
            statement=r["statement"],
            claim_type=r["claim_type"],
            relevance=float(r["relevance"]),
        )
        for r in rows
    )


def parse_searxng(payload: Any) -> tuple[UpstreamHit, ...]:
    """SearXNG JSON API: /search?format=json → {"results": [...]}.

    Tolerant: anything unexpected is dropped, never raised — the
    upstream is untrusted external content.
    """
    if not isinstance(payload, dict):
        return ()
    raw_results = payload.get("results")
    if not isinstance(raw_results, list):
        return ()
    hits: list[UpstreamHit] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        url = item.get("url")
        if not isinstance(url, str) or not url.startswith(("http://", "https://")):
            continue
        title = item.get("title")
        content = item.get("content")
        hits.append(
            UpstreamHit(
                url=url,
                title=title if isinstance(title, str) else "",
                content=content if isinstance(content, str) else "",
            )
        )
    return tuple(hits)


def upstream_request_url(searxng_url: str, query: str) -> str:
    """The SearXNG JSON API endpoint for one query."""
    base = searxng_url.rstrip("/")
    from urllib.parse import quote

    return f"{base}/search?format=json&q={quote(query)}"


async def count_upstream_requests(
    db: AsyncSession, *, window_seconds: int
) -> int:
    """The upstream log doubled as the rate-limit counter: how many
    upstream requests were journaled inside the window."""
    value = await db.execute(
        text(
            """
            SELECT count(*) FROM audit_events
            WHERE type = 'research_upstream_request'
              AND occurred_at > now() - make_interval(secs => :w)
            """
        ),
        {"w": window_seconds},
    )
    return int(value.scalar_one())
