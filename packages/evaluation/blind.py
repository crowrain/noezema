"""T7.7 (EVAL-3): the blind sample for MANUAL review (§22.2).

§22.2 defines the blind sample as a manual procedure: a person
independently judges whether each sampled claim follows from its
evidence, and the 95% confidence interval is published. The automatic
run cannot perform that judgment — the ``blind_provenance_path`` /
``blind_scope`` gates measure only the STRUCTURAL projection (linked
evidence exists, scope is declared). Therefore the run report marks
those two gates as a structural check, not as passed §22.2 gates;
full acceptance requires the manual review of the sample rendered
here (``noezemactl blind-sample``; docs/eval/EVAL-3-freeze.md
«Ограничение метода»).

Selection: exactly the one the blind gates measure — seeded shuffle
within each (claim_type, epistemic_status) stratum of the current
head, proportional allocation of ``run.blind_sample_size``, remainder
top-up from the global seeded shuffle. Deterministic for the same run
row, so the rendered sample IS the measured sample.

HEAD SELECTION (T7.19, EVAL-3d post-mortem; §14.1, §8.7.2): after a
mid-run online activation a claim has one head per config snapshot
(shadow heads, ``UNIQUE(claim_id, config_snapshot_id)``). The current
knowledge of a claim is resolved through the runtime pointer —
``runtime_config_heads.active_config_snapshot_id`` (pointer equality,
NOT ``config_snapshots.activation_state``; §14.1) — and ONLY that head
is counted. A claim without a head on the active snapshot has no
current lifecycle under the effective config (the query path,
``MemoryService.claim_view``, returns None for it) and is not part of
the sample.
"""

from __future__ import annotations

import json
import random
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.evaluation.service import EvaluationRun

#: The effective config snapshot, resolved through the runtime pointer
#: (§14.1: «current lifecycle разрешается только через
#: runtime_config_heads.active_config_snapshot_id. Pointer equality, а
#: не config_snapshots.activation_state='active', определяет effective
#: config»). After a mid-run activation (§8.7.2) a claim has one head
#: per config snapshot; the gates and the blind sample count EXACTLY
#: ONE head per claim — the head of this snapshot (T7.19, EVAL-3d).
#: A claim without a head here has no current knowledge under the
#: effective config and is not counted.
EFFECTIVE_SNAPSHOT_SQL = (
    "(SELECT active_config_snapshot_id FROM runtime_config_heads "
    "WHERE scope = 'global')"
)


async def blind_sample_claim_ids(db: AsyncSession, run: EvaluationRun) -> list[Any]:
    """The seeded, stratified blind sample (claim ids).

    Stratification: (claim_type, epistemic_status) of the current head
    of the EFFECTIVE snapshot (T7.19: exactly one head per claim).
    Seeded shuffle within each stratum; proportional allocation of
    ``run.blind_sample_size``; the remainder (rounding) is drawn from
    the global seeded shuffle. Deterministic for the same run row.
    """
    rows = (
        await db.execute(
            text(
                "SELECT c.id, c.claim_type, h.epistemic_status "
                "FROM claims c "
                "JOIN claim_assessment_heads h "
                "  ON h.claim_id = c.id AND h.assessment_state = 'current' "
                f"  AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL} "
                "ORDER BY c.created_at, c.id"
            )
        )
    ).all()
    if not rows:
        return []
    rng = random.Random(run.blind_sample_seed)
    strata: dict[tuple[str, str], list[Any]] = {}
    for r in rows:
        strata.setdefault((r[1], r[2]), []).append(r[0])
    for members in strata.values():
        rng.shuffle(members)
    size = min(run.blind_sample_size, len(rows))
    total = len(rows)
    picked: list[Any] = []
    for members in strata.values():
        share = max(1, round(size * len(members) / total))
        picked.extend(members[:share])
    # deterministic dedup + top-up from the global seeded shuffle
    seen: set[Any] = set()
    unique: list[Any] = []
    for cid in picked:
        if cid not in seen:
            seen.add(cid)
            unique.append(cid)
    if len(unique) < size:
        global_pool = [r[0] for r in rows]
        rng2 = random.Random(run.blind_sample_seed)
        rng2.shuffle(global_pool)
        for cid in global_pool:
            if cid not in seen:
                seen.add(cid)
                unique.append(cid)
                if len(unique) >= size:
                    break
    return unique[:size]


