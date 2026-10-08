"""Операторский спор об утверждении (T7.81, §11.3).

Спор оператора — это НЕ мнение о самом утверждении и новая оценка: спека
(§11.3) разрешает оператору исправлять **происхождение источника** («эта
страница — пересказ той») отдельной записью `source_graph_corrections` с
проверяемой provenance-цепочкой, actor и audit record. Оценку дальше
пересчитывают правила: склейка двух групп независимости убавляет их
число, `temporal_fact`/`external_fact` теряют `min_independence_groups`
и получают `insufficient_independence` → E1/hypothesis (packages/memory/
rules_engine.py). Бейдж «Проверено» уходит потому, что независимости стало
меньше, а не потому, что так решил оператор.

Чего здесь сознательно нет (стоп-критерий T7.81):

- нового значения `OperatorCommandType` и правки CHECK `operator_commands.type`
  (список materialизован миграцией 0002 из enum);
- прямого перевода головы в `pending`/`invalid` «по воле оператора»: актор
  головы — закрытый CHECK (миграция 0022), а рабочий переоценки пересчитывает
  «только из существующих evidence» (§8.6) и вернул бы прежнюю оценку на
  ближайшем тике;
- presentation-слоя, который прятал бы действующую оценку (ADR-0026).

Дисциплина: коррекция + каскад + вопросы + журнал — в одной транзакции
вызывающего кода (инвариант audit+outbox §3); grade и epistemic_status
назначает только rules engine; идемпотентность держится естественным ключом
коррекции `UNIQUE (actor, from_source_id, to_source_id, kind, rules_version)`
и точным текстом вопроса — клиентский `idempotency_key` не принимается (§20.10).
"""

from __future__ import annotations

import uuid
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sqlalchemy import or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from packages.domain.models.enums import AuditEventType
from packages.domain.models.memory import ORMSourceGraphCorrection
from packages.domain.services.audit import AuditService
from packages.domain.services.question_intake import put_operator_question
from packages.memory.independence import normalize_uri
from packages.memory.rules_engine import RULES_ENGINE_VERSION
from packages.memory.source_graph import apply_source_graph_change

#: Акторы: вход, через который оператор сделал действие. Хост называет себя
#: сам — свободный текст от клиента в акторе не участвует (§13.2).
ACTOR_WEB = "operator:web"
ACTOR_HOSTCTL = "operator:hostctl"

KIND_MERGE = "merge"

REASON_MIN_CHARS = 3
REASON_MAX_CHARS = 500
#: сколько statement'а цитирует вопрос на перепроверку (ловушка T7.73:
# лексика FTS обязана достать якорь, поэтому котировка обязательна)
STATEMENT_QUOTE_CHARS = 900

# честные коды отказа: их подписывает apps/web/labels.py (категория
# `command_refusal`), а не presentation-слой на месте.
REFUSAL_CLAIM_NOT_FOUND = "dispute_claim_not_found"
REFUSAL_HEAD_NOT_CURRENT = "dispute_claim_head_not_current"
REFUSAL_SOURCE_NOT_FOUND = "dispute_source_not_found"
REFUSAL_SAME_SOURCE = "dispute_same_source"
REFUSAL_ALREADY_GROUPED = "dispute_sources_already_grouped"
REFUSAL_REASON_INVALID = "dispute_reason_invalid"
REFUSAL_URI_INVALID = "dispute_uri_invalid"
REFUSAL_CORRECTION_NOT_FOUND = "dispute_correction_not_found"

QUESTION_TEXT_PREFIX = "Перепроверить утверждение (операторский спор):"


