"""Read-only предпросмотр смены групп независимости (T7.117 часть б, ADR-0029 §22).

Приоритет записи значения над страничным указателем во ВХОДЕ группировщика меняет
группы независимости у УЖЕ записанных утверждений — но ничего не меняет сам: прежние
оценки зафиксированы в неизменяемых снимках (`claim_assessments.source_independence_snapshot_id`).
Этот модуль отвечает честно на два вопроса:

1. «что изменится при переоценке?» — для каждого утверждения с действующей головой
   effective-снапшота сравнивает ЗАПИСАННЫЕ группы (участники снимка независимости головы)
   с группами, которые соберёт новое правило (тот же чистый `group_source_graph` через
   `load_source_graph_assembly` + `grouping_arguments`, тот же каскад-вход, что у рабочего
   переоценки), и пересчитывает оценку правил тем же чистым `evaluate` на новых группах —
   БЕЗ ЕДИНОЙ ЗАПИСИ в БД;
2. «как применить?» — `--apply` использует существующий каскад `apply_source_graph_change`
   (тот же, что у переатрибуции T7.98/T7.116 и коррекций оператора ADR-0031): головы
   затронутых утверждений становятся pending, создаются задачи переоценки, рабочий
   пересобирает снимки и оценки уже по новому правилу. Нового AuditEventType, миграций и
   собственных правок таблиц нет.

Оговорка о широте каскада (как у переатрибуции): invalidate накрывает ВСЕ утверждения, чьи
улики стоят на затронутых источниках, а не только перечисленные предпросмотром — итог для
несовпадающих утверждений не меняется (переоценка детерминирована), дубликатов задач не
возникает (частичный уникальный индекс на claim_id в очереди).

Идемпотентность: после применения и обработки задач повторный предпросмотр пуст — записанный
снимок совпадает с пересчётом по тому же правилу.

Алгоритм группировки, пороги, правила и пороги оценок здесь НЕ определяются: модуль только
вызывает существующие чистые функции и читает существующие строки.
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.config import ORMConfigSnapshot, ORMRuntimeConfigHead
from packages.domain.models.memory import (
    ORMClaim,
    ORMClaimAssessment,
    ORMClaimAssessmentHead,
    ORMCounterevidenceResolution,
    ORMEnvironmentIndependenceMember,
    ORMEvidence,
    ORMSource,
    ORMSourceIndependenceMember,
)
from packages.domain.services.audit import AuditService
from packages.memory.env_independence import UNTRACKED_GROUP
from packages.memory.independence import group_source_graph
from packages.memory.rules_engine import EvaluatedEvidence, evaluate
from packages.memory.service import MemoryService
from packages.memory.source_graph import (
    SourceGraphChangeResult,
    apply_source_graph_change,
    claim_evidence_for_assessment,
    grouping_arguments,
    load_source_graph_assembly,
)

#: актёр каскада: существующий `apply_source_graph_change` не ограничивает актёра
#: закрытым перечислением (долговременная запись — сам аудит-событий строка)
ACTOR_PREVIEW = "system:independence-preview"

_NOT_COMPUTED_EVALUATION = "оценка правил предпросмотром не пересчитана"


class IndependencePreviewError(RuntimeError):
    """Отказ предпросмотра: нет effective-снапшота (fail-closed, §3) или нет головы оценки."""


@dataclass(frozen=True)
class ParentSwitch:
    """Улика, у которой входной родитель меняется: страничный указатель → запись значения."""

    evidence_uri: str
    pointer_parent_uri: str | None
    record_parent_uri: str


@dataclass(frozen=True)
class IndependencePreviewRow:
    claim_id: uuid.UUID
    claim_type: str
    statement: str
    groups_changed: bool
    #: partitions by group: member canonical uris, stable order (old = persisted snapshot)
    old_groups: tuple[tuple[str, ...], ...]
    new_groups: tuple[tuple[str, ...], ...]
    parent_switches: tuple[ParentSwitch, ...]
    evaluation_computed: bool
    evaluation_note: str | None
    #: записанная оценка головы («было»)
    persisted_grade: str
    persisted_status: str
    persisted_confidence: float
    #: пересчёт чистым `evaluate` на новых группах («стало»); None, если не пересчитана
    new_grade: str | None
    new_status: str | None
    new_confidence: float | None

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
class IndependencePreviewReport:
    config_snapshot_id: uuid.UUID | None
    claims_checked: int
    rows: tuple[IndependencePreviewRow, ...]

    @property
    def groups_changed_count(self) -> int:
        return sum(1 for r in self.rows if r.groups_changed)

    @property
    def assessment_changed_count(self) -> int:
        return sum(1 for r in self.rows if r.assessment_changed)


async def _effective_snapshot_id(db: AsyncSession) -> uuid.UUID | None:
    head = (
        (await db.execute(select(ORMRuntimeConfigHead).where(ORMRuntimeConfigHead.scope == "global")))
        .scalars()
        .first()
    )
    if head is None:
        return None
    return head.active_config_snapshot_id


async def _latest_assessed_scope(db: AsyncSession, claim_id: uuid.UUID) -> dict[str, Any]:
    """Тот же источник области оценки, что у рабочего переоценки (scope носят оценки)."""
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


def _uri_of(source_id: uuid.UUID, uris: Mapping[uuid.UUID, str | None]) -> str:
    uri = uris.get(source_id)
    return uri if uri else f"источник {str(source_id)[:8]}"


def _partition(
    groups: Mapping[uuid.UUID, str], uris: Mapping[uuid.UUID, str | None]
) -> tuple[tuple[str, ...], ...]:
    """Partition of members by group name, stable order (group names are the snapshot's)."""
    by_group: dict[str, list[str]] = {}
    for sid, gid in groups.items():
        by_group.setdefault(gid, []).append(_uri_of(sid, uris))
    return tuple(
        tuple(sorted(items)) for _, items in sorted(by_group.items(), key=lambda kv: (kv[0], kv[1]))
    )


async def preview_independence_changes(db: AsyncSession) -> IndependencePreviewReport:
    """Read-only сравнение записанных групп/оценок с тем, что даст новое правило T7.117."""
    snapshot_id = await _effective_snapshot_id(db)
    if snapshot_id is None:
        raise IndependencePreviewError(
            "нет effective config snapshot в runtime_config_heads (fail-closed, §3)"
        )
    snapshot = await db.get(ORMConfigSnapshot, snapshot_id)
    if snapshot is None:
        raise IndependencePreviewError(f"snapshot {snapshot_id} отсутствует в config_snapshots")

    heads = (
        (
            await db.execute(
                select(ORMClaimAssessmentHead)
                .where(
                    ORMClaimAssessmentHead.config_snapshot_id == snapshot_id,
                    ORMClaimAssessmentHead.assessment_state == "current",
                )
                .order_by(ORMClaimAssessmentHead.claim_id)
            )
        )
        .scalars()
        .all()
    )

    service = MemoryService(snapshot)
    rows: list[IndependencePreviewRow] = []
    claims_checked = 0
    for head in heads:
        claim_id = head.claim_id
        if head.current_assessment_id is None:
            continue  # head 'current' без оценки невозможен по инварианту §3 — защищаемся честно
        assessment = await db.get(ORMClaimAssessment, head.current_assessment_id)
        claim = await db.get(ORMClaim, claim_id)
        if assessment is None or claim is None:
            continue
        claims_checked += 1

        assembly = await load_source_graph_assembly(db, claim_id=claim_id)
        if assembly is None:
            # утверждение без источных улик: группировка источников к нему не применяется
            continue

        # «было» — участники записанного снимка независимости головы
        old_members: dict[uuid.UUID, str] = {}
        if assessment.source_independence_snapshot_id is not None:
            for sm in (
                (
                    await db.execute(
                        select(ORMSourceIndependenceMember).where(
                            ORMSourceIndependenceMember.snapshot_id
                            == assessment.source_independence_snapshot_id
                        )
                    )
                )
                .scalars()
                .all()
            ):
                old_members[sm.source_id] = sm.group_id

        # «стало» — тот же чистый группировщик, что у рабочего переоценки, но по новому правилу
        inputs, edges, corrections = grouping_arguments(assembly)
        result = group_source_graph(inputs, edges, corrections)
        new_members = {sid: result.groups[sid] for sid in assembly.member_ids}

        uris = {n.source_id: n.canonical_uri for n in assembly.nodes}
        if old_members:
            missing = [sid for sid in old_members if sid not in uris]
            if missing:
                extra = (
                    (await db.execute(select(ORMSource).where(ORMSource.id.in_(missing))))
                    .scalars()
                    .all()
                )
                uris.update({s.id: s.canonical_uri for s in extra})

        old_partition = _partition(old_members, uris)
        new_partition = _partition(new_members, uris)
        groups_changed = old_partition != new_partition

        switches: list[ParentSwitch] = []
        for node in assembly.nodes:
            if node.source_id not in assembly.member_ids:
                continue
            recorded = assembly.effective_parent.get(node.source_id)
            if recorded is not None and recorded != node.pointer_parent:
                switches.append(
                    ParentSwitch(
                        evidence_uri=_uri_of(node.source_id, uris),
                        pointer_parent_uri=(
                            _uri_of(node.pointer_parent, uris) if node.pointer_parent else None
                        ),
                        record_parent_uri=_uri_of(recorded, uris),
                    )
                )

        # оценка «стало»: чистый evaluate на новых группах; env-группы — из записанного
        # снимка независимости окружения головы (алгоритм окружения T7.117 не тронут)
        new_grade = new_status = None
        new_confidence: float | None = None
        note: str | None = None
        try:
            rule = service.rule(claim.claim_type)
            # T7.126b (дополнение ADR-0039): ВХОД оценки «стало» — из общей точки
            # `claim_evidence_for_assessment`, то есть улики утверждения БЕЗ действующего
            # отзыва связи. Прежняя сборка брала ВСЕ улики: отозванная связь не входит в
            # группы (её источник исключён из входа группировщика), поэтому получала вторую
            # «независимую» группу `UNTRACKED_GROUP` и поднимала оценку — предпросмотр и
            # `--apply` записывали бы ложное «Проверено» (стендовый случай 83e7a1c8:
            # E1 hypothesis 0.15 → E3 supported 0.75).
            evidence_rows = await claim_evidence_for_assessment(db, claim_id=claim_id)
            env_groups: dict[uuid.UUID, str] = {}
            env_relations: dict[uuid.UUID, str] = {}
            if assessment.environment_independence_snapshot_id is not None:
                for em in (
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
                    env_groups[em.environment_manifest_id] = em.group_id
                    env_relations[em.environment_manifest_id] = em.relation
            resolved_ids = {
                r.evidence_id
                for r in (
                    await db.execute(
                        select(ORMCounterevidenceResolution).where(
                            ORMCounterevidenceResolution.valid,
                            ORMCounterevidenceResolution.evidence_id.in_(
                                [e.id for e in evidence_rows]
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            }
            evs = [
                EvaluatedEvidence(
                    identity_hash=e.identity_hash,
                    kind=e.evidence_kind,
                    relation=e.relation,
                    scope=dict(e.scope or {}),
                    independence_group=(
                        new_members[e.source_id]
                        if e.source_id is not None and e.source_id in new_members
                        else (
                            env_groups.get(e.environment_manifest_id, UNTRACKED_GROUP)
                            if e.environment_manifest_id is not None
                            else UNTRACKED_GROUP
                        )
                    ),
                    env_relation=(
                        env_relations.get(e.environment_manifest_id, "none")
                        if e.environment_manifest_id is not None
                        else "none"
                    ),
                    resolved=e.id in resolved_ids,
                )
                for e in evidence_rows
            ]
            outcome = evaluate(
                claim.claim_type,
                rule,
                claim_scope=await _latest_assessed_scope(db, claim_id),
                evidences=evs,
                has_as_of=claim.as_of is not None,
            )
            new_grade, new_status, new_confidence = (
                outcome.grade,
                outcome.epistemic_status,
                float(outcome.confidence),
            )
        except ValueError as exc:  # RuleValidationError и отказ подбора правила
            note = f"{_NOT_COMPUTED_EVALUATION}: {exc}"

        if groups_changed or (new_grade is not None and (
            new_grade != assessment.effective_grade
            or new_status != assessment.epistemic_status
            or abs(float(new_confidence or 0.0) - float(assessment.confidence)) > 1e-9
        )):
            rows.append(
                IndependencePreviewRow(
                    claim_id=claim_id,
                    claim_type=claim.claim_type,
                    statement=claim.statement,
                    groups_changed=groups_changed,
                    old_groups=old_partition,
                    new_groups=new_partition,
                    parent_switches=tuple(switches),
                    evaluation_computed=new_grade is not None,
                    evaluation_note=note,
                    persisted_grade=assessment.effective_grade,
                    persisted_status=str(assessment.epistemic_status),
                    persisted_confidence=float(assessment.confidence),
                    new_grade=new_grade,
                    new_status=new_status,
                    new_confidence=new_confidence,
                )
            )

    return IndependencePreviewReport(
        config_snapshot_id=snapshot_id,
        claims_checked=claims_checked,
        rows=tuple(rows),
    )


async def apply_independence_preview(
    db: AsyncSession,
    audit: AuditService,
    *,
    rows: Sequence[IndependencePreviewRow],
) -> SourceGraphChangeResult:
    """Применение существующим каскадом §11.3 (`apply_source_graph_change` — тот же путь,
    что у переатрибуции и коррекций оператора): объединение источников улик затронутых
    утверждений уходит в каскад, головы становятся pending, очередь пополняется задачами
    переоценки. Рабочий (`run_reassessment_batch`) вызывается вызывающей стороной отдельно."""
    claim_ids = [row.claim_id for row in rows]
    if not claim_ids:
        return SourceGraphChangeResult(0, 0, 0, 0)
    source_ids = {
        row[0]
        for row in (
            await db.execute(
                select(ORMEvidence.source_id)
                .where(ORMEvidence.claim_id.in_(claim_ids), ORMEvidence.source_id.is_not(None))
            )
        ).all()
        if row[0] is not None
    }
    return await apply_source_graph_change(
        db, audit, source_ids=frozenset(source_ids), actor=ACTOR_PREVIEW
    )