def _truncate(s: str, limit: int) -> str:
    if len(s) <= limit:
        return s
    return s[:limit] + f" […] ({len(s) - limit} chars more)"


async def blind_sample_details(
    db: AsyncSession,
    run: EvaluationRun,
    *,
    store: ArtifactStore | None = None,
    fragment_chars: int = 2000,
) -> list[dict[str, Any]]:
    """The blind sample with the detail a human reviewer needs.

    Per claim: id, type, statement, freshness, epistemic status (head),
    effective grade, assessed scope. Per linked evidence: kind,
    relation, scope, source URL / artifact id, and the cited fragment
    — the text the model read (normalized source text via the source
    row, or the observation artifact), resolved from the
    content-addressed ``store`` when available (None otherwise).
    """
    entries: list[dict[str, Any]] = []
    for claim_id in await blind_sample_claim_ids(db, run):
        head = (
            await db.execute(
                text(
                    "SELECT c.id, c.claim_type, c.statement, c.freshness_status, "
                    "c.as_of, h.epistemic_status, a.effective_grade, a.assessed_scope, "
                    "h.current_assessment_id "
                    "FROM claims c "
                    "JOIN claim_assessment_heads h "
                    "  ON h.claim_id = c.id AND h.assessment_state = 'current' "
                    f"  AND h.config_snapshot_id = {EFFECTIVE_SNAPSHOT_SQL} "
                    "JOIN claim_assessments a ON a.id = h.current_assessment_id "
                    "WHERE c.id = :c"
                ),
                {"c": claim_id},
            )
        ).first()
        if head is None:
            continue
        entry: dict[str, Any] = {
            "id": str(head[0]),
            "claim_type": head[1],
            "statement": head[2],
            "freshness_status": head[3],
            "as_of": head[4].isoformat() if head[4] is not None else None,
            "epistemic_status": head[5],
            "effective_grade": head[6],
            "assessed_scope": head[7],
            "evidence": [],
        }
        evs = (
            await db.execute(
                text(
                    "SELECT e.evidence_kind, e.relation, e.scope, e.source_id, "
                    "e.chunk_id, e.observation_artifact_id, s.canonical_uri, "
                    "s.content_hash, s.metadata, art.sha256, art.mime "
                    "FROM evidence e "
                    "JOIN assessment_evidence ae ON ae.evidence_id = e.id "
                    "LEFT JOIN sources s ON s.id = e.source_id "
                    "LEFT JOIN artifacts art ON art.id = e.observation_artifact_id "
                    "WHERE ae.assessment_id = :a "
                    "ORDER BY e.id"
                ),
                {"a": head[8]},
            )
        ).all()
        for ev in evs:
            kind, relation, scope, src_id, chunk_id, art_id = ev[0], ev[1], ev[2], ev[3], ev[4], ev[5]
            uri, content_hash, src_meta, art_sha, art_mime = ev[6], ev[7], ev[8], ev[9], ev[10]
            fragment = await _fragment_text(store, art_sha, src_meta, content_hash, fragment_chars)
            entry["evidence"].append(
                {
                    "kind": kind,
                    "relation": relation,
                    "scope": scope,
                    "source_id": str(src_id) if src_id is not None else None,
                    "source_uri": uri,
                    "source_content_sha256": content_hash,
                    "chunk_id": chunk_id,
                    "artifact_id": str(art_id) if art_id is not None else None,
                    "artifact_mime": art_mime,
                    "fragment": fragment,
                }
            )
        entries.append(entry)
    return entries


