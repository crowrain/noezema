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

T7.114 (дополнение ADR-0031, решение пользователя): кандидатами на роль ПЕРВОИСТОЧНИКА
в паре спора, кроме источников улик утверждения, являются также источники-производители,
к которым хост УЖЕ приписал улики этого утверждения: запись значения
`evidence.scope.value_attribution.parent_source_id` (любая версия метода) и страничный
указатель `sources.parent_source_id` источников улик. Это не произвольная страница
интернета: факт производности от неё уже записан хостом в истории знания. Роль ПЕРЕСКАЗА
остаётся прежней — только источник собственной улики утверждения.

Чего здесь сознательно нет (стоп-критерий T7.81):

- нового значения `OperatorCommandType` и правки CHECK `operator_commands.type`
  (список материализован миграцией 0002 из enum);
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
from packages.memory.scope import VALUE_ATTRIBUTION_SCHEMA
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
    """Роли спора, названные адресом (T7.129).

    Адресу оператора может соответствовать НЕСКОЛЬКО строк `sources`: одна и та же
    страница, прочитанная узлом несколько раз (разные `content_hash` — разные версии
    содержимого, у каждой версии своя улика). Это ОДНА страница, а не двусмысленность:
    роль держит кортеж строк, отсортированный по id (детерминированно), а коррекция
    заводится на каждую строку — иначе оспоренной оказалась бы только одна версия.
    """

    primary_rows: tuple[ClaimSource, ...]
    retelling_rows: tuple[ClaimSource, ...]

    @property
    def primary(self) -> ClaimSource:
        """Представитель первоисточника — первая по id строка названного адреса."""
        return self.primary_rows[0]

    @property
    def retelling(self) -> ClaimSource:
        """Представитель пересказа — первая по id строка названного адреса."""
        return self.retelling_rows[0]

    @property
    def merge_pairs(self) -> tuple[tuple[ClaimSource, ClaimSource], ...]:
        """Пары «пересказ → первоисточник» для коррекций: каждая строка пересказа к
        каждой строке первоисточника (обе группы — одна страница, склейка симметрична)."""
        return tuple(
            (retelling, primary)
            for retelling in self.retelling_rows
            for primary in self.primary_rows
        )

    @property
    def source_ids(self) -> frozenset[uuid.UUID]:
        return frozenset(s.source_id for s in (*self.primary_rows, *self.retelling_rows))


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
    #: T7.129: все коррекции спора (одна на каждую версию оспоренной страницы) и все
    #: затронутые строки пересказа. `correction_id`/`retelling_source_id` — представитель
    #: (первая по id строка): прежние читатели исхода (web, hostctl) не изменены.
    correction_ids: tuple[uuid.UUID, ...] = ()
    retelling_source_ids: frozenset[uuid.UUID] = frozenset()

    @property
    def source_ids(self) -> frozenset[uuid.UUID]:
        """Затронутые источники: оба (основание merge симметрично в группировке)."""
        return frozenset({self.primary_source_id, self.retelling_source_id}) | self.retelling_source_ids


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
    #: T7.129: все снятые коррекции спора (спор по адресу их заводит несколько — по одной
    #: на каждую версию страницы). `correction_id` — представитель, прежнее поле.
    correction_ids: tuple[uuid.UUID, ...] = ()


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
    producer_sources: Sequence[ClaimSource] = (),
) -> DisputePair:
    """Пара «первоисточник ← пересказ» (T7.81; границы первоисточника расширены T7.114).

    Fail-closed по ролям. ПЕРЕСКАЗ: адрес обязан стоять среди источников улик именно
    этого утверждения («оператор спорит про эту страницу», а не про произвольный
    источник). ПЕРВОИСТОЧНИК: источник улики ИЛИ источник-производитель, которому хост
    уже приписал улики этого утверждения (`claim_producer_sources`): оператор называет
    факт, уже записанный в истории знания, а не своё мнение о чужом сайте.

    Совпадение — по хосту, пути и query (схема http/https одной страницы не важна).
    Один и тот же источник, встреченный и как улика, и как производитель, считается
    один раз (без двойного счёта).

    T7.129 (стенд `.92`, спор по адресу smart-lab.ru/company/mozgovik/blog/1359553.php):
    address'у соответствуют НЕСКОЛЬКО строк `sources` — одна страница в нескольких
    прочтениях (разные `content_hash`). Прежде это читалось как двусмысленность и давало
    отказ «неоднозначно», то есть страницу оспорить было нельзя вовсе. Теперь роли держат
    ВСЕ строки названного адреса: оператор спорит страницу, а хост заводит коррекцию на
    каждую её версию (см. `DisputePair.merge_pairs`). Двусмысленность РАЗНЫХ адресов —
    прежний отказ `dispute_source_not_found`; разные страницы с одним адресом по-прежнему
    неразличимы, потому что адрес и есть идентичность страницы (`_uri_identity`).
    """

    def _rows(uri: str, role: str, candidates: Sequence[ClaimSource]) -> tuple[ClaimSource, ...]:
        matches: dict[Any, ClaimSource] = {}
        for source in candidates:
            if _uri_matches(source.canonical_uri, uri):
                matches[source.source_id] = source
        if not matches:
            raise DisputeError(REFUSAL_SOURCE_NOT_FOUND, detail=f"{role}: {uri}")
        return tuple(matches[sid] for sid in sorted(matches))

    primary_rows = _rows(primary_uri, "primary", _primary_candidates(sources, producer_sources))
    retelling_rows = _rows(retelling_uri, "retelling", sources)
    if {s.source_id for s in primary_rows} & {s.source_id for s in retelling_rows}:
        # одна и та же страница (одна строка или общий адрес обеих ролей) не бывает
        # пересказом самой себя: выбор «другой строки того же адреса» здесь был бы ложью
        raise DisputeError(REFUSAL_SAME_SOURCE)
    return DisputePair(primary_rows=primary_rows, retelling_rows=retelling_rows)


