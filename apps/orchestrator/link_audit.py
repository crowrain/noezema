"""Аудит «значение на связи» для УЖЕ записанных связей (T7.124) и честный отзыв связи (T7.125).

Гейт значения (T7.108, ADR-0034 §10; профили T7.113 и T7.123) ведёт КАЖДУЮ новую
`supports`-связь с уликой `source_assertion` и снимает её при записи. Ранее записанные связи
гейты не затрагивают никогда (ADR-0038 §12): стенд 2026-08-29 (сессия 64837cb5, утверждение
83e7a1c8) живёт с двумя связями к странице про ключевую ставку, где значения «9,6» нет, и
оценка E3 «Проверено» держится на двух группах независимости, одна из которых — эти связи.
Этот модуль отвечает честно на вопрос «что было бы, если бы гейт стоял и на записанных
связях»: перечисляет связи, называет исход той же проверки и пересчитывает группы/оценку
теми же чистыми функциями, что у рабочего переоценки и у `independence-preview`
(`load_source_graph_assembly` + `group_source_graph` + `evaluate`) — БЕЗ ЕДИНОЙ ЗАПИСИ.

Почему в T7.124 применения не было и чем оно стало в T7.125 (остановка той задачи по
стоп-критерию; разбор вариантов — ADR-0038 §13.2, принятое решение — ADR-0039). В прежней схеме
не было признака «улика не участвует в оценке»:

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

Следствие (решение пользователя 2026-10-19, ADR-0039 — минимальный честный вариант ADR-0038
§13.4): отзыв уже записанной связи — отдельная append-only сущность `evidence_link_revocations`
(миграция 0026) по образцу `counterevidence_resolutions` плюс предикат «нет действующего отзыва»
на входе сборки оценки (`reassessment.py`) и на входе группировщика (`source_graph.py`). Строки
`evidence` при этом не меняются никогда: отозванная связь остаётся в журнале и отдельной
строкой называется в плане и в выводе команды; витрина отдельной строки об отзыве пока не
делает (остаток ADR-0039 §6). Запись делает `apply_link_revocations` (и отмену —
`cancel_link_revocations`); план `audit_recorded_links` по-прежнему не пишет ни одной строки.

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

План (`audit_recorded_links`) не пишет ни одной строки и не вызывает ни одного сервиса записи.
Запись появляется только в `apply_link_revocations` / `cancel_link_revocations`: append-only
строка отзыва (или колонки отмены у существующей), audit-событие `link_revocation_created` /
`link_revocation_cancelled` и каскад §11.3/§8.6 `heads → pending` + одна durable-задача
переоценки — тот же путь, что у коррекций графа источников и разрешений спорных улик; рабочий
`run_reassessment_batch` вызывает вызывающая сторона. Оценку ни план, ни отзыв не назначают:
план пересчитывает чистым `evaluate` (единственный производитель оценок, §3) и называет результат
прогнозом, а новую действующую оценку пишет rules engine в рабочем батче.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.orchestrator.curator_rejections import (
    VALUE_GATE_ABSENT,
    link_value_gate,
    statement_value_profile,
)
from apps.research_proxy.reattribution import OPERATOR_ACTOR_PREFIX
from packages.artifacts.store import ArtifactStore, ArtifactStoreError
from packages.domain.models.base import JsonDict
from packages.domain.models.config import ORMConfigSnapshot
from packages.domain.models.enums import AuditEventType, ReassessmentJobStatus
from packages.domain.models.events import ORMAuditEvent
from packages.domain.models.memory import (
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMCounterevidenceResolution,
    ORMEnvironmentIndependenceMember,
    ORMEvidence,
    ORMLinkRevocation,
    ORMReassessmentJob,
    ORMSource,
    ORMSourceGraphCorrection,
    ORMSourceIndependenceMember,
)
from packages.domain.services.audit import AuditService
from packages.domain.services.config import ConfigError, ConfigService
from packages.memory.env_independence import UNTRACKED_GROUP
from packages.memory.independence import group_source_graph
from packages.memory.rules_engine import RULES_ENGINE_VERSION, EvaluatedEvidence, evaluate
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
    #: T7.125 (ADR-0039): действующий отзыв этой связи (id записи и причина оператора);
    #: None — отзыва нет. План без `--apply` обязан показывать уже отозванные связи.
    revocation_id: uuid.UUID | None = None
    revocation_reason: str | None = None
    revocation_actor: str | None = None
    #: сохранённый нормализованный текст страницы, по которому измерен исход (для basis отзыва)
    page_sha256: str | None = None

    @property
    def would_be_excluded(self) -> bool:
        return self.verdict == VERDICT_VALUE_ABSENT

    @property
    def text_missing(self) -> bool:
        return self.verdict == VERDICT_NO_TEXT

    @property
    def revoked(self) -> bool:
        """Связь уже снята действующим отзывом оператора: она вне оценки и вне групп."""
        return self.revocation_id is not None


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
    def revoked_links(self) -> tuple[LinkAuditLink, ...]:
        """Связи, уже снятые действующим отзывом оператора (T7.125): они не участвуют ни
        в оценке, ни в группах — но остаются в плане с причиной отзыва."""
        return tuple(link for link in self.links if link.revoked)

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

    @property
    def links_revoked(self) -> int:
        """Связей, уже снятых действующим отзывом оператора (T7.125): они учтены в пересчёте
        («стало» совпадает с записанным), но названы в плане отдельной строкой."""
        return sum(len(row.revoked_links) for row in self.rows)


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
    return _parse_uuid_list(raw, option="--claims")


def _parse_uuid_list(raw: str, *, option: str) -> tuple[uuid.UUID, ...]:
    ids: list[uuid.UUID] = []
    for part in raw.split(","):
        piece = part.strip()
        if not piece:
            continue
        try:
            ids.append(uuid.UUID(piece))
        except ValueError as exc:
            raise LinkAuditError(f"{option}: не uuid ({piece!r}): {exc}") from exc
    if not ids:
        raise LinkAuditError(f"{option}: пустой список")
    return tuple(ids)


def parse_evidence_ids(raw: str) -> tuple[uuid.UUID, ...]:
    """Список `--evidence` (T7.125): uuid улик через запятую — только перечисленные улики
    получают отзыв; пакетного отзыва «все, где гейт сказал нет значения» нет намеренно."""
    return _parse_uuid_list(raw, option="--evidence")


def parse_revocation_ids(raw: str) -> tuple[uuid.UUID, ...]:
    """Список `--revocation` (T7.125): uuid записей отзыва через запятую."""
    return _parse_uuid_list(raw, option="--revocation")


async def claims_of_evidence(db: AsyncSession, evidence_ids: Sequence[uuid.UUID]) -> tuple[uuid.UUID, ...]:
    """Утверждения перечисленных улик — область плана при `--apply` (чтобы оператор видел
    «было → стало» именно по тем связкам, которые собирается отозвать)."""
    if not evidence_ids:
        return ()
    return tuple(
        row[0]
        for row in (
            await db.execute(
                select(ORMEvidence.claim_id)
                .where(ORMEvidence.id.in_(list(evidence_ids)))
                .distinct()
                .order_by(ORMEvidence.claim_id)
            )
        ).all()
    )


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
    #: T7.125: план обязан показывать уже отозванные связи (они вне оценки и вне групп,
    #: но остаются в отчёте — «исключено» не значит «стёрто»)
    revocations = await _active_revocations(db, [row.id for row in evidence_rows])
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
        existing = revocations.get(row.id)
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
                page_sha256=sha,
                revocation_id=existing.id if existing is not None else None,
                revocation_reason=existing.reason if existing is not None else None,
                revocation_actor=existing.actor if existing is not None else None,
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


# ─── T7.125 (ADR-0039, ADR-0038 §13.4): отзыв связи и его отмена ──────────────────

#: причина durable-задачи переоценки после отзыва или отмены (тот же образец, что
#: `JOB_REASON` у разрешений спорных улик, `packages/memory/resolutions.py:50`)
REVOCATION_JOB_REASON: Final = "link_revocation_change"
#: система, подготовившая голову в состоянии pending (перечисление расширено CHECK'ом
#: миграции 0026 — тот же путь, что 0007/0011/0012)
REVOCATION_PREPARED_BY: Final = "system:link_revocation"
#: чем именно установлен отзыв (пишется в basis и печатается в выводе)
REVOCATION_METHOD: Final = "link-audit --apply: гейт значения T7.108/T7.123 по сохранённой странице"

#: коды отказа отзыва; отказ относится ко ВСЕМУ вызову: ни одной записи при отказе нет
REFUSAL_UNKNOWN_EVIDENCE: Final = "нет такой улики"
REFUSAL_UNKNOWN_CLAIM: Final = "нет такого утверждения"
REFUSAL_OUT_OF_SCOPE: Final = "вне юрисдикции гейта"
REFUSAL_VALUE_PRESENT: Final = "значение на странице есть"
REFUSAL_TEXT_MISSING: Final = "текста страницы нет"
REFUSAL_NO_PROFILE: Final = "утверждение без измеренного значения"
REFUSAL_OPERATOR_CORRECTION: Final = "под коррекцией оператора"
REFUSAL_NO_CURRENT_HEAD: Final = "нет действующей оценки"
REFUSAL_LAST_EVIDENCE: Final = "у утверждения не останется ни одной улики"


class LinkRevocationError(RuntimeError):
    """Отзыв связи не выполнен: проверка не пройдена или не названа причина.

    `refusals` — поимённый перечень того, что не прошло (улика, утверждение, адрес,
    причина). Ни одна строка при отказе не записывается: вызов отказывает целиком."""

    def __init__(self, detail: str, *, refusals: Sequence[LinkRevocationRefusal] = ()) -> None:
        super().__init__(detail)
        self.refusals: tuple[LinkRevocationRefusal, ...] = tuple(refusals)


@dataclass(frozen=True)
class LinkRevocationRefusal:
    code: str
    evidence_id: uuid.UUID | None
    claim_id: uuid.UUID | None
    canonical_uri: str | None
    detail: str


@dataclass(frozen=True)
class LinkRevocationWrite:
    """Что записано этим вызовом: id отзыва, улика, утверждение, адрес, причина, исход."""

    revocation_id: uuid.UUID
    evidence_id: uuid.UUID
    claim_id: uuid.UUID
    canonical_uri: str | None
    reason: str
    verdict: str
    value_tokens: tuple[str, ...]


@dataclass(frozen=True)
class LinkRevocationAlready:
    """Связь уже отозвана действующим отзывом: повтор ничего не записывает (идемпотентность)."""

    revocation_id: uuid.UUID
    evidence_id: uuid.UUID
    claim_id: uuid.UUID
    canonical_uri: str | None
    reason: str
    actor: str


@dataclass(frozen=True)
class LinkRevocationResult:
    written: tuple[LinkRevocationWrite, ...]
    already_revoked: tuple[LinkRevocationAlready, ...]
    claims_affected: tuple[uuid.UUID, ...]
    heads_invalidated: int
    jobs_created: int
    #: утверждения, у которых после отзыва не остаётся НИ ОДНОЙ улики: рабочий батч
    #: переведёт голову в invalid и поставит исследовательский вопрос (§5.9.1). Без
    #: `--allow-empty` такой отзыв не записывается.
    heads_to_invalid: tuple[uuid.UUID, ...]
    #: утверждения, у которых не остаётся ИСТОЧНЫХ улик (групп независимости больше нет),
    #: но улики другого вида остались: это законно и печатается отдельной строкой
    claims_without_source_links: tuple[uuid.UUID, ...]


@dataclass(frozen=True)
class LinkRevocationCancelResult:
    cancelled: tuple[uuid.UUID, ...]
    already_cancelled: tuple[uuid.UUID, ...]
    claims_affected: tuple[uuid.UUID, ...]
    heads_invalidated: int
    jobs_created: int


async def _active_revocations(
    db: AsyncSession, evidence_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, ORMLinkRevocation]:
    """Действующие отзывы перечисленных улик (partial unique 0026 — не больше одного)."""
    if not evidence_ids:
        return {}
    rows = (
        await db.execute(
            select(ORMLinkRevocation)
            .where(
                ORMLinkRevocation.evidence_id.in_(list(evidence_ids)),
                ORMLinkRevocation.cancelled_at.is_(None),
            )
            .order_by(ORMLinkRevocation.evidence_id)
        )
    ).scalars().all()
    return {row.evidence_id: row for row in rows}


async def _revocation_cascade_claim(
    db: AsyncSession, *, claim_id: uuid.UUID, snapshot_id: uuid.UUID
) -> tuple[int, int]:
    """Тот же каскад §11.3/§8.6, что у коррекций графа и у разрешений спорных улик: голова
    действующей оценки становится pending (оба признака current — NULL, §3 Lifecycle), для
    (утверждение, снимок) заводится ровно одна durable-задача переоценки (partial unique
    делает повтор идемпотентным). Рабочий `run_reassessment_batch` вызывает вызывающая
    сторона отдельно. Возвращает (снятых голов, созданных задач)."""
    res = await db.execute(
        text(
            "UPDATE claim_assessment_heads SET assessment_state = 'pending', "
            "current_assessment_id = NULL, epistemic_status = NULL, "
            "prepared_by = :p, updated_at = clock_timestamp() "
            "WHERE claim_id = :c AND config_snapshot_id = :s "
            "AND assessment_state = 'current' RETURNING 1"
        ),
        {"c": claim_id, "s": snapshot_id, "p": REVOCATION_PREPARED_BY},
    )
    if res.first() is None:
        return (0, 0)
    exists = (
        (
            await db.execute(
                text(
                    "SELECT 1 FROM reassessment_jobs "
                    "WHERE claim_id = :c AND target_config_snapshot_id = :s "
                    "AND status IN ('queued','leased','retry')"
                ),
                {"c": claim_id, "s": snapshot_id},
            )
        ).scalar_one_or_none()
    )
    if exists is not None:
        return (1, 0)
    db.add(
        ORMReassessmentJob(
            id=uuid.uuid4(),
            claim_id=claim_id,
            target_config_snapshot_id=snapshot_id,
            status=ReassessmentJobStatus.QUEUED.value,
            reason=REVOCATION_JOB_REASON,
        )
    )
    await db.flush()
    return (1, 1)


def _dedup(ids: Sequence[uuid.UUID]) -> list[uuid.UUID]:
    """Порядок оператора сохранён, повторы убраны (повторный uuid — не вторая запись)."""
    out: list[uuid.UUID] = []
    seen: set[uuid.UUID] = set()
    for raw in ids:
        if raw not in seen:
            seen.add(raw)
            out.append(raw)
    return out


async def apply_link_revocations(
    db: AsyncSession,
    audit: AuditService,
    store: ArtifactStore,
    *,
    evidence_ids: Sequence[uuid.UUID],
    reason: str,
    actor: str,
    allow_empty: bool = False,
) -> LinkRevocationResult:
    """Записать отзыв перечисленных оператором связей + каскад переоценки (одна транзакция).

    Проверки (каждая названа; при любой неудаче отказывается весь вызов, записей нет):

    1. `--reason` обязан быть непустым: отзыв без причины — не решение, а след;
    2. улика существует и находится в юрисдикции гейта (`supports` + `source_assertion`);
    3. её действующего отзыва нет (повтор — «уже отозвана», ничего не записывается);
    4. verdict того же гейта значения — ровно «нет значения»: «значение есть», «текста нет»
       и «вне проверки» (утверждение без измеренного числового профиля) — отказ;
    5. источник улики не покрыт действующей коррекцией оператора (ADR-0031): решение
       оператора без журнала отменять нельзя;
    6. у утверждения есть действующая голова effective-снапшота (перезаписывать нечего —
       иначе отзыв был бы записью без последствий);
    7. после отзыва у утверждения остаётся хоть одна улика, иначе нужен явный `--allow-empty`
       (рабочий батч по §5.9.1 переведёт голову в invalid и поставит вопрос).

    Оценку функция не назначает: она снимает голову с действующей оценки и заводит задачу,
    grade заново вычислит rules engine в рабочем батче (§3.7).
    """
    clean_reason = reason.strip()
    if not clean_reason:
        raise LinkRevocationError("--reason обязателен: отзыв связи без названной причины не записывается")
    wanted = _dedup(evidence_ids)
    if not wanted:
        raise LinkRevocationError(
            "--evidence обязателен: отзыв записывается только по явно перечисленным уликам"
        )

    try:
        snapshot: ORMConfigSnapshot = await ConfigService.get_effective(db)
    except ConfigError as exc:
        raise LinkAuditError(f"effective config: {exc}") from exc

    rows = (
        (await db.execute(select(ORMEvidence).where(ORMEvidence.id.in_(wanted))))
        .scalars()
        .all()
    )
    row_by_id = {row.id: row for row in rows}
    claim_ids = sorted({row.claim_id for row in rows}, key=lambda value: str(value))

    claims: dict[uuid.UUID, ORMClaim] = {}
    for claim_id in claim_ids:
        claim = await db.get(ORMClaim, claim_id)
        if claim is not None:
            claims[claim_id] = claim
    evidence_by_claim: dict[uuid.UUID, list[ORMEvidence]] = {}
    links_by_claim: dict[uuid.UUID, dict[uuid.UUID, LinkAuditLink]] = {}
    uris_by_claim: dict[uuid.UUID, dict[uuid.UUID, str | None]] = {}
    for claim_id in claim_ids:
        claim = claims.get(claim_id)
        if claim is None:
            continue
        evidence_rows = list(
            (
                await db.execute(
                    select(ORMEvidence).where(ORMEvidence.claim_id == claim_id).order_by(ORMEvidence.id)
                )
            )
            .scalars()
            .all()
        )
        links, uris = await _links_of(db, store, claim=claim, evidence_rows=evidence_rows)
        evidence_by_claim[claim_id] = evidence_rows
        links_by_claim[claim_id] = {link.evidence_id: link for link in links}
        uris_by_claim[claim_id] = uris

    revocations = await _active_revocations(db, wanted)
    corrected = await _operator_corrected_source_ids(db)
    head_state = {
        head.claim_id: head.assessment_state
        for head in (
            await db.execute(
                select(ORMClaimAssessmentHead).where(
                    ORMClaimAssessmentHead.config_snapshot_id == snapshot.id,
                    ORMClaimAssessmentHead.claim_id.in_(claim_ids),
                )
            )
        ).scalars().all()
    }

    refusals: list[LinkRevocationRefusal] = []
    already: list[LinkRevocationAlready] = []
    candidates: list[tuple[ORMEvidence, LinkAuditLink]] = []
    for evidence_id in wanted:
        row = row_by_id.get(evidence_id)
        if row is None:
            refusals.append(
                LinkRevocationRefusal(
                    code=REFUSAL_UNKNOWN_EVIDENCE,
                    evidence_id=evidence_id,
                    claim_id=None,
                    canonical_uri=None,
                    detail="улики с таким id в журнале нет",
                )
            )
            continue
        claim = claims.get(row.claim_id)
        if claim is None:
            refusals.append(
                LinkRevocationRefusal(
                    code=REFUSAL_UNKNOWN_CLAIM,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=None,
                    detail="утверждения этой улики в журнале нет",
                )
            )
            continue
        uri = _uri_of(row.source_id, uris_by_claim.get(row.claim_id, {}))
        link = links_by_claim.get(row.claim_id, {}).get(evidence_id)
        existing = revocations.get(evidence_id)
        if existing is not None:
            already.append(
                LinkRevocationAlready(
                    revocation_id=existing.id,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=link.canonical_uri if link is not None else None,
                    reason=existing.reason,
                    actor=existing.actor,
                )
            )
            continue
        if link is None:
            refusals.append(
                LinkRevocationRefusal(
                    code=REFUSAL_OUT_OF_SCOPE,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=uri,
                    detail=(
                        f"связь не проходит юрисдикцию гейта (relation={row.relation!r}, "
                        f"evidence_kind={row.evidence_kind!r}): отзыв разрешён только для "
                        "`supports`-связей улики `source_assertion`"
                    ),
                )
            )
            continue
        if row.source_id is not None and row.source_id in corrected:
            refusals.append(
                LinkRevocationRefusal(
                    code=REFUSAL_OPERATOR_CORRECTION,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=link.canonical_uri,
                    detail=(
                        "источник связи покрыт действующей коррекцией оператора (ADR-0031): "
                        "такое решение этой командой не отменяется"
                    ),
                )
            )
            continue
        if head_state.get(row.claim_id) != "current":
            refusals.append(
                LinkRevocationRefusal(
                    code=REFUSAL_NO_CURRENT_HEAD,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=link.canonical_uri,
                    detail=(
                        f"у утверждения нет действующей оценки effective-снапшота "
                        f"(состояние головы: {head_state.get(row.claim_id) or 'нет головы'}): "
                        "пересчитывать нечего"
                    ),
                )
            )
            continue
        if link.verdict != VERDICT_VALUE_ABSENT:
            code = {
                VERDICT_VALUE_PRESENT: REFUSAL_VALUE_PRESENT,
                VERDICT_NO_TEXT: REFUSAL_TEXT_MISSING,
            }.get(link.verdict, REFUSAL_NO_PROFILE)
            refusals.append(
                LinkRevocationRefusal(
                    code=code,
                    evidence_id=evidence_id,
                    claim_id=row.claim_id,
                    canonical_uri=link.canonical_uri,
                    detail=(
                        f"исход той же проверки значения — «{link.verdict}»: {link.note}. "
                        "Отзыв разрешён только при «нет значения»"
                    ),
                )
            )
            continue
        candidates.append((row, link))

    if refusals:
        raise LinkRevocationError(
            f"отзыв не записан: проверок не прошли {len(refusals)} из {len(wanted)} улик",
            refusals=refusals,
        )

    # последствие для утверждения считается по ВСЕМ действующим отзывам (и прежним, и новым)
    active_ids_by_claim: dict[uuid.UUID, set[uuid.UUID]] = {}
    for claim_id, evidence_rows in evidence_by_claim.items():
        active_ids_by_claim[claim_id] = {
            row.id
            for row in evidence_rows
            if row.id in revocations or any(row.id == cand[0].id for cand in candidates)
        }
    heads_to_invalid: list[uuid.UUID] = []
    no_source_links: list[uuid.UUID] = []
    affected_claims = sorted({row.claim_id for row, _link in candidates}, key=lambda value: str(value))
    for claim_id in affected_claims:
        remaining = [
            row
            for row in evidence_by_claim.get(claim_id, [])
            if row.id not in active_ids_by_claim.get(claim_id, set())
        ]
        if not remaining:
            heads_to_invalid.append(claim_id)
        elif not [row for row in remaining if row.source_id is not None]:
            no_source_links.append(claim_id)
    if heads_to_invalid and not allow_empty:
        raise LinkRevocationError(
            "отзыв не записан: после него у утверждения "
            + ", ".join(str(claim_id)[:8] for claim_id in heads_to_invalid)
            + " не останется ни одной улики. переоценка по §5.9.1 переведёт голову в invalid "
            "и поставит исследовательский вопрос. это отдельное решение: нужен --allow-empty",
            refusals=[
                LinkRevocationRefusal(
                    code=REFUSAL_LAST_EVIDENCE,
                    evidence_id=None,
                    claim_id=claim_id,
                    canonical_uri=None,
                    detail=(
                        "это единственная связь утверждения: без улик голова уходит в invalid, "
                        "оценки не будет (--allow-empty)"
                    ),
                )
                for claim_id in heads_to_invalid
            ],
        )

    written: list[LinkRevocationWrite] = []
    for row, link in candidates:
        basis: JsonDict = {
            "method": REVOCATION_METHOD,
            "verdict": link.verdict,
            "note": link.note,
            "value_tokens": list(link.value_tokens),
            "relation": row.relation,
            "evidence_kind": row.evidence_kind,
            "source_uri": link.canonical_uri,
            "page_sha256": link.page_sha256,
            "config_snapshot_id": str(snapshot.id),
            "config_snapshot_sha256": snapshot.sha256,
            "rules_engine_version": RULES_ENGINE_VERSION,
        }
        revocation = ORMLinkRevocation(
            id=uuid.uuid4(),
            evidence_id=row.id,
            claim_id=row.claim_id,
            reason=clean_reason,
            basis=basis,
            actor=actor,
            rules_version=RULES_ENGINE_VERSION,
        )
        db.add(revocation)
        await db.flush()
        written.append(
            LinkRevocationWrite(
                revocation_id=revocation.id,
                evidence_id=row.id,
                claim_id=row.claim_id,
                canonical_uri=link.canonical_uri,
                reason=clean_reason,
                verdict=link.verdict,
                value_tokens=link.value_tokens,
            )
        )

    invalidated = 0
    jobs_created = 0
    for claim_id in affected_claims:
        removed, jobs = await _revocation_cascade_claim(
            db, claim_id=claim_id, snapshot_id=snapshot.id
        )
        invalidated += removed
        jobs_created += jobs

    for item in written:
        await audit.record(
            AuditEventType.LINK_REVOCATION_CREATED,
            actor=actor,
            payload={
                "revocation_id": str(item.revocation_id),
                "evidence_id": str(item.evidence_id),
                "claim_id": str(item.claim_id),
                "reason": item.reason,
                "verdict": item.verdict,
                "method": REVOCATION_METHOD,
                "config_snapshot_id": str(snapshot.id),
                "heads_invalidated": invalidated,
                "jobs_created": jobs_created,
            },
            public_summary=(
                f"связь улики {str(item.evidence_id)[:8]} отозвана оператором: {item.reason}"
            ),
        )

    return LinkRevocationResult(
        written=tuple(written),
        already_revoked=tuple(already),
        claims_affected=tuple(affected_claims),
        heads_invalidated=invalidated,
        jobs_created=jobs_created,
        heads_to_invalid=tuple(heads_to_invalid),
        claims_without_source_links=tuple(no_source_links),
    )


async def cancel_link_revocations(
    db: AsyncSession,
    audit: AuditService,
    *,
    revocation_ids: Sequence[uuid.UUID],
    reason: str,
    actor: str,
) -> LinkRevocationCancelResult:
    """Отменить отзыв (связь возвращается в оценку) + тот же каскад переоценки.

    История не правится: строка отзыва остаётся навсегда, у неё заполняются колонки отмены
    (`cancelled_at`, `cancelled_by`, `cancel_reason`) — ровно как `valid=false` у коррекций
    оператора и разрешений спорных улик. Повтор — «уже отменена», записей нет."""
    clean_reason = reason.strip()
    if not clean_reason:
        raise LinkRevocationError(
            "--reason обязателен: отмена отзыва без названной причины не записывается"
        )
    wanted = _dedup(revocation_ids)
    if not wanted:
        raise LinkRevocationError("--revocation обязателен: перечислите id отзывов")

    rows = (
        (await db.execute(select(ORMLinkRevocation).where(ORMLinkRevocation.id.in_(wanted))))
        .scalars()
        .all()
    )
    found = {row.id: row for row in rows}
    refusals = [
        LinkRevocationRefusal(
            code="нет такого отзыва",
            evidence_id=None,
            claim_id=None,
            canonical_uri=None,
            detail=f"отзыва связи с id {str(missing)[:8]} в журнале нет",
        )
        for missing in wanted
        if missing not in found
    ]
    if refusals:
        raise LinkRevocationError(
            f"отмена не записана: {len(refusals)} из {len(wanted)} отзывов не найдены",
            refusals=refusals,
        )

    try:
        snapshot: ORMConfigSnapshot = await ConfigService.get_effective(db)
    except ConfigError as exc:
        raise LinkAuditError(f"effective config: {exc}") from exc

    cancelled: list[uuid.UUID] = []
    already_cancelled: list[uuid.UUID] = []
    for revocation_id in wanted:
        row = found[revocation_id]
        if row.cancelled_at is not None:
            already_cancelled.append(revocation_id)
            continue
        row.cancelled_at = datetime.now(UTC)
        row.cancelled_by = actor
        row.cancel_reason = clean_reason
        await db.flush()
        cancelled.append(revocation_id)

    affected_claims = sorted(
        {found[revocation_id].claim_id for revocation_id in cancelled},
        key=lambda value: str(value),
    )
    invalidated = 0
    jobs_created = 0
    for claim_id in affected_claims:
        removed, jobs = await _revocation_cascade_claim(
            db, claim_id=claim_id, snapshot_id=snapshot.id
        )
        invalidated += removed
        jobs_created += jobs

    for revocation_id in cancelled:
        row = found[revocation_id]
        await audit.record(
            AuditEventType.LINK_REVOCATION_CANCELLED,
            actor=actor,
            payload={
                "revocation_id": str(revocation_id),
                "evidence_id": str(row.evidence_id),
                "claim_id": str(row.claim_id),
                "cancel_reason": clean_reason,
                "revocation_reason": row.reason,
                "config_snapshot_id": str(snapshot.id),
                "heads_invalidated": invalidated,
                "jobs_created": jobs_created,
            },
            public_summary=(
                f"отзыв связи {str(revocation_id)[:8]} отменён оператором: {clean_reason}"
            ),
        )

    return LinkRevocationCancelResult(
        cancelled=tuple(cancelled),
        already_cancelled=tuple(already_cancelled),
        claims_affected=tuple(affected_claims),
        heads_invalidated=invalidated,
        jobs_created=jobs_created,
    )