class DisputeError(ValueError):
    """Спор нельзя оформить существующим механизмом: хост отказывает whole-way,
    ничего не пишет и называет причину кодом (`code`)."""

    def __init__(self, code: str, *, detail: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class ClaimSource:
    """Источник улики утверждения (то, что оператор видит на странице)."""

    source_id: uuid.UUID
    canonical_uri: str | None


@dataclass(frozen=True)
class DisputePair:
    primary: ClaimSource
    retelling: ClaimSource


@dataclass(frozen=True)
class DisputeOutcome:
    claim_id: uuid.UUID
    statement: str
    correction_id: uuid.UUID
    actor: str
    reason: str
    replayed: bool
    invalidated: int
    jobs_created: int
    affected_claims: int
    source_graph_revision: int
    question_id: uuid.UUID
    question_created: bool
    primary_source_id: uuid.UUID
    retelling_source_id: uuid.UUID
    primary_uri: str | None
    retelling_uri: str | None

    @property
    def source_ids(self) -> frozenset[uuid.UUID]:
        """Затронутые источники: оба (основание merge симметрично в группировке)."""
        return frozenset({self.primary_source_id, self.retelling_source_id})


@dataclass(frozen=True)
class CancelOutcome:
    claim_id: uuid.UUID
    correction_id: uuid.UUID
    actor: str
    reason: str | None
    invalidated: int
    jobs_created: int
    affected_claims: int
    source_graph_revision: int


# ─── чистые функции (тестируются без БД) ──────────────────────────────────


def validate_dispute_reason(raw: Any) -> str:
    """Причина спора обязательна: «оспорено оператором» без причины — не спор.

    Границы те же, что у причины команды оператора (`CommandIn.reason`).
    """
    if not isinstance(raw, str):
        raise DisputeError(REFUSAL_REASON_INVALID, detail="причина должна быть строкой")
    text_value = raw.strip()
    if len(text_value) < REASON_MIN_CHARS:
        raise DisputeError(REFUSAL_REASON_INVALID, detail="причина пустая")
    if len(text_value) > REASON_MAX_CHARS:
        raise DisputeError(
            REFUSAL_REASON_INVALID,
            detail=f"причина длиннее {REASON_MAX_CHARS} знаков",
        )
    return text_value


def normalize_dispute_uri(raw: Any) -> str:
    """Канонический вид адреса для сравнения с `sources.canonical_uri`.

    То же нормирование, что у группировки источников (`normalize_uri`), поэтому
    оператор может вставить адрес как его видит: со query-строкой, хвостовым
    слэшем или без схемы (тогда подразумевается https — адрес источника всегда
    хранится со схемой).
    """
    if not isinstance(raw, str):
        raise DisputeError(REFUSAL_URI_INVALID, detail="адрес должен быть строкой")
    candidate = raw.strip()
    if not candidate:
        raise DisputeError(REFUSAL_URI_INVALID, detail="адрес пустой")
    if "://" not in candidate:
        candidate = f"https://{candidate}"
    normalized = normalize_uri(candidate)
    if not normalized.split("://", 1)[1]:
        raise DisputeError(REFUSAL_URI_INVALID, detail="в адресе нет хоста")
    return normalized


def resolve_dispute_pair(
    sources: Sequence[ClaimSource],
    *,
    primary_uri: str,
    retelling_uri: str,
) -> DisputePair:
    """Пара «первоисточник ← пересказ» среди улик именно этого утверждения.

    Fail-closed: адрес, которого нет среди источников claim'а, не разрешается
    («оператор спорит про эту страницу», а не про произвольный источник).
    Совпадение — по хосту, пути и query (схема http/https одной страницы не
    важна); несколько источников с одним адресом — отказ, пока оператор не
    назвал их разными адресами.
    """

    def _one(uri: str, role: str) -> ClaimSource:
        matches = [s for s in sources if _uri_matches(s.canonical_uri, uri)]
        if not matches:
            raise DisputeError(REFUSAL_SOURCE_NOT_FOUND, detail=f"{role}: {uri}")
        if len(matches) > 1:
            raise DisputeError(REFUSAL_SOURCE_NOT_FOUND, detail=f"{role}: {uri} (неоднозначно)")
        return matches[0]

    primary = _one(primary_uri, "primary")
    retelling = _one(retelling_uri, "retelling")
    if primary.source_id == retelling.source_id:
        raise DisputeError(REFUSAL_SAME_SOURCE)
    return DisputePair(primary=primary, retelling=retelling)


def recheck_question_text(statement: str) -> str:
    """Текст вопроса на перепроверку: цитата statement'а якоря (T7.73).

    Детерминированный по утверждению: повтор спора даёт тот же вопрос
    (`put_operator_question` идемпотентен по точному тексту).
    """
    quote = " ".join((statement or "").split())[:STATEMENT_QUOTE_CHARS]
    return f"{QUESTION_TEXT_PREFIX} {quote}"


def _uri_matches(stored: str | None, wanted: str) -> bool:
    """Совпадение адреса с `sources.canonical_uri`.

    Схема не важна (http и https одной страницы — один источник), важны хост,
    путь и query: именно по ним оператор различает первоисточник и пересказ.
    """
    if not stored:
        return False
    return _uri_identity(stored.strip()) == _uri_identity(wanted)


def _uri_identity(uri: str) -> str:
    normalized = normalize_uri(uri) if "://" in uri else normalize_dispute_uri(uri)
    return normalized.split("://", 1)[-1]


# ─── рабочий контур (хост — доверенный писатель графа) ─────────────────────


async def _claim_row(db: AsyncSession, claim_id: uuid.UUID) -> Mapping[str, Any]:
    row = (
        (
            await db.execute(
                text("SELECT id, statement FROM claims WHERE id = :id FOR UPDATE"),
                {"id": claim_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        raise DisputeError(REFUSAL_CLAIM_NOT_FOUND)
    return dict(row)


async def _head_state(db: AsyncSession, claim_id: uuid.UUID) -> tuple[str | None, uuid.UUID | None]:
    """Состояние головы на effective-снапшоте + id действующей оценки."""
    row = (
        (
            await db.execute(
                text(
                    """
                    SELECT h.assessment_state, h.current_assessment_id
                    FROM claim_assessment_heads h
                    WHERE h.claim_id = :c
                      AND h.config_snapshot_id =
                          (SELECT active_config_snapshot_id FROM runtime_config_heads
                            WHERE scope = 'global')
                    """
                ),
                {"c": claim_id},
            )
        )
        .mappings()
        .first()
    )
    if row is None:
        return None, None
    assessment_id = row["current_assessment_id"]
    if assessment_id is not None and not isinstance(assessment_id, uuid.UUID):
        assessment_id = uuid.UUID(str(assessment_id))
    return row["assessment_state"], assessment_id


async def _claim_sources(db: AsyncSession, claim_id: uuid.UUID) -> list[ClaimSource]:
    rows = (
        (
            await db.execute(
                text(
                    """
                    SELECT DISTINCT s.id AS source_id, s.canonical_uri
                    FROM evidence e
                    JOIN sources s ON s.id = e.source_id
                    WHERE e.claim_id = :c
                    ORDER BY s.id
                    """
                ),
                {"c": claim_id},
            )
        )
        .mappings()
        .all()
    )
    return [ClaimSource(source_id=r["source_id"], canonical_uri=r["canonical_uri"]) for r in rows]


async def _basis_artifact_id(db: AsyncSession, claim_id: uuid.UUID, source_id: uuid.UUID) -> uuid.UUID | None:
    """Артефакт прочитанной страницы пересказа — проверяемое основание коррекции.

    Берётся из улики этого утверждения (`evidence.observation_artifact_id`):
    оператор ссылается на тот же артефакт, который уже лежит в истории знания.
    """
    row = (
        (
            await db.execute(
                text(
                    "SELECT observation_artifact_id FROM evidence "
                    "WHERE claim_id = :c AND source_id = :s "
                    "  AND observation_artifact_id IS NOT NULL "
                    "ORDER BY created_at, id LIMIT 1"
                ),
                {"c": claim_id, "s": source_id},
            )
        )
        .scalar()
    )
    if row is None:
        return None
    return row if isinstance(row, uuid.UUID) else uuid.UUID(str(row))


async def _group_ids(
    db: AsyncSession, assessment_id: uuid.UUID | None, source_ids: Sequence[uuid.UUID]
) -> dict[uuid.UUID, str]:
    """Группы независимости, зафиксированные действующей оценкой (если есть)."""
    if assessment_id is None or not source_ids:
        return {}
    snapshot_id = (
        await db.execute(
            text("SELECT source_independence_snapshot_id FROM claim_assessments WHERE id = :a"),
            {"a": assessment_id},
        )
    ).scalar_one_or_none()
    if snapshot_id is None:
        return {}
    rows = (
        (
            await db.execute(
                text(
                    "SELECT source_id, group_id FROM source_independence_members "
                    "WHERE snapshot_id = :s AND source_id = ANY(:ids)"
                ),
                {"s": snapshot_id, "ids": list(source_ids)},
            )
        )
        .all()
    )
    out: dict[uuid.UUID, str] = {}
    for source_id, group_id in rows:
        key = source_id if isinstance(source_id, uuid.UUID) else uuid.UUID(str(source_id))
        out[key] = str(group_id)
    return out


async def _find_correction(
    db: AsyncSession, *, actor: str, primary_id: uuid.UUID, retelling_id: uuid.UUID
) -> ORMSourceGraphCorrection | None:
    """Естественный ключ коррекции — тот же UNIQUE, что в таблице (§13.2)."""
    return (
        (
            await db.execute(
                select(ORMSourceGraphCorrection)
                .where(
                    ORMSourceGraphCorrection.actor == actor,
                    ORMSourceGraphCorrection.kind == KIND_MERGE,
                    ORMSourceGraphCorrection.rules_version == RULES_ENGINE_VERSION,
                    ORMSourceGraphCorrection.from_source_id == retelling_id,
                    ORMSourceGraphCorrection.to_source_id == primary_id,
                )
                .limit(1)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )


async def _active_job_count(db: AsyncSession, claim_id: uuid.UUID) -> int:
    return int(
        (
            await db.execute(
                text(
                    "SELECT count(*) FROM reassessment_jobs "
                    "WHERE claim_id = :c AND status IN ('queued','leased','retry')"
                ),
                {"c": claim_id},
            )
        ).scalar_one()
    )


async def dispute_claim(
    db: AsyncSession,
    audit: AuditService,
    *,
    claim_id: uuid.UUID,
    primary_uri: str,
    retelling_uri: str,
    reason: str,
    actor: str = ACTOR_WEB,
) -> DisputeOutcome:
    """Оспорить действующее утверждение фактом о происхождении источника.

    Делает ровно то, что спека отвела для исправления ложного объединения
    (§11.3): строку `source_graph_corrections(kind='merge')` с актором,
    артефактом-основанием и ссылкой на событие журнала с причиной; затем
    существующий каскад `apply_source_graph_change` (головы → pending +
    долговременные задачи пересчёта) и вопрос на перепроверку, цитирующий
    statement. Оценку не трогает: её пересчитает рабочий переоценки.

    Идемпотентность — естественный ключ коррекции и точный текст вопроса:
    повтор возвращает прежнюю коррекцию (`replayed=True`) и не заводит
    ни второй строка графа, ни второго вопроса. Транзакцию владеет вызывающий.
    """
    reason_text = validate_dispute_reason(reason)
    primary_wanted = normalize_dispute_uri(primary_uri)
    retelling_wanted = normalize_dispute_uri(retelling_uri)

    claim = await _claim_row(db, claim_id)
    sources = await _claim_sources(db, claim_id)
    pair = resolve_dispute_pair(sources, primary_uri=primary_wanted, retelling_uri=retelling_wanted)

    received = await audit.record(
        AuditEventType.OPERATOR_COMMAND_RECEIVED,
        actor=actor,
        payload={
            "action": "claim_dispute",
            "claim_id": str(claim_id),
            "kind": KIND_MERGE,
            "primary_uri": primary_wanted,
            "retelling_uri": retelling_wanted,
            "operator_reason": reason_text,
        },
        public_summary=f"operator dispute for claim {claim_id}: {reason_text[:120]}",
    )

    existing = await _find_correction(
        db, actor=actor, primary_id=pair.primary.source_id, retelling_id=pair.retelling.source_id
    )
    if existing is not None and existing.valid:
        # повтор: граф уже исправлен этим же оператором — ничего не меняем,
        # но перепроверку гарантируем (тот же детерминированный текст вопроса)
        question, created = await put_operator_question(
            db, raw_text=recheck_question_text(str(claim["statement"])), raw_priority=0
        )
        await audit.record(
            AuditEventType.OPERATOR_COMMAND_COMPLETED,
            actor=actor,
            payload={
                "action": "claim_dispute",
                "claim_id": str(claim_id),
                "correction_id": str(existing.id),
                "replayed": True,
                "jobs_active": await _active_job_count(db, claim_id),
                "question_id": str(question.id),
                "question_created": created,
            },
            public_summary=f"operator dispute replayed for claim {claim_id}",
        )
        return DisputeOutcome(
            claim_id=claim_id,
            statement=str(claim["statement"]),
            correction_id=existing.id,
            actor=actor,
            reason=reason_text,
            replayed=True,
            invalidated=0,
            jobs_created=0,
            affected_claims=0,
            source_graph_revision=0,
            question_id=question.id,
            question_created=created,
            primary_source_id=pair.primary.source_id,
            retelling_source_id=pair.retelling.source_id,
            primary_uri=pair.primary.canonical_uri,
            retelling_uri=pair.retelling.canonical_uri,
        )

    state, assessment_id = await _head_state(db, claim_id)
    if state != "current":
        raise DisputeError(
            REFUSAL_HEAD_NOT_CURRENT,
            detail=f"голова утверждения: {state or 'нет головы на активном снапшоте'}",
        )

    groups = await _group_ids(
        db, assessment_id, (pair.primary.source_id, pair.retelling.source_id)
    )
    if len(groups) == 2 and groups[pair.primary.source_id] == groups[pair.retelling.source_id]:
        raise DisputeError(REFUSAL_ALREADY_GROUPED)

    if existing is not None:
        # коррекция этого же оператора по этой же паре уже была и её снимали:
        # строка оживает (history не переписывается), а не заводится вторая
        existing.valid = True
        existing.reason_audit_event_id = received.id
        correction = existing
    else:
        correction = ORMSourceGraphCorrection(
            id=uuid.uuid4(),
            actor=actor,
            kind=KIND_MERGE,
            from_source_id=pair.retelling.source_id,
            to_source_id=pair.primary.source_id,
            basis_artifact_id=await _basis_artifact_id(db, claim_id, pair.retelling.source_id),
            rules_version=RULES_ENGINE_VERSION,
            valid=True,
            reason_audit_event_id=received.id,
        )
        db.add(correction)
    await db.flush()

    cascade = await apply_source_graph_change(
        db, audit, source_ids=frozenset({pair.primary.source_id, pair.retelling.source_id}), actor=actor
    )

    question, created = await put_operator_question(
        db, raw_text=recheck_question_text(str(claim["statement"])), raw_priority=0
    )

    await audit.record(
        AuditEventType.OPERATOR_COMMAND_COMPLETED,
        actor=actor,
        payload={
            "action": "claim_dispute",
            "claim_id": str(claim_id),
            "correction_id": str(correction.id),
            "kind": KIND_MERGE,
            "from_source_id": str(pair.retelling.source_id),
            "to_source_id": str(pair.primary.source_id),
            "operator_reason": reason_text,
            "invalidated": cascade.invalidated,
            "jobs_created": cascade.jobs_created,
            "affected_claims": cascade.affected_claims,
            "source_graph_revision": cascade.revision,
            "question_id": str(question.id),
            "question_created": created,
        },
        public_summary=(
            f"operator dispute of claim {claim_id}: {cascade.invalidated} assessment(s) requeued"
        ),
    )

    return DisputeOutcome(
        claim_id=claim_id,
        statement=str(claim["statement"]),
        correction_id=correction.id,
        actor=actor,
        reason=reason_text,
        replayed=False,
        invalidated=cascade.invalidated,
        jobs_created=cascade.jobs_created,
        affected_claims=cascade.affected_claims,
        source_graph_revision=cascade.revision,
        question_id=question.id,
        question_created=created,
        primary_source_id=pair.primary.source_id,
        retelling_source_id=pair.retelling.source_id,
        primary_uri=pair.primary.canonical_uri,
        retelling_uri=pair.retelling.canonical_uri,
    )


async def _valid_correction_for_claim(
    db: AsyncSession, claim_id: uuid.UUID
) -> ORMSourceGraphCorrection | None:
    """Последняя действующая коррекция склейки, затрагивающая источники claim'а.

    Строка берётся ORM-запросом и блокируется: `valid` меняет именно она,
    а не копия значений из raw-SQL выборки.
    """
    source_ids = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT source_id FROM evidence "
                    "WHERE claim_id = :c AND source_id IS NOT NULL"
                ),
                {"c": claim_id},
            )
        )
        .scalars()
        .all()
    )
    if not source_ids:
        return None
    return (
        (
            await db.execute(
                select(ORMSourceGraphCorrection)
                .where(
                    ORMSourceGraphCorrection.kind == KIND_MERGE,
                    ORMSourceGraphCorrection.valid.is_(True),
                    or_(
                        ORMSourceGraphCorrection.from_source_id.in_(source_ids),
                        ORMSourceGraphCorrection.to_source_id.in_(source_ids),
                    ),
                )
                .order_by(
                    ORMSourceGraphCorrection.created_at.desc(),
                    ORMSourceGraphCorrection.id.desc(),
                )
                .limit(1)
                .with_for_update()
            )
        )
        .scalars()
        .first()
    )


async def cancel_dispute(
    db: AsyncSession,
    audit: AuditService,
    *,
    claim_id: uuid.UUID,
    reason: str | None = None,
    actor: str = ACTOR_WEB,
) -> CancelOutcome:
    """Отменить операторский спор тем же механизмом, которым он был сделан.

    Строка коррекции не удаляется (знание и прежняя оценка остаются в
    истории): `valid = false` + повторный каскад §11.3. Дальше правила
    пересчитывают независимость без склейки — прежний grade возвращается,
    если для него снова достаточно групп. Отмена записана в журнале вместе
    с тем, кто её сделал (§14).
    """
    reason_text: str | None = None
    if reason is not None:
        reason_text = validate_dispute_reason(reason)

    await _claim_row(db, claim_id)  # утверждение обязано существовать
    received = await audit.record(
        AuditEventType.OPERATOR_COMMAND_RECEIVED,
        actor=actor,
        payload={
            "action": "claim_dispute_cancel",
            "claim_id": str(claim_id),
            "operator_reason": reason_text,
        },
        public_summary=f"operator dispute cancel requested for claim {claim_id}",
    )

    correction = await _valid_correction_for_claim(db, claim_id)
    if correction is None:
        raise DisputeError(REFUSAL_CORRECTION_NOT_FOUND)

    correction.valid = False
    await db.flush()

    cascade = await apply_source_graph_change(
        db,
        audit,
        source_ids=frozenset({correction.from_source_id, correction.to_source_id}),
        actor=actor,
    )
    received.payload = {
        **dict(received.payload or {}),
        "correction_id": str(correction.id),
        "cancelled_by": actor,
    }
    await audit.record(
        AuditEventType.OPERATOR_COMMAND_COMPLETED,
        actor=actor,
        payload={
            "action": "claim_dispute_cancel",
            "claim_id": str(claim_id),
            "correction_id": str(correction.id),
            "kind": KIND_MERGE,
            "operator_reason": reason_text,
            "invalidated": cascade.invalidated,
            "jobs_created": cascade.jobs_created,
            "affected_claims": cascade.affected_claims,
            "source_graph_revision": cascade.revision,
        },
        public_summary=(
            f"operator dispute cancelled for claim {claim_id}: "
            f"{cascade.invalidated} assessment(s) requeued"
        ),
    )

    return CancelOutcome(
        claim_id=claim_id,
        correction_id=correction.id,
        actor=actor,
        reason=reason_text,
        invalidated=cascade.invalidated,
        jobs_created=cascade.jobs_created,
        affected_claims=cascade.affected_claims,
        source_graph_revision=cascade.revision,
    )


async def list_claim_disputes(db: AsyncSession, claim_id: uuid.UUID) -> list[dict[str, Any]]:
    """Читающая модель для витрины: коррекции склейки по источникам утверждения.

    Никаких текстовых эвристик (запрет T7.74): причина читается из payload'а
    события журнала, на которое указывает `reason_audit_event_id`.
    """
    rows = (
        (
            await db.execute(
                text(
                    """
                    SELECT c.id, c.kind, c.actor, c.valid, c.rules_version, c.created_at,
                           f.canonical_uri AS from_uri, t.canonical_uri AS to_uri,
                           ae.payload ->> 'operator_reason' AS reason,
                           ae.actor AS audit_actor, ae.occurred_at AS audit_occurred_at,
                           cw.actor AS withdrawn_by, cw.occurred_at AS withdrawn_at,
                           cw.payload ->> 'operator_reason' AS withdraw_reason
                    FROM source_graph_corrections c
                    JOIN sources f ON f.id = c.from_source_id
                    JOIN sources t ON t.id = c.to_source_id
                    LEFT JOIN audit_events ae ON ae.id = c.reason_audit_event_id
                    LEFT JOIN LATERAL (
                        SELECT ae2.actor, ae2.occurred_at, ae2.payload
                        FROM audit_events ae2
                        WHERE ae2.type = 'operator_command_completed'
                          AND ae2.payload ->> 'action' = 'claim_dispute_cancel'
                          AND ae2.payload ->> 'correction_id' = c.id::text
                        ORDER BY ae2.sequence DESC
                        LIMIT 1
                    ) cw ON true
                    WHERE c.kind = :k
                      AND (c.from_source_id IN (SELECT source_id FROM evidence
                                                 WHERE claim_id = :c AND source_id IS NOT NULL)
                        OR c.to_source_id IN (SELECT source_id FROM evidence
                                                 WHERE claim_id = :c AND source_id IS NOT NULL))
                    ORDER BY c.valid DESC, c.created_at DESC, c.id DESC
                    """
                ),
                {"k": KIND_MERGE, "c": claim_id},
            )
        )
        .mappings()
        .all()
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        occurred = r["audit_occurred_at"]
        withdrawn_at = r["withdrawn_at"]
        out.append(
            {
                "correction_id": str(r["id"]),
                "kind": r["kind"],
                "actor": r["actor"],
                "valid": bool(r["valid"]),
                "rules_version": r["rules_version"],
                "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                "retelling_uri": r["from_uri"],
                "primary_uri": r["to_uri"],
                "reason": r["reason"],
                "audit_actor": r["audit_actor"],
                "audit_occurred_at": occurred.isoformat() if occurred is not None else None,
                "withdrawn_by": r["withdrawn_by"],
                "withdrawn_at": withdrawn_at.isoformat() if withdrawn_at is not None else None,
                "withdraw_reason": r["withdraw_reason"],
            }
        )
    return out