def _primary_candidates(
    evidence: Sequence[ClaimSource], producers: Sequence[ClaimSource]
) -> list[ClaimSource]:
    """Кандидаты первоисточника: улики ∪ производители, дедупликация по строке источника."""
    seen = {s.source_id for s in evidence}
    return list(evidence) + [p for p in producers if p.source_id not in seen]


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


async def claim_producer_sources(db: AsyncSession, claim_id: uuid.UUID) -> list[ClaimSource]:
    """Источники-производители, к которым хост уже приписал улики утверждения (T7.114).

    Две уже записанные формы решения хоста, читаемые так же, как их читает
    группировщик (`source_graph.py::_value_attribution_parents`; детектор отсюда не
    вызывается):

    - страничный указатель `sources.parent_source_id` источника улики;
    - запись значения `evidence.scope.value_attribution.parent_source_id` — любая
      версия метода: маркер формы `VALUE_ATTRIBUTION_SCHEMA` с T7.85 не поднимался,
      прежние записи читаются (урок T7.85(5)).

    Родитель, совпадающий с самой уликой, отдельно не перечисляется: кандидат-улика
    уже его содержит (`_primary_candidates` дедуплицирует по строке источника).
    """
    pointer_rows = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT p.id AS source_id, p.canonical_uri "
                    "FROM evidence e "
                    "JOIN sources es ON es.id = e.source_id "
                    "JOIN sources p ON p.id = es.parent_source_id "
                    "WHERE e.claim_id = :c AND e.source_id IS NOT NULL "
                    "  AND es.parent_source_id IS NOT NULL AND es.parent_source_id <> es.id "
                    "ORDER BY p.id"
                ),
                {"c": claim_id},
            )
        )
        .mappings()
        .all()
    )
    producers = [ClaimSource(source_id=r["source_id"], canonical_uri=r["canonical_uri"]) for r in pointer_rows]

    attribution_rows = (
        (
            await db.execute(
                text(
                    "SELECT scope -> 'value_attribution' ->> 'parent_source_id' AS parent_id "
                    "FROM evidence "
                    "WHERE claim_id = :c AND source_id IS NOT NULL "
                    "  AND jsonb_typeof(scope -> 'value_attribution') = 'object' "
                    "  AND scope -> 'value_attribution' ->> 'schema' = :schema"
                ),
                {"c": claim_id, "schema": VALUE_ATTRIBUTION_SCHEMA},
            )
        )
        .scalars()
        .all()
    )
    seen = {p.source_id for p in producers}
    extra: list[uuid.UUID] = []
    for raw_parent in attribution_rows:
        parent_id = _as_uuid(raw_parent)
        if parent_id is None or parent_id in seen:
            continue
        seen.add(parent_id)
        extra.append(parent_id)
    if extra:
        rows = (
            (
                await db.execute(
                    text("SELECT id, canonical_uri FROM sources WHERE id = ANY(:ids) ORDER BY id"),
                    {"ids": extra},
                )
            )
            .mappings()
            .all()
        )
        producers.extend(
            ClaimSource(source_id=r["id"], canonical_uri=r["canonical_uri"]) for r in rows
        )
    return producers