async def _fragment_text(
    store: ArtifactStore | None,
    observation_sha: Any,
    source_meta: Any,
    source_content_sha: Any,
    limit: int,
) -> str | None:
    """The text the model read: the observation artifact, else the
    normalized source text (``metadata.normalized_sha256``), else the
    raw source content. None when the store is unavailable or the
    artifact is missing."""
    if store is None:
        return None
    candidates: list[str] = []
    if isinstance(observation_sha, str):
        candidates.append(observation_sha)
    normalized = (
        (source_meta or {}).get("normalized_sha256")
        if isinstance(source_meta, dict)
        else None
    )
    if isinstance(normalized, str):
        candidates.append(normalized)
    if isinstance(source_content_sha, str):
        candidates.append(source_content_sha)
    for sha in candidates:
        try:
            data = store.get(sha)
        except ArtifactStoreError:
            continue
        return _truncate(data.decode("utf-8", errors="replace"), limit)
    return None


def _jsonish(value: Any) -> str:
    if value is None:
        return "—"
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def render_blind_sample(run: EvaluationRun, entries: list[dict[str, Any]]) -> str:
    """The human-readable report for the manual §22.2 review."""
    lines: list[str] = []
    lines.append(f"# Слепая выборка — run {run.label} ({run.id})")
    lines.append("")
    lines.append(
        f"seed={run.blind_sample_seed} size={run.blind_sample_size} "
        f"sampled={len(entries)}"
    )
    lines.append("")
    lines.append(
        "> **§22.2: слепая выборка — РУЧНАЯ проверка.** Этот файл — структурная\n"
        "> проекция выборки: тот же seeded/стратифицированный отбор, что у гейтов\n"
        "> `blind_provenance_path` / `blind_scope`. Автоматический исход этих двух\n"
        "> гейтов — **структурная проверка, а не пройденные гейты §22.2**; приёмка\n"
        "> требует, чтобы человек независимо проверил каждый claim ниже: следует ли\n"
        "> statement из процитированных фрагментов. См. docs/eval/EVAL-3-freeze.md,\n"
        "> «Ограничение метода»."
    )
    lines.append("")
    for e in entries:
        lines.append(f"## claim `{e['id']}`")
        lines.append("")
        lines.append(f"- type: `{e['claim_type']}`")
        lines.append(f"- statement: {e['statement']}")
        lines.append(f"- epistemic_status: `{e['epistemic_status']}` (head: current)")
        lines.append(f"- grade: `{e['effective_grade']}`")
        lines.append(f"- assessed_scope: `{_jsonish(e['assessed_scope'])}`")
        if e["freshness_status"] or e["as_of"]:
            lines.append(
                f"- freshness: `{e['freshness_status']}`"
                + (f" (as_of {e['as_of']})" if e["as_of"] else "")
            )
        if not e["evidence"]:
            lines.append("- linked evidence: **НЕТ** (структурный сбой provenance)")
        for i, ev in enumerate(e["evidence"], 1):
            lines.append("")
            lines.append(
                f"### evidence {i}: `{ev['kind']}` / `{ev['relation']}`"
            )
            lines.append(f"- scope: `{_jsonish(ev['scope'])}`")
            if ev["source_uri"]:
                src = f"- source: {ev['source_uri']}"
                if ev["source_content_sha256"]:
                    src += f" (content sha256 `{ev['source_content_sha256'][:16]}…`)"
                if ev["chunk_id"]:
                    src += f" chunk `{ev['chunk_id']}`"
                lines.append(src)
            elif ev["artifact_id"]:
                lines.append(f"- observation artifact: `{ev['artifact_id']}`")
            else:
                lines.append("- source: **не разрешается** (нет ни source, ни artifact)")
            frag = ev["fragment"]
            if frag is None:
                lines.append("- fragment: *(в artifact-хранилище недоступен)*")
            else:
                lines.append("- fragment:")
                for line in frag.splitlines() or [""]:
                    lines.append(f"  > {line}")
        lines.append("")
    return "\n".join(lines)
