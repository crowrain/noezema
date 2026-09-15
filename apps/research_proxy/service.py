"""The research proxy service (T6.1, stage 5, §5.12).

The ONLY egress to the network. One call:

1. read the EFFECTIVE config (fail-closed); the ``research_proxy``
   section selects the mode and the limits;
2. sealed mode (bootstrap default) refuses every fetch — no egress,
   no connection, audited;
3. the SSRF guard validates the URL and every resolved address;
4. the read-only fetch under size/redirect/time limits;
5. the result is stored original + normalized + hashes (content-
   addressed artifacts) and the provenance journal rows are written
   (``sources`` + ``artifact_chunks``) with the audit event in the
   SAME transaction;
6. the working copy of the fetched bytes is discarded — the proxy
   keeps no active content.

Every fetched page is untrusted external content: the response
envelope says so, the artifacts are ``trust_class=untrusted_external``,
and the normalized text must never enter a model context without the
untrusted-data fence (T6.3 wires that into the explorer context).
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
)

from apps.research_proxy.fetch import FetchClient, FetchError
from apps.research_proxy.modes import ModeError, ModePolicy, ResearchMode
from apps.research_proxy.normalization import (
    PARSER_FINGERPRINT,
    normalize_content,
)
from apps.research_proxy.search import (
    SearchResults,
    UpstreamHit,
    count_upstream_requests,
    parse_searxng,
    search_local,
    upstream_request_url,
)
from apps.research_proxy.ssrf_guard import SSRFError, SSRFPolicy, validate_url
from packages.artifacts.store import ArtifactStore
from packages.domain.canonical import canonical_json_bytes
from packages.domain.db.uow import transaction
from packages.domain.models.enums import AuditEventType
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigError, ConfigService

#: the trust class for everything the proxy fetches — the DB check
#: constraint (0003) is the closed set local_trusted|session_workspace|
#: untrusted|external; external content is the "external" class (and
#: the context marking says it is untrusted, T6.3)
UNTRUSTED_EXTERNAL = "external"


class ResearchProxyError(RuntimeError):
    """The fetch was refused or failed (reason in ``.reason``)."""

    def __init__(self, reason: str, status: str) -> None:
        super().__init__(reason)
        self.reason = reason
        self.status = status


class ResearchProxyService:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        artifact_store: ArtifactStore,
    ) -> None:
        self.session_factory = session_factory
        self.store = artifact_store

    async def fetch(self, url: str) -> dict[str, Any]:
        """Fetch ``url`` through the controlled egress. Returns the
        provenance envelope (hashes, ids, trust marking) — never the
        raw content: the caller reads it back from the artifact store
        through the same content-addressed paths as any other artifact.

        Raises ResearchProxyError (audited) for a refused/fetch-failed
        request and ConfigError for an unusable effective config.
        """
        async with self.session_factory() as db:
            snapshot = await ConfigService.get_effective(db)
            section = snapshot.research_proxy
            mode_policy = self._mode_policy(section)
            if not mode_policy.egress:
                async with transaction(db):
                    await self._reject(db, url, "sealed_mode_no_egress")
                raise ResearchProxyError("sealed mode: no network egress", "rejected")
            try:
                policy = SSRFPolicy.from_section(section)
                if mode_policy.mode is ResearchMode.OPEN_LAB:
                    host, _port = validate_url(url)
                    if not mode_policy.domain_allowed(host):
                        raise SSRFError(
                            f"open_lab: domain {host!r} is not in the allowed list"
                        )
            except SSRFError as exc:
                async with transaction(db):
                    await self._reject(db, url, str(exc))
                raise ResearchProxyError(str(exc), "rejected") from exc
        mode = mode_policy.mode.value

        client = FetchClient(policy)
        try:
            result = await client.fetch(url)
        except (SSRFError, FetchError) as exc:
            async with self.session_factory() as db, transaction(db):
                await self._reject(db, url, str(exc))
            status = "rejected" if isinstance(exc, SSRFError) else "failed"
            raise ResearchProxyError(str(exc), status) from exc
        finally:
            await client.aclose()

        # active content hygiene: persist original + normalized, then
        # the only working copy goes out of scope
        original_sha = self.store.put(
            result.data,
            origin="research_proxy",
            trust_class=UNTRUSTED_EXTERNAL,
            mime=result.content_type,
        )
        normalized = normalize_content(result.content_type, result.data)
        normalized_sha: str | None = None
        if normalized.text is not None:
            normalized_sha = self.store.put(
                normalized.text.encode("utf-8"),
                origin="research_proxy",
                trust_class=UNTRUSTED_EXTERNAL,
                mime="text/plain",
            )
        original_size = len(result.data)
        # active-content hygiene: the working copy is cleared once the
        # content-addressed store has it — the proxy keeps no live
        # copy of fetched content
        result.data = b""

        async with self.session_factory() as db:
            source_id = uuid.uuid4()
            async with transaction(db):
                # the content-addressed registry rows (fs store + DB row
                # in the same transaction, deduped by sha)
                for sha, size, mime in (
                    (original_sha, original_size, result.content_type),
                    (
                        normalized_sha,
                        len((normalized.text or "").encode("utf-8")),
                        "text/plain",
                    ),
                ):
                    if sha is None:
                        continue
                    existing = (
                        (
                            await db.execute(
                                text("SELECT id FROM artifacts WHERE sha256 = :s"),
                                {"s": sha},
                            )
                        )
                        .mappings()
                        .first()
                    )
                    if existing is None:
                        await db.execute(
                            text(
                                """
                                INSERT INTO artifacts (id, sha256, size, mime,
                                                       origin, trust_class)
                                VALUES (:id, :s, :size, :mime, 'research_proxy',
                                       :trust)
                                """
                            ),
                            {
                                "id": str(uuid.uuid4()),
                                "s": sha,
                                "size": size,
                                "mime": mime,
                                "trust": UNTRUSTED_EXTERNAL,
                            },
                        )
                await db.execute(
                    text(
                        """
                        INSERT INTO sources (id, source_type, canonical_uri,
                                             retrieved_at, content_hash, metadata)
                        VALUES (:id, 'external_url', :uri, now(), :hash,
                               CAST(:meta AS jsonb))
                        """
                    ),
                    {
                        "id": str(source_id),
                        "uri": result.final_url,
                        "hash": original_sha,
                        "meta": canonical_json_bytes(
                            {
                                "requested_url": result.url,
                                "content_type": result.content_type,
                                "redirects": result.redirects,
                                "elapsed_ms": result.elapsed_ms,
                                "user_agent": policy.user_agent,
                                "normalized_sha256": normalized_sha,
                                "mode": mode,
                            }
                        ).decode("utf-8"),
                    },
                )
                await db.execute(
                    text(
                        """
                        INSERT INTO artifact_chunks (
                            id, artifact_id, chunk_id, byte_range, origin_kind,
                            source_uri, obtained_at, content_hash, transform_chain,
                            parser_fingerprint, trust_class, usage_constraints
                        ) VALUES (
                            :id, (SELECT id FROM artifacts WHERE sha256 = :sha),
                            'chunk-0', NULL, 'research_proxy', :uri, now(),
                            :sha, CAST(:chain AS jsonb), :fingerprint, :trust,
                            '{}'::jsonb
                        )
                        """
                    ),
                    {
                        "id": str(uuid.uuid4()),
                        "sha": original_sha,
                        "uri": result.final_url,
                        "chain": canonical_json_bytes(
                            list(normalized.transform_chain)
                        ).decode("utf-8"),
                        "fingerprint": PARSER_FINGERPRINT,
                        "trust": UNTRUSTED_EXTERNAL,
                    },
                )
                await AuditService(db).record(
                    AuditEventType.RESEARCH_FETCH_COMPLETED,
                    payload={
                        "url": result.url,
                        "final_url": result.final_url,
                        "sha256": original_sha,
                        "normalized_sha256": normalized_sha,
                        "source_id": str(source_id),
                        "mode": mode,
                        "redirects": result.redirects,
                        "elapsed_ms": result.elapsed_ms,
                    },
                    actor="research_proxy",
                    public_summary=f"fetched {result.final_url} (untrusted external)",
                )

        return {
            "source_id": str(source_id),
            "requested_url": result.url,
            "final_url": result.final_url,
            "original_sha256": original_sha,
            "original_size": original_size,
            "normalized_sha256": normalized_sha,
            "normalized_text_sha256": normalized.text_sha256,
            "transform_chain": list(normalized.transform_chain),
            "content_type": result.content_type,
            "redirects": result.redirects,
            "trust_class": UNTRUSTED_EXTERNAL,
            "note": "недоверенный внешний контент: использовать только за fence-ом",
        }

    def _mode_policy(self, section: Any) -> ModePolicy:
        try:
            return ModePolicy.from_section(section)
        except (ModeError, SSRFError) as exc:
            raise ConfigError(f"invalid research_proxy section: {exc}") from exc

    async def search(self, query: str) -> dict[str, Any]:
        """Search in the effective mode (§5.12.1).

        - every mode: the local index (committed knowledge, no egress);
        - curated: SearXNG through the guarded fetch client — the
          upstream log (audit ``research_upstream_request``) and the
          rate limit apply;
        - sealed/open_lab: no upstream (open_lab's egress is the
          domain-allowlisted ``fetch``, not search).
        """
        async with self.session_factory() as db:
            snapshot = await ConfigService.get_effective(db)
            mode_policy = self._mode_policy(snapshot.research_proxy)

        local = None
        async with self.session_factory() as db:
            local = await search_local(db, query)

        upstream: tuple[UpstreamHit, ...] = ()
        upstream_logged = False
        if mode_policy.mode is ResearchMode.CURATED and mode_policy.searxng_url:
            upstream = await self._search_upstream(mode_policy, query)
            upstream_logged = True

        results = SearchResults(
            mode=mode_policy.mode.value,
            profile=mode_policy.profile_name,
            local=local or (),
            upstream=upstream,
            upstream_logged=upstream_logged and bool(upstream),
        )
        return {
            "mode": results.mode,
            "profile": results.profile,
            "local": [
                {
                    "claim_id": h.claim_id,
                    "statement": h.statement,
                    "claim_type": h.claim_type,
                    "relevance": h.relevance,
                }
                for h in results.local
            ],
            "upstream": [
                {"url": h.url, "title": h.title, "content": h.content}
                for h in results.upstream
            ]
            if upstream_logged
            else None,
            "note": "недоверенный внешний контент: использовать только за fence-ом",
        }

    async def _search_upstream(
        self, mode_policy: ModePolicy, query: str
    ) -> tuple[UpstreamHit, ...]:
        assert mode_policy.searxng_url is not None
        async with self.session_factory() as db:
            snapshot = await ConfigService.get_effective(db)
            section_policy = SSRFPolicy.from_section(snapshot.research_proxy)
            # rate limit against the upstream log (fail-closed: the
            # count query and the eventual log entry share the table)
            recent = await count_upstream_requests(
                db, window_seconds=mode_policy.rate_limit_window_seconds
            )
            if recent >= mode_policy.rate_limit_max:
                async with transaction(db):
                    await AuditService(db).record(
                        AuditEventType.RESEARCH_FETCH_REJECTED,
                        payload={
                            "url": upstream_request_url(
                                mode_policy.searxng_url, query
                            ),
                            "reason": "upstream_rate_limit_exceeded",
                            "mode": mode_policy.mode.value,
                        },
                        actor="research_proxy",
                        public_summary="egress refused: upstream rate limit",
                    )
                raise ResearchProxyError(
                    "upstream rate limit exceeded", "rate_limited"
                )

        client = FetchClient(section_policy)
        target = upstream_request_url(mode_policy.searxng_url, query)
        try:
            result = await client.fetch(target)
            payload = json.loads(result.data)
        except (SSRFError, FetchError, ValueError) as exc:
            async with self.session_factory() as db, transaction(db):
                await AuditService(db).record(
                    AuditEventType.RESEARCH_UPSTREAM_REQUEST,
                    payload={
                        "upstream_host": mode_policy.searxng_host,
                        "query": query,
                        "mode": mode_policy.mode.value,
                        "status": "failed",
                        "error": str(exc),
                    },
                    actor="research_proxy",
                    public_summary="upstream request failed (logged)",
                )
            raise ResearchProxyError(f"upstream request failed: {exc}", "failed") from exc
        finally:
            await client.aclose()

        hits = parse_searxng(payload)
        async with self.session_factory() as db, transaction(db):
            await AuditService(db).record(
                AuditEventType.RESEARCH_UPSTREAM_REQUEST,
                payload={
                    "upstream_host": mode_policy.searxng_host,
                    "query": query,
                    "mode": mode_policy.mode.value,
                    "status": "ok",
                    "results": len(hits),
                },
                actor="research_proxy",
                public_summary=f"upstream search via {mode_policy.searxng_host} (logged)",
            )
        return hits

    async def _reject(self, db: AsyncSession, url: str, reason: str) -> None:
        await AuditService(db).record(
            AuditEventType.RESEARCH_FETCH_REJECTED,
            payload={"url": url, "reason": reason},
            actor="research_proxy",
            public_summary=f"egress refused: {reason}",
        )
