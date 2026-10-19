"""T7.124 (часть 2): аудит «значение на связи» для УЖЕ записанных связей — только чтение.

Гейт значения (T7.108, ADR-0034 §10; профили T7.113 и T7.123) ведёт КАЖДУЮ новую
`supports`-связь с уликой `source_assertion` и снимает её при записи. Ранее записанные связи
гейты не затрагивают никогда (ADR-0038 §12): стенд 2026-08-29 (сессия 64837cb5, утверждение
83e7a1c8) живёт с двумя связями к странице про ключевую ставку, где значения «9,6» нет, и
оценка E3 «Проверено» держится на двух группах независимости, одна из которых — эти связи.
Этот модуль отвечает честно на вопрос «что было бы, если бы гейт стоял и на записанных
связях»: перечисляет связи, называет исход той же проверки и пересчитывает группы/оценку
теми же чистыми функциями, что у рабочего переоценки и у `independence-preview`
(`load_source_graph_assembly` + `group_source_graph` + `evaluate`) — БЕЗ ЕДИНОЙ ЗАПИСИ.

Почему здесь нет `--apply` (остановка по стоп-критерию задачи, разбор — ADR-0038 §13).
В существующей схеме нет признака «улика не участвует в оценке»:

* `evidence` (миграция 0004:74–99) не имеет колонки состояния: `relation` закрыт CHECK'ом
  `('supports','counters')`, `evidence_kind` — закрытым перечислением, `scope` носит keys
  покрытия/атрибуции; таблица `evidence` ни одной миграцией не изменялась;
* движок правил исключает улику только по известным ему признакам: relation, вид улики,
  покрытие области, as_of и `resolved` — последнее есть только у опровержений и живёт в
  отдельной таблице `counterevidence_resolutions` (0012:52–77, `packages/memory/resolutions.py`);
* вход группировки — ВСЕ источные улики утверждения без предиката
  (`source_graph.load_source_graph_assembly`: `SELECT DISTINCT source_id FROM evidence WHERE
  claim_id = :c`), участники группы — тоже они;
* `assessment_evidence.role` пишется механически (`counter`/`support`, `reassessment.py:567`)
  и движком не читается: «не участвует в оценке» через него выразить нельзя;
* `relation='counters'` был бы ложью (страница без значения не опровергает) и по правилу
  ADR-0021 cap'нул бы оценку до E1/disputed;
* запись значения (`scope.value_attribution`, снятие — T7.120a `reattribution.py:1308`)
  решает только склейку родителей: участником или невключателем улики она её не делает;
* коррекция оператора (ADR-0031) утверждает происхождение («это пересказ того же
  первоисточника») — на странице без значения такое утверждение лжено;
* снять покрытие области, чтобы улика перестала подтверждать (`scope_not_covered`), — ровно
  тот «нечестный downgrade через другую дверь», от которого защищён `packages/memory/reverify.py:40-44`.

Следствие: честное снятие уже записанной связи требует новой долговременной сущности
(таблица отзывов связей по образцу `counterevidence_resolutions`) и предиката исключения на
входе оценки — миграция БД плюс правка сборки оценки, то есть отдельная задача с решением
пользователя. Заглушку «применить», которая всегда отказывает, AGENTS §4 запрещает. Поэтому
команда отдаёт план и ничего не меняет; варианты — в ADR-0038 §13 и в отчёте.

Что модуль читает и чем пользуется: `runtime_config_heads` → `config_snapshots`
(fail-closed, §3), действующие головы оценок effective-снапшота, строки `evidence` и
`sources`, сохранённый нормализованный текст страницы (`sources.metadata->>'normalized_sha256'`
→ артектное хранилище; запасной путь — payload существующего `research_fetch_completed`),
записанные снимки независимости (источников и окружения) головы, коррекции графа источников.

Текст связи. Долговременно хранится ТОЛЬКО нормализованный текст страницы (артефакт по
`normalized_sha256`); фрагмент улики — окно, выбранное в момент чтения (`evidence.py`), оно
подмножество этого же текста. Поэтому проверка по всей странице не строже гейта записи
(«фрагмент ИЛИ страница»), и связь, которую живой гейт оставил бы, план снятой не называет.
Если текста нет (артефакта нет или он не читается) — связь НЕ включается в пересчёт и
называется отдельно: «текста нет» — это не «значения нет».

Юрисдикция та же, что у гейта: `supports` + `source_assertion`. Опровержения (`counters`)
и улики других видов план не трогает. Утверждения, чьи источники покрыты действующей
коррекцией оператора, пропускаются (как в `research-reattribute`: решение оператора без
журнала отменять нельзя). Идемпотентность: повторный прогон даёт тот же план — менять нечего.

Модуль не пишет ни одной строки и не вызывает ни одного сервиса записи: ни audit, ни staging,
ни каскада `apply_source_graph_change`. Оценку он не назначает — пересчитывает чистым
`evaluate` (единственный производитель оценок, §3) и называет результат прогнозом.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from apps.orchestrator.curator_rejections import (
    VALUE_GATE_ABSENT,
    link_value_gate,
    statement_value_profile,
)
from apps.research_proxy.reattribution import OPERATOR_ACTOR_PREFIX
from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.memory import (
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMCounterevidenceResolution,
    ORMEnvironmentIndependenceMember,
    ORMEvidence,
    ORMSource,
    ORMSourceGraphCorrection,
    ORMSourceIndependenceMember,
)
from packages.domain.services.config import ConfigError, ConfigService
from packages.memory.env_independence import UNTRACKED_GROUP
from packages.memory.independence import group_source_graph
from packages.memory.rules_engine import EvaluatedEvidence, evaluate
from packages.memory.service import MemoryService
from packages.memory.source_graph import (
    GraphNode,
    SourceGraphAssembly,
    grouping_arguments,
    load_source_graph_assembly,
)

#: исходы проверки записанной связи (отчётные строки; «текста нет» связь не снимает)
VERDICT_VALUE_PRESENT: Final = "значение есть"
VERDICT_VALUE_ABSENT: Final = "нет значения"
VERDICT_NO_TEXT: Final = "текста нет"

#: связь вне юрисдикции гейта (не `source_assertion` или не `supports`) — в план не входит
VERDICT_OUT_OF_SCOPE: Final = "вне проверки"

_NOT_COMPUTED_EVALUATION: Final = "оценка правил предпросмотром не пересчитана"
_NO_SOURCE_EVIDENCE: Final = (
    "все источные улики утверждения проваливают гейт: без улик переоценка ведёт голову "
    "в invalid и ставит исследовательский вопрос (§5.9.1), оценка не пересчитана"
)


class LinkAuditError(RuntimeError):
    """Отказ аудита: нет effective-снапшота (fail-closed, §3) или некорректный параметр."""


@dataclass(frozen=True)
class LinkAuditLink:
    """Одна записанная связь и исход той же проверки значения, что у гейта записи."""

    evidence_id: uuid.UUID
    source_id: uuid.UUID | None
    canonical_uri: str | None
    session_id: uuid.UUID | None
    evidence_kind: str
    verdict: str
    #: нормализованные числовые токены утверждения, которые искали на тексте страницы
    value_tokens: tuple[str, ...]
    #: почему именно такой исход (для строки отчёта)
    note: str

    @property
    def would_be_excluded(self) -> bool:
        return self.verdict == VERDICT_VALUE_ABSENT

    @property
    def text_missing(self) -> bool:
        return self.verdict == VERDICT_NO_TEXT


@dataclass(frozen=True)
class LinkAuditRow:
    """Утверждение с действующей головой: его связи, группы «было/стало», оценка «было/стало»."""

    claim_id: uuid.UUID
    claim_type: str
    statement: str
    links: tuple[LinkAuditLink, ...]
    groups_changed: bool
    #: partitions by group: canonical uris участников (стабильный порядок, как в preview)
    old_groups: tuple[tuple[str, ...], ...]
    new_groups: tuple[tuple[str, ...], ...]
    #: какие источники уходили бы из участников (по убыванию uri — детерминированно)
    excluded_source_uris: tuple[str, ...]
    evaluation_computed: bool
    evaluation_note: str | None
    persisted_grade: str
    persisted_status: str
    persisted_confidence: float
    new_grade: str | None
    new_status: str | None
    new_confidence: float | None

    @property
    def value_absent_links(self) -> tuple[LinkAuditLink, ...]:
        return tuple(link for link in self.links if link.would_be_excluded)

    @property
    def missing_text_links(self) -> tuple[LinkAuditLink, ...]:
        return tuple(link for link in self.links if link.text_missing)

    @property
    def out_of_scope_links(self) -> tuple[LinkAuditLink, ...]:
        return tuple(link for link in self.links if link.verdict == VERDICT_OUT_OF_SCOPE)

    @property
    def assessment_changed(self) -> bool:
        if not self.evaluation_computed:
            return False
        return (
            self.new_grade != self.persisted_grade
            or self.new_status != self.persisted_status
            or abs((self.new_confidence or 0.0) - self.persisted_confidence) > 1e-9
        )


@dataclass(frozen=True)
class LinkAuditSkipped:
    """Утверждение, которое аудит сознательно не перешёл (причина названа)."""

    claim_id: uuid.UUID
    statement: str
    reason: str


@dataclass(frozen=True)
class LinkAuditReport:
    config_snapshot_id: uuid.UUID | None
    claims_checked: int
    links_checked: int
    rows: tuple[LinkAuditRow, ...]
    skipped: tuple[LinkAuditSkipped, ...]

    @property
    def links_without_value(self) -> int:
        return sum(len(row.value_absent_links) for row in self.rows)

    @property
    def links_missing_text(self) -> int:
        return sum(len(row.missing_text_links) for row in self.rows)

    @property
    def claims_with_absent_links(self) -> int:
        return sum(1 for row in self.rows if row.value_absent_links)

    @property
    def groups_changed_count(self) -> int:
        return sum(1 for row in self.rows if row.groups_changed)

    @property
    def assessment_changed_count(self) -> int:
        return sum(1 for row in self.rows if row.assessment_changed)


def parse_since(raw: str) -> datetime:
    """ISO-дата начала окна (`--since`), как в `research-reattribute`: принимает и «Z»."""

    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError as exc:
        raise LinkAuditError(f"--since: не ISO-дата ({raw!r}): {exc}") from exc
    if parsed.tzinfo is None:
        raise LinkAuditError("--since: нужен таймзона (например 2026-10-07T14:30:00+00:00)")
    return parsed.astimezone(UTC)


def parse_claim_ids(raw: str) -> tuple[uuid.UUID, ...]:
    """Список `--claims`: uuid через запятую (как в `dispute` — только полный id)."""
    ids: list[uuid.UUID] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            ids.append(uuid.UUID(piece))
        except ValueError as exc:
            raise LinkAuditError(f"--claims: не uuid ({piece!r}): {exc}") from exc
    if not ids:
        raise LinkAuditError("--claims: пустой список")
    return tuple(ids)


async def _operator_corrected_source_ids(db: AsyncSession) -> frozenset[uuid.UUID]:
    """Источники действующих коррекций оператора (ADR-0031) — та же защита, что у
    `research-reattribute`: перерешить такое решение без журнала нельзя."""
    rows = (
        (
            await db.execute(
                select(ORMSourceGraphCorrection.from_source_id, ORMSourceGraphCorrection.to_source_id).where(
                    ORMSourceGraphCorrection.valid,
                    ORMSourceGraphCorrection.actor.startswith(OPERATOR_ACTOR_PREFIX),
                )
            )
        )
        .all()
    )
    out: set[uuid.UUID] = set()
    for from_id, to_id in rows:
        for value in (from_id, to_id):
            if isinstance(value, uuid.UUID):
                out.add(value)
    return frozenset(out)


async def _normalized_shas(db: AsyncSession, source_ids: Sequence[uuid.UUID]) -> dict[uuid.UUID, str]:
    """Сохранённый нормализованный текст страницы: `sources.metadata->>'normalized_sha256'`,
    запасной путь — payload существующего `research_fetch_completed` (последняя запись по
    источнику). Ни один путь не найден — источника в результате нет (fail-closed)."""
    if not source_ids:
        return {}
    out: dict[uuid.UUID, str] = {}
    for source in (
        (await db.execute(select(ORMSource).where(ORMSource.id.in_(list(source_ids))))).scalars().all()
    ):
        sha = (source.meta or {}).get("normalized_sha256")
        if isinstance(sha, str) and sha:
            out[source.id] = sha
    missing = [sid for sid in source_ids if sid not in out]
    if not missing:
        return out
    rows = (
        await db.execute(
            select(
                ORMAuditEvent.payload["source_id"].as_string(),
                ORMAuditEvent.payload["normalized_sha256"].as_string(),
            )
            .where(
                ORMAuditEvent.type == "research_fetch_completed",
                ORMAuditEvent.payload["source_id"].as_string().in_([str(sid) for sid in missing]),
            )
            .order_by(
                ORMAuditEvent.payload["source_id"].as_string(),
                ORMAuditEvent.sequence.desc(),
            )
        )
    ).all()
    for raw_id, sha in rows:
        if not isinstance(sha, str) or not sha:
            continue
        try:
            sid = uuid.UUID(str(raw_id))
        except ValueError:
            continue
        out.setdefault(sid, sha)
    return out


def _read_text(store: ArtifactStore, sha: str) -> str | None:
    """Текст сохранённой страницы; сбой чтения — None (fail-closed: связь не трогается)."""
    try:
        return store.get(sha).decode("utf-8")
    except (ArtifactStoreError, OSError, UnicodeDecodeError):
        return None


def _uri_of(source_id: uuid.UUID | None, uris: Mapping[uuid.UUID, str | None]) -> str:
    if source_id is None:
        return "источник не записан"
    uri = uris.get(source_id)
    return uri if uri else f"источник {str(source_id)[:8]}"


def _partition(
    groups: Mapping[uuid.UUID, str], uris: Mapping[uuid.UUID, str | None]
) -> tuple[tuple[str, ...], ...]:
    """Partition of members by group name, stable order (тот же порядок, что у preview)."""
    by_group: dict[str, list[str]] = {}
    for sid, gid in groups.items():
        by_group.setdefault(gid, []).append(_uri_of(sid, uris))
    return tuple(
        tuple(sorted(items)) for _, items in sorted(by_group.items(), key=lambda kv: (kv[0], kv[1]))
    )


def _assembly_without(
    assembly: SourceGraphAssembly, excluded: Iterable[uuid.UUID]
) -> SourceGraphAssembly | None:
    """Тот же снимок графа WITHOUT исключённых источников — ради честного «стало».

    Алгоритм группировки не меняется: остаются участники, их родители (страничные указатели
    и родители по записям значения), рёбра и коррекции только оставшихся узлов. Никакого
    нового признака в БД для этого не появляется: сборка живёт только в памяти процесса."""
    drop = set(excluded)
    members = tuple(sid for sid in assembly.member_ids if sid not in drop)
    if not members:
        return None
    node_by_id: dict[Any, GraphNode] = {node.source_id: node for node in assembly.nodes}
    member_set = set(members)
    parent_ids = sorted(
        (
            {
                node_by_id[sid].pointer_parent
                for sid in members
                if sid in node_by_id and node_by_id[sid].pointer_parent is not None
            }
            | {
                parent
                for sid, parent in assembly.effective_parent.items()
                if sid in member_set and parent is not None
            }
        )
        - member_set
    )
    nodes = tuple(
        node_by_id[sid] for sid in (*members, *parent_ids) if sid in node_by_id
    )
    keep_ids = {node.source_id for node in nodes}
    effective_parent = {
        sid: parent
        for sid, parent in assembly.effective_parent.items()
        if sid in member_set and parent is not None
    }
    return SourceGraphAssembly(
        member_ids=members,
        nodes=nodes,
        edges=tuple(
            edge
            for edge in assembly.edges
            if edge.from_id in keep_ids and edge.to_id in keep_ids
        ),
        corrections=tuple(
            correction
            for correction in assembly.corrections
            if correction.from_id in keep_ids and correction.to_id in keep_ids
        ),
        effective_parent=effective_parent,
    )


async def _links_of(
    db: AsyncSession,
    store: ArtifactStore,
    *,
    claim: ORMClaim,
    evidence_rows: Sequence[ORMEvidence],
) -> tuple[list[LinkAuditLink], dict[uuid.UUID, str | None]]:
    """Связи утверждения и исход той же проверки значения, что у гейта записи."""
    source_ids = sorted(
        {row.source_id for row in evidence_rows if row.source_id is not None},
        key=lambda sid: str(sid),
    )
    uris: dict[uuid.UUID, str | None] = {}
    if source_ids:
        sources = (await db.execute(select(ORMSource).where(ORMSource.id.in_(source_ids)))).scalars().all()
        uris = {source.id: source.canonical_uri for source in sources}
    shas = await _normalized_shas(db, source_ids)
    tokens = statement_value_profile(claim.statement)
    text_cache: dict[str, str | None] = {}
    links: list[LinkAuditLink] = []
    for row in evidence_rows:
        if row.relation != "supports" or row.evidence_kind != "source_assertion":
            continue  # юрисдикция гейта: опровержения и улики других видов не проверяются
        page_text: str | None = None
        note = ""
        sha = shas.get(row.source_id) if row.source_id is not None else None
        if sha is None:
            verdict = VERDICT_NO_TEXT
            note = "нормализованный текст страницы не записан (normalized_sha256 отсутствует)"
        else:
            if sha not in text_cache:
                text_cache[sha] = _read_text(store, sha)
            page_text = text_cache[sha]
            if page_text is None:
                verdict = VERDICT_NO_TEXT
                note = f"сохранённый текст страницы не читается ({sha[:12]}…)"
            elif not tokens:
                verdict = VERDICT_OUT_OF_SCOPE
                note = "утверждение без измеренного числового профиля: гейт значения его не проверяет (T7.108, T7.120)"
            elif link_value_gate(statement=claim.statement, texts=(None, page_text)) == VALUE_GATE_ABSENT:
                verdict = VERDICT_VALUE_ABSENT
                note = f"значения {', '.join(tokens)} на сохранённом тексте страницы нет"
            else:
                verdict = VERDICT_VALUE_PRESENT
                note = f"значение {', '.join(tokens)} на сохранённом тексте страницы есть"
        links.append(
            LinkAuditLink(
                evidence_id=row.id,
                source_id=row.source_id,
                canonical_uri=uris.get(row.source_id) if row.source_id is not None else None,
                session_id=row.created_in_session,
                evidence_kind=row.evidence_kind,
                verdict=verdict,
                value_tokens=tokens,
                note=note,
            )
        )
    return links, uris


async def audit_recorded_links(
    db: AsyncSession,
    store: ArtifactStore,
    *,
    since: datetime | None = None,
    claim_ids: Sequence[uuid.UUID] | None = None,
) -> LinkAuditReport:
    """Read-only план по уже записанным связям утверждений effective-снапшота."""
    try:
        snapshot: ORMConfigSnapshot = await ConfigService.get_effective(db)
    except ConfigError as exc:
        raise LinkAuditError(f"effective config: {exc}") from exc

    stmt = select(ORMClaimAssessmentHead).where(
        ORMClaimAssessmentHead.config_snapshot_id == snapshot.id,
        ORMClaimAssessmentHead.assessment_state == "current",
    )
    if claim_ids:
        stmt = stmt.where(ORMClaimAssessmentHead.claim_id.in_(list(claim_ids)))
    if since is not None:
        stmt = stmt.where(
            select(ORMEvidence.id)
            .where(ORMEvidence.claim_id == ORMClaimAssessmentHead.claim_id, ORMEvidence.created_at >= since)
            .exists()
        )
    heads = (await db.execute(stmt.order_by(ORMClaimAssessmentHead.claim_id))).scalars().all()

    corrected = await _operator_corrected_source_ids(db)
    service = MemoryService(snapshot)

    claims_checked = 0
    links_checked = 0
    rows: list[LinkAuditRow] = []
    skipped: list[LinkAuditSkipped] = []

    for head in heads:
        claim_id = head.claim_id
        claim = await db.get(ORMClaim, claim_id)
        if claim is None:
            continue
        evidence_rows = (
            (await db.execute(select(ORMEvidence).where(ORMEvidence.claim_id == claim_id).order_by(ORMEvidence.id)))
            .scalars()
            .all()
        )
        links, uris = await _links_of(db, store, claim=claim, evidence_rows=evidence_rows)
        if not links:
            continue  # утверждение без связных `source_assertion`-улик: проверять нечего
        claims_checked += 1
        links_checked += len(links)

        link_sources = {link.source_id for link in links if link.source_id is not None}
        touched = sorted(link_sources & corrected, key=lambda sid: str(sid))
        if touched:
            skipped.append(
                LinkAuditSkipped(
                    claim_id=claim_id,
                    statement=claim.statement,
                    reason=(
                        "под коррекцией оператора (ADR-0031): "
                        + ", ".join(_uri_of(sid, uris) for sid in touched)
                    ),
                )
            )
            continue

        absent = [link for link in links if link.would_be_excluded]
        missing_text = [link for link in links if link.text_missing]
        if not absent and not missing_text:
            continue  # все связи проходят ту же проверку: менять нечего (идемпотентность)

        row = await _recompute(db, service, claim, evidence_rows, links, uris, absent)
        rows.append(row)

    return LinkAuditReport(
        config_snapshot_id=snapshot.id,
        claims_checked=claims_checked,
        links_checked=links_checked,
        rows=tuple(rows),
        skipped=tuple(skipped),
    )


async def _recompute(
    db: AsyncSession,
    service: MemoryService,
    claim: ORMClaim,
    evidence_rows: Sequence[ORMEvidence],
    links: Sequence[LinkAuditLink],
    uris: Mapping[uuid.UUID, str | None],
    absent: Sequence[LinkAuditLink],
) -> LinkAuditRow:
    """Группы и оценка «было → стало» при снятии проваливших связей — чистым пересчётом.

    Ничего не записывается: сборка и `evaluate` живут только в памяти процесса. «Было» —
    записанный снимок независимости действующей головы (тот же источник, что у preview)."""
    claim_id = claim.id
    excluded_ids = {link.evidence_id for link in absent}
    remaining_sources = {
        row.source_id for row in evidence_rows if row.source_id is not None and row.id not in excluded_ids
    }
    excluded_sources = {
        row.source_id
        for row in evidence_rows
        if row.source_id is not None and row.id in excluded_ids and row.source_id not in remaining_sources
    }

    assembly = await load_source_graph_assembly(db, claim_id=claim_id)
    filtered = _assembly_without(assembly, excluded_sources) if assembly is not None else None

    assessment = await _current_assessment(db, claim_id)
    old_members: dict[uuid.UUID, str] = {}
    if assessment is not None and assessment.source_independence_snapshot_id is not None:
        for member in (
            (
                await db.execute(
                    select(ORMSourceIndependenceMember).where(
                        ORMSourceIndependenceMember.snapshot_id == assessment.source_independence_snapshot_id
                    )
                )
            )
            .scalars()
            .all()
        ):
            old_members[member.source_id] = member.group_id

    new_members: dict[uuid.UUID, str] = {}
    if filtered is not None:
        inputs, edges, corrections = grouping_arguments(filtered)
        result = group_source_graph(inputs, edges, corrections)
        new_members = {sid: result.groups[sid] for sid in filtered.member_ids}

    old_partition = _partition(old_members, uris)
    new_partition = _partition(new_members, uris) if filtered is not None else ()

    new_grade: str | None = None
    new_status: str | None = None
    new_confidence: float | None = None
    note: str | None = None
    kept_rows = [row for row in evidence_rows if row.id not in excluded_ids]
    if not kept_rows:
        note = _NO_SOURCE_EVIDENCE
    else:
        new_grade, new_status, new_confidence, note = await _evaluate(
            db, service, claim, kept_rows, new_members, assessment
        )

    return LinkAuditRow(
        claim_id=claim_id,
        claim_type=claim.claim_type,
        statement=claim.statement,
        links=tuple(links),
        groups_changed=old_partition != new_partition,
        old_groups=old_partition,
        new_groups=new_partition,
        excluded_source_uris=tuple(sorted(_uri_of(sid, uris) for sid in excluded_sources)),
        evaluation_computed=new_grade is not None,
        evaluation_note=note,
        persisted_grade=assessment.effective_grade if assessment is not None else "—",
        persisted_status=str(assessment.epistemic_status) if assessment is not None else "—",
        persisted_confidence=float(assessment.confidence) if assessment is not None else 0.0,
        new_grade=new_grade,
        new_status=new_status,
        new_confidence=new_confidence,
    )


async def _current_assessment(db: AsyncSession, claim_id: uuid.UUID) -> ORMClaimAssessment | None:
    """Оценка действующей головы effective-снапшота (head 'current' ⇔ оценка, §3)."""
    head = (
        (
            await db.execute(
                select(ORMClaimAssessmentHead).where(ORMClaimAssessmentHead.claim_id == claim_id)
            )
        )
        .scalars()
        .first()
    )
    if head is None or head.current_assessment_id is None:
        return None
    return await db.get(ORMClaimAssessment, head.current_assessment_id)


async def _evaluate(
    db: AsyncSession,
    service: MemoryService,
    claim: ORMClaim,
    kept_rows: Sequence[ORMEvidence],
    new_members: Mapping[uuid.UUID, str],
    assessment: ORMClaimAssessment | None,
) -> tuple[str | None, str | None, float | None, str | None]:
    """Оценка «стало» чистым `evaluate` (единственный производитель оценок) на оставшихся уликах."""
    env_groups: dict[uuid.UUID, str] = {}
    env_relations: dict[uuid.UUID, str] = {}
    if assessment is not None and assessment.environment_independence_snapshot_id is not None:
        for member in (
            (
                await db.execute(
                    select(ORMEnvironmentIndependenceMember).where(
                        ORMEnvironmentIndependenceMember.snapshot_id
                        == assessment.environment_independence_snapshot_id
                    )
                )
            )
            .scalars()
            .all()
        ):
            env_groups[member.environment_manifest_id] = member.group_id
            env_relations[member.environment_manifest_id] = member.relation
    resolved_ids = {
        resolution.evidence_id
        for resolution in (
            await db.execute(
                select(ORMCounterevidenceResolution).where(
                    ORMCounterevidenceResolution.valid,
                    ORMCounterevidenceResolution.evidence_id.in_([row.id for row in kept_rows]),
                )
            )
        )
        .scalars()
        .all()
    }
    evidences = [
        EvaluatedEvidence(
            identity_hash=row.identity_hash,
            kind=row.evidence_kind,
            relation=row.relation,
            scope=dict(row.scope or {}),
            independence_group=(
                new_members[row.source_id]
                if row.source_id is not None and row.source_id in new_members
                else (
                    env_groups.get(row.environment_manifest_id, UNTRACKED_GROUP)
                    if row.environment_manifest_id is not None
                    else UNTRACKED_GROUP
                )
            ),
            env_relation=(
                env_relations.get(row.environment_manifest_id, "none")
                if row.environment_manifest_id is not None
                else "none"
            ),
            resolved=row.id in resolved_ids,
        )
        for row in kept_rows
    ]
    try:
        outcome = evaluate(
            claim.claim_type,
            service.rule(claim.claim_type),
            claim_scope=await _latest_assessed_scope(db, claim.id),
            evidences=evidences,
            has_as_of=claim.as_of is not None,
        )
    except ValueError as exc:  # RuleValidationError и отказ подбора правила
        return None, None, None, f"{_NOT_COMPUTED_EVALUATION}: {exc}"
    return outcome.grade, str(outcome.epistemic_status), float(outcome.confidence), None


async def _latest_assessed_scope(db: AsyncSession, claim_id: uuid.UUID) -> dict[str, Any]:
    """Тот же источник области оценки, что у рабочего переоценки и у preview (scope носят оценки)."""
    row = (
        await db.execute(
            select(ORMClaimAssessment.assessed_scope)
            .where(ORMClaimAssessment.claim_id == claim_id)
            .order_by(ORMClaimAssessment.created_at.desc(), ORMClaimAssessment.id.desc())
            .limit(1)
        )
    ).first()
    if row is None or row[0] is None:
        return {}
    return dict(row[0])