def _as_uuid(value: Any) -> uuid.UUID | None:
    """asyncpg возвращает свой UUID-тип (AGENTS §7): guard перед приведением."""
    if isinstance(value, uuid.UUID):
        return value
    if isinstance(value, str):
        try:
            return uuid.UUID(value)
        except ValueError:
            return None
    return None


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


async def _producer_group_ids(
    db: AsyncSession, *, assessment_id: uuid.UUID | None, parent_id: uuid.UUID, claim_id: uuid.UUID
) -> set[str]:
    """Группа производителя в снимке независимости действующей оценки (T7.114).

    Производитель не входит в участники снимка (участники — только источники улик),
    поэтому его группа читается из того же снимка — через улики этого утверждения,
    которым хост записал его родителем (обе формы указателя). Группировщик склеивает
    детей общего родителя в одну группу, поэтому обычно это ровно одна группа.
    Ноль групп (снимка нет) или больше одной (указатель записан уже ПОСЛЕ снимка —
    каскад ещё не пересчитал оценку) означает «группа производителя неизвестна»:
    отказа «уже сгруппированы» нет, merge лишь приведёт группы к согласию на пересчёте.
    """
    if assessment_id is None:
        return set()
    snapshot_id = (
        await db.execute(
            text("SELECT source_independence_snapshot_id FROM claim_assessments WHERE id = :a"),
            {"a": assessment_id},
        )
    ).scalar_one_or_none()
    if snapshot_id is None:
        return set()
    rows = (
        (
            await db.execute(
                text(
                    "SELECT DISTINCT m.group_id "
                    "FROM source_independence_members m "
                    "JOIN evidence e ON e.source_id = m.source_id AND e.claim_id = :c "
                    "LEFT JOIN sources es ON es.id = e.source_id "
                    "WHERE m.snapshot_id = :s "
                    "  AND (es.parent_source_id = :p "
                    "       OR (jsonb_typeof(e.scope -> 'value_attribution') = 'object' "
                    "           AND e.scope -> 'value_attribution' ->> 'schema' = :schema "
                    "           AND e.scope -> 'value_attribution' ->> 'parent_source_id' = :p_text))"
                ),
                {
                    "s": snapshot_id,
                    "c": claim_id,
                    "p": parent_id,
                    "p_text": str(parent_id),
                    "schema": VALUE_ATTRIBUTION_SCHEMA,
                },
            )
        )
        .scalars()
        .all()
    )
    return {str(group_id) for group_id in rows}


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

    # T7.114: первоисточник разрешается среди улик утверждения И среди производителей,
    # которым эти улики уже приписаны хостом; пересказ — только улика утверждения.

    claim = await _claim_row(db, claim_id)
    sources = await _claim_sources(db, claim_id)
    producers = await claim_producer_sources(db, claim_id)
    pair = resolve_dispute_pair(
        sources,
        primary_uri=primary_wanted,
        retelling_uri=retelling_wanted,
        producer_sources=producers,
    )

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

    existing_by_pair = [
        await _find_correction(
            db, actor=actor, primary_id=primary.source_id, retelling_id=retelling.source_id
        )
        for retelling, primary in pair.merge_pairs
    ]
    replayed_rows = [row for row in existing_by_pair if row is not None and row.valid]
    if len(replayed_rows) == len(existing_by_pair):
        # повтор: граф уже исправлен этим же оператором — ничего не меняем,
        # но перепроверку гарантируем (тот же детерминированный текст вопроса)
        replayed_row = replayed_rows[0]
        question, created = await put_operator_question(
            db, raw_text=recheck_question_text(str(claim["statement"])), raw_priority=0
        )
        await audit.record(
            AuditEventType.OPERATOR_COMMAND_COMPLETED,
            actor=actor,
            payload={
                "action": "claim_dispute",
                "claim_id": str(claim_id),
                "correction_id": str(replayed_row.id),
                "corrections": [str(row.id) for row in replayed_rows],
                "page_source_ids": [str(source.source_id) for source in pair.retelling_rows],
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
            correction_id=replayed_row.id,
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
            correction_ids=tuple(row.id for row in replayed_rows),
            retelling_source_ids=frozenset(s.source_id for s in pair.retelling_rows),
        )

    state, assessment_id = await _head_state(db, claim_id)
    if state != "current":
        raise DisputeError(
            REFUSAL_HEAD_NOT_CURRENT,
            detail=f"голова утверждения: {state or 'нет головы на активном снапшоте'}",
        )

    groups = await _group_ids(db, assessment_id, sorted(pair.source_ids))
    primary_group = groups.get(pair.primary.source_id)
    if primary_group is None:
        # первоисточник назван производителем (T7.114): его группа в снимке —
        # группа улик, которым он записан родителем
        derived = await _producer_group_ids(
            db, assessment_id=assessment_id, parent_id=pair.primary.source_id, claim_id=claim_id
        )
        primary_group = next(iter(derived)) if len(derived) == 1 else None
    retelling_groups = {groups.get(source.source_id) for source in pair.retelling_rows}
    if primary_group is not None and all(
        group is not None and group == primary_group for group in retelling_groups
    ):
        # каждая версия оспоренной страницы уже стоит в группе первоисточника: спор не
        # приносит ни одной новой склейки (T7.129: проверка по всем строкам адреса)
        raise DisputeError(REFUSAL_ALREADY_GROUPED)

    corrections: list[ORMSourceGraphCorrection] = []
    for (retelling_source, primary_source), existing in zip(
        pair.merge_pairs, existing_by_pair, strict=True
    ):
        if existing is not None:
            # коррекция этого же оператора по этой же паре уже была и её снимали:
            # строка оживает (history не переписывается), а не заводится вторая
            existing.valid = True
            existing.reason_audit_event_id = received.id
            corrections.append(existing)
        else:
            correction = ORMSourceGraphCorrection(
                id=uuid.uuid4(),
                actor=actor,
                kind=KIND_MERGE,
                from_source_id=retelling_source.source_id,
                to_source_id=primary_source.source_id,
                basis_artifact_id=await _basis_artifact_id(
                    db, claim_id, retelling_source.source_id
                ),
                rules_version=RULES_ENGINE_VERSION,
                valid=True,
                reason_audit_event_id=received.id,
            )
            db.add(correction)
            corrections.append(correction)
    await db.flush()

    cascade = await apply_source_graph_change(db, audit, source_ids=pair.source_ids, actor=actor)

    question, created = await put_operator_question(
        db, raw_text=recheck_question_text(str(claim["statement"])), raw_priority=0
    )

    await audit.record(
        AuditEventType.OPERATOR_COMMAND_COMPLETED,
        actor=actor,
        payload={
            "action": "claim_dispute",
            "claim_id": str(claim_id),
            "correction_id": str(corrections[0].id),
            "corrections": [str(row.id) for row in corrections],
            "page_source_ids": [str(source.source_id) for source in pair.retelling_rows],
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
        correction_id=corrections[0].id,
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
        correction_ids=tuple(row.id for row in corrections),
        retelling_source_ids=frozenset(source.source_id for source in pair.retelling_rows),
    )


async def _valid_corrections_for_claim(
    db: AsyncSession, claim_id: uuid.UUID
) -> list[ORMSourceGraphCorrection]:
    """Действующие коррекции склейки, затрагивающие источники claim'а (новые сначала).

    Строки берутся ORM-запросом и блокируются: `valid` меняют именно они,
    а не копии значений из raw-SQL выборки.
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
        return []
    return list(
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
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )


async def _same_page_corrections(
    db: AsyncSession, claim_id: uuid.UUID, correction: ORMSourceGraphCorrection
) -> list[ORMSourceGraphCorrection]:
    """Действующие коррекции того же спора по OTHER версиям той же страницы (T7.129).

    Спор по адресу заводит по коррекции на каждую строку `sources` с этим адресом.
    Отмена обязана снять весь набор: иначе «отозванный» спор продолжает склеивать
    остальные версии страницы, а журнал сообщает об отменённой коррекции неверно.
    Совпадение — по идентичности адреса (`_uri_identity`: схема не важна, важны хост,
    путь и query), а не по дословному `canonical_uri`.
    """

    candidates = [
        row
        for row in await _valid_corrections_for_claim(db, claim_id)
        if row.id != correction.id
        and row.actor == correction.actor
        and row.to_source_id == correction.to_source_id
        and row.rules_version == correction.rules_version
    ]
    if not candidates:
        return []
    wanted_ids = {row.from_source_id for row in candidates} | {correction.from_source_id}
    address_rows = (
        await db.execute(
            text("SELECT id, canonical_uri FROM sources WHERE id = ANY(:ids)"),
            {"ids": list(wanted_ids)},
        )
    ).all()
    addresses: dict[Any, str | None] = {row[0]: row[1] for row in address_rows}
    representative = addresses.get(correction.from_source_id)
    if not representative:
        return []
    wanted = _uri_identity(str(representative))
    return [
        row
        for row in candidates
        if addresses.get(row.from_source_id)
        and _uri_identity(str(addresses[row.from_source_id])) == wanted
    ]


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

    corrections = await _valid_corrections_for_claim(db, claim_id)
    if not corrections:
        raise DisputeError(REFUSAL_CORRECTION_NOT_FOUND)
    representative = corrections[0]
    cancelled = [representative, *await _same_page_corrections(db, claim_id, representative)]

    for correction in cancelled:
        # строка не удаляется — снимается флаг действенности (история знания не переписывается)
        correction.valid = False
    await db.flush()

    cascade = await apply_source_graph_change(
        db,
        audit,
        source_ids=frozenset(
            {row.from_source_id for row in cancelled} | {row.to_source_id for row in cancelled}
        ),
        actor=actor,
    )
    received.payload = {
        **dict(received.payload or {}),
        "correction_id": str(representative.id),
        "corrections": [str(row.id) for row in cancelled],
        "cancelled_by": actor,
    }
    await audit.record(
        AuditEventType.OPERATOR_COMMAND_COMPLETED,
        actor=actor,
        payload={
            "action": "claim_dispute_cancel",
            "claim_id": str(claim_id),
            "correction_id": str(representative.id),
            "corrections": [str(row.id) for row in cancelled],
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
        correction_id=representative.id,
        actor=actor,
        reason=reason_text,
        invalidated=cascade.invalidated,
        jobs_created=cascade.jobs_created,
        affected_claims=cascade.affected_claims,
        source_graph_revision=cascade.revision,
        correction_ids=tuple(row.id for row in cancelled),
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
