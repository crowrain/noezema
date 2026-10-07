"""Указатель производности источника: общая реализация для fetch и переатрибуции (T7.75/T7.77).

Те же две функции пишут provenance-поля `sources` в двух местах: когда страница прочитана
(`apps/research_proxy/service.py`) и когда решение дописывается задним числом уже прочитанным
строкам (`apps/research_proxy/reattribution.py`). Одна реализация — разная мотивация вызова.

Запись делает хост, не модель: `parent_source_id` и `metadata.derivative_of` — происхождение
прочитанного, а не оценка знания; оценки меняет только rules engine (для переатрибуции —
существующим каскадом `packages/memory/source_graph.py::apply_source_graph_change` и рабочим
переоценки). Родителем может быть только **оригинальная** публикация: уже помеченный пересказ
(`parent_source_id NOT NULL`) родителем не становится — иначе цепочка «новость → ЦБ, который
цитирует Росстат» транзитивно склеила бы два разных первоисточника.

Якорь (`content_hash IS NULL`, `retrieved_at IS NULL`, `declared_primary_anchor`) — честная
запись объявленного первоисточника: узел его не читал, доказательством он не является и в
члены снимка независимости не входит (загрузчик графа добавляет прямых родителей только как
узлы — `packages/memory/source_graph.py:87–105`).
"""

from __future__ import annotations

import uuid
from typing import Final

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.research_proxy.source_attribution import (
    ATTRIBUTION_METHOD_VERSION,
    AttributionDecision,
    PrimarySource,
    is_home_host,
    primary_source,
)
from packages.domain.canonical import canonical_json_bytes

#: сколько последних прочитанных страниц просматривается при поиске уже прочитанной страницы
#: первоисточника (детерминированный потолок, T7.75)
PRIMARY_LOOKUP_SCAN_LIMIT: Final = 500


async def resolve_primary_source(db: AsyncSession, spec: PrimarySource) -> str:
    """Строка первоисточника для указателя: прочитанная страница того же хоста либо якорь."""
    rows = (
        (
            await db.execute(
                text(
                    "SELECT id, canonical_uri FROM sources "
                    "WHERE source_type = 'external_url' AND canonical_uri IS NOT NULL "
                    "  AND content_hash IS NOT NULL AND parent_source_id IS NULL "
                    "ORDER BY retrieved_at DESC NULLS LAST, id LIMIT :limit"
                ),
                {"limit": PRIMARY_LOOKUP_SCAN_LIMIT},
            )
        )
        .mappings()
        .all()
    )
    for row in rows:
        if is_home_host(row["canonical_uri"], spec.home_hosts):
            return str(row["id"])

    anchor = (
        (
            await db.execute(
                text(
                    "SELECT id FROM sources WHERE source_type = 'external_url' "
                    "AND canonical_uri = :uri AND content_hash IS NULL ORDER BY id LIMIT 1"
                ),
                {"uri": spec.home_uri},
            )
        )
        .mappings()
        .first()
    )
    if anchor is not None:
        return str(anchor["id"])

    anchor_id = str(uuid.uuid4())
    await db.execute(
        text(
            """
            INSERT INTO sources (id, source_type, canonical_uri, retrieved_at,
                                 content_hash, metadata)
            VALUES (:id, 'external_url', :uri, NULL, NULL, CAST(:meta AS jsonb))
            """
        ),
        {
            "id": anchor_id,
            "uri": spec.home_uri,
            "meta": canonical_json_bytes(
                {
                    "declared_primary_anchor": True,
                    "primary_key": spec.key,
                    "primary_name": spec.name,
                    "declared_by": ATTRIBUTION_METHOD_VERSION,
                    "note": "первоисточник объявлен хостом как общий родитель пересказов; "
                    "узлом не прочитан",
                }
            ).decode("utf-8"),
        },
    )
    return anchor_id


async def write_derivative_pointer(
    db: AsyncSession,
    source_id: str,
    decision: AttributionDecision,
    *,
    written_by: str | None = None,
) -> str | None:
    """Указатель происхождения строки источника.

    Пишутся только уже существующие поля модели: `sources.parent_source_id` и ключ
    `derivative_of` в `sources.metadata`. При переатрибуции добавляется ключ `written_by`:
    из журнала видно, что решение принял не fetch, а отложенный прогон детектора v2.
    """
    spec = primary_source(decision.primary_key or "")
    if spec is None:
        return None
    parent_id = await resolve_primary_source(db, spec)
    derivative_of: dict[str, str] = {
        "key": spec.key,
        "name": spec.name,
        "uri": spec.home_uri,
        "method": ATTRIBUTION_METHOD_VERSION,
        "basis_fragment": decision.basis_fragment,
    }
    if written_by is not None:
        derivative_of["written_by"] = written_by
    await db.execute(
        text(
            "UPDATE sources SET parent_source_id = :p, "
            "metadata = COALESCE(metadata, '{}'::jsonb) || CAST(:meta AS jsonb) "
            "WHERE id = :id"
        ),
        {
            "p": parent_id,
            "id": source_id,
            "meta": canonical_json_bytes({"derivative_of": derivative_of}).decode("utf-8"),
        },
    )
    return parent_id
