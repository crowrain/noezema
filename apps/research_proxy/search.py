"""Research backends per mode (T6.2, stage 5, §5.12.1).

- the local index: committed knowledge (claims) searched with the same
  Russian FTS as the context builder — available in EVERY mode, no
  egress;
- the curated upstream: SearXNG queried through the SSRF-guarded fetch
  client; every upstream request is journaled (upstream log) and the
  request rate is limited.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
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


@dataclass(frozen=True)
class UpstreamWindow:
    """Снимок upstream-журнала за окно лимита (T7.127): сколько запросов в окне, когда самый
    старый из них и какие часы у базы — сравнение считается по одним и тем же часам."""

    requests: int
    oldest: datetime | None
    now: datetime
    window_seconds: int


async def upstream_window_state(db: AsyncSession, *, window_seconds: int) -> UpstreamWindow:
    """Одно read-only чтение upstream-журнала: счётчик окна и время самого старого запроса.

    Счётчик и время берутся ОДНИМ запросом: два отдельных чтения могли бы описать два разных
    окна (между запросами скользящее окно сдвигается), и отказ объяснял бы модели не то
    окно, по которому он посчитан.
    """
    row = (
        (
            await db.execute(
                text(
                    """
                    SELECT count(*) AS requests,
                           min(occurred_at) AS oldest,
                           now() AS db_now
                    FROM audit_events
                    WHERE type = 'research_upstream_request'
                      AND occurred_at > now() - make_interval(secs => :w)
                    """
                ),
                {"w": window_seconds},
            )
        )
        .mappings()
        .one()
    )
    oldest_raw = row["oldest"]
    now_raw = row["db_now"]
    return UpstreamWindow(
        requests=int(row["requests"]),
        oldest=oldest_raw if isinstance(oldest_raw, datetime) else None,
        now=now_raw if isinstance(now_raw, datetime) else datetime.now(UTC),
        window_seconds=window_seconds,
    )


async def count_upstream_requests(
    db: AsyncSession, *, window_seconds: int
) -> int:
    """The upstream log doubled as the rate-limit counter: how many
    upstream requests were journaled inside the window."""
    return (await upstream_window_state(db, window_seconds=window_seconds)).requests


def _as_utc(value: datetime) -> datetime:
    """Naive-время журнала трактуется как UTC (та же трактовка, что в `parse_since`)."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def minutes_until_release(window: UpstreamWindow) -> int | None:
    """Сколько минут ждать первого освободившегося слота окна (чистая функция, T7.127).

    Окно скользящее: слот освобождается, когда самый старый запрос окна выпадает из него, то
    есть в `oldest + window_seconds`. Запросов в окне нет — предсказывать нечего (None); срок
    самого старого запроса уже истёк — 0 (окно уже раскрылось, считать минуты не за чем).
    Дробный остаток округляется ВВЕРХ: обещать исследователю меньше, чем осталось, нельзя.
    """
    if window.oldest is None:
        return None
    release_at = _as_utc(window.oldest) + timedelta(seconds=window.window_seconds)
    remaining = (release_at - _as_utc(window.now)).total_seconds()
    if remaining <= 0:
        return 0
    return math.ceil(remaining / 60)


def _plural(count: int, one: str, few: str, many: str) -> str:
    """Русская плюрализация счётчика: 1 запрос, 3 запроса, 5 запросов (T7.127)."""
    tail = abs(count) % 100
    if 11 <= tail <= 14:
        return many
    last = tail % 10
    if last == 1:
        return one
    if 2 <= last <= 4:
        return few
    return many


def rate_limit_notice(window: UpstreamWindow, limit: int) -> str:
    """Понятное объяснение отказа по лимиту — тот текст, который видит исследователь (T7.127).

    Машинный код `rate_limited` остаётся в тексте: по нему отказ отличают от прочих отказов
    поиска. Журнал отказов (`research_fetch_rejected`, reason `upstream_rate_limit_exceeded`)
    этим не меняется — текст наблюдения и есть то, что модель получает вместо результатов.
    """
    minutes = minutes_until_release(window)
    window_minutes = max(1, round(window.window_seconds / 60))
    if minutes is None:
        release = "предсказать освобождение не по чему: в окне нет ни одного записанного запроса"
    else:
        release = f"ближайшее освобождение — примерно через {minutes} мин"
    requests_word = _plural(limit, "запрос", "запроса", "запросов")
    return (
        f"внешний поиск отклонён: лимит поиска узла исчерпан — {window.requests} из {limit} {requests_word} "
        f"за окно {window.window_seconds} с ({window_minutes} мин); {release}; "
        "до этого времени дальнейшие поиски тоже будут отклонены — используй уже найденные адреса "
        "(research.fetch) или заканчивай с тем, что найдено. Код отказа: rate_limited."
    )
