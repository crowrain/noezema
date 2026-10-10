"""Knowledge-graph queries for the M7 web (T7.1, §22.1).

Read-only views over claims / assessments / dependencies / evidence /
sources / artifacts. Every head-joined query pins to the EFFECTIVE
config snapshot (``runtime_config_heads`` — the same fail-closed pointer
the runtime uses); candidate/shadow heads are visible per claim but are
never presented as current. No writes: a provenance read must not create
independence snapshots (those are fixed by assessments).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from typing import Any, Final

from fastapi import HTTPException
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.web.labels import LABELS, describe
from apps.web.producer_view import producer_publication_name
from apps.web.reliability import describe_verification, reliability
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import AuditEventType
from packages.memory.dispute import list_claim_disputes

# the effective snapshot pointer (fail-closed: the view is served from
# the same head the runtime reads)
_EFF_SNAP = (
    "(SELECT active_config_snapshot_id FROM runtime_config_heads WHERE scope = 'global')"
)

#: Тот же указатель наружу (T7.65): приложение ответа читает головы утверждений по
#: действующему снимку правил — одному и тому же, который читает рантайм.
EFFECTIVE_SNAPSHOT_SQL = _EFF_SNAP

HEAD_STATES = ("current", "pending", "invalid", "none")

#: Состояния операторского спора (T7.81) — закрытый набор витрины: он подписан
#: словарём и выводится из строки коррекции (`valid`), а не из догадки. Сам
#: спор живёт в `source_graph_corrections`; здесь только то, что видит человек.
DISPUTED_STATE: Final = "disputed"
WITHDRAWN_STATE: Final = "withdrawn"
DISPUTE_STATES: Final[tuple[str, str]] = (DISPUTED_STATE, WITHDRAWN_STATE)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _uuid(value: Any) -> str | None:
    return None if value is None else str(value)


async def effective_claim_rules(db: AsyncSession) -> dict[str, Any]:
    """Пороги типов утверждений из ДЕЙСТВУЮЩЕГО снимка правил (только чтение).

    Подписям нужен порог ровно того снапшота, по которому rules engine оценивал
    знание; саму оценку здесь никто не пересчитывает (ADR-0026).
    """
    row = (
        await db.execute(
            text(
                """
                SELECT cs.claim_type_rules
                FROM runtime_config_heads h
                JOIN config_snapshots cs ON cs.id = h.active_config_snapshot_id
                WHERE h.scope = 'global'
                """
            )
        )
    ).scalar_one_or_none()
    if isinstance(row, Mapping):
        return dict(row)
    return {}


#: События ленты, в которых rules engine записывает причины оценки (T7.87, ADR-0035 §3).
#: Их два, и оба несут `assessment_id` + `reasons`: `claim_assessed` пишет commit сессии
#: (`packages/memory/service.py::_assess`), `reassessment_job_completed` — рабочий
#: переоценки (`packages/memory/reassessment.py`). У события рабочего нет строки сессии,
#: поэтому причина head обязана читаться из того события, которое её записало.
ASSESSMENT_EVENT_TYPES: Final[tuple[str, ...]] = (
    AuditEventType.CLAIM_ASSESSED.value,
    AuditEventType.REASSESSMENT_JOB_COMPLETED.value,
)

#: Те же типы как SQL-литерал: витрины, которым нужно встроить перечень прямо в запрос
#: (список вопросов не может делать отдельный запрос ради причин), берут ровно этот список.
ASSESSMENT_EVENT_TYPES_SQL: Final[str] = ", ".join(f"'{t}'" for t in ASSESSMENT_EVENT_TYPES)


def _in_params(prefix: str, values: Sequence[Any]) -> tuple[str, dict[str, Any]]:
    """`IN (:p0, :p1, …)` с привязанными параметрами (dynamic SQL без None-ловушки)."""
    names = [f"{prefix}{index}" for index in range(len(values))]
    return ", ".join(f":{name}" for name in names), dict(zip(names, values, strict=True))


async def assessment_reason_rows(
    db: AsyncSession, assessment_ids: Sequence[str], floor: datetime | None
) -> dict[str, list[str]]:
    """Причины оценок — из того события ленты, которое их записало (T7.73, T7.87).

    Rules engine хранит причины в payload события оценки; долговременной колонки причин
    нет, и миграций этот путь не требует. Витрина только подписывает уже вычисленное
    (ADR-0026): она ничего не пересчитывает и не придумывает причину там, где её нет.

    Выборка — ОДИН запрос на весь набор id (карточка ответа платит за неё ровно один
    SELECT: бюджет T7.74 от добавления второго типа события не растёт). На одну оценку
    берётся самое свежее событие: хронология — `occurred_at` + `sequence`, а не
    `created_at` (AGENTS §7: в долгой транзакции у всех строк одно время). Нижняя
    граница времени — самая ранняя из этих claim-строк: оценка не может быть записана
    раньше claim'а, поэтому выборка идёт по индексу `occurred_at`, а не полным сканом.
    """
    if not assessment_ids:
        return {}
    clause, params = _in_params("aid", assessment_ids)
    types, type_params = _in_params("etype", ASSESSMENT_EVENT_TYPES)
    params.update(type_params)
    time_floor = ""
    if floor is not None:
        # динамическое условие: параметр присутствует в SQL только когда он есть
        # (ловушка AGENTS §7 — asyncpg не выводит тип из None)
        time_floor = "AND occurred_at >= :floor"
        params["floor"] = floor
    rows = list(
        (
            await db.execute(
                text(
                    f"""
                    WITH latest AS (
                        SELECT DISTINCT ON (payload->>'assessment_id') payload
                        FROM audit_events
                        WHERE type IN ({types})
                          AND payload->>'assessment_id' IN ({clause})
                          {time_floor}
                        ORDER BY payload->>'assessment_id', occurred_at DESC, sequence DESC
                    )
                    SELECT l.payload->>'assessment_id' AS assessment_id, r.reason
                    FROM latest l
                    LEFT JOIN LATERAL jsonb_array_elements_text(
                        COALESCE(l.payload->'reasons', '[]'::jsonb)
                    ) WITH ORDINALITY AS r(reason, n) ON TRUE
                    ORDER BY 1, r.n
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    reasons: dict[str, list[str]] = {}
    for row in rows:
        reason = row["reason"]
        if reason is None:
            continue
        reasons.setdefault(str(row["assessment_id"]), []).append(str(reason))
    return reasons


#: Колонки фактов действующей оценки для правила «кластер вокруг производителя» (T7.87).
CURRENT_ASSESSMENT_FACT_COLUMNS: Final[str] = "cur_ae.role AS assessment_role, cur_sim.group_id"


def current_assessment_evidence_joins(claim_ref: str, evidence_ref: str, source_ref: str) -> str:
    """JOIN'ы фактов ДЕЙСТВУЮЩЕЙ оценки для запроса, который читает улики.

    `claim_ref`, `evidence_ref` и `source_ref` — как вызывающий запрос обозвал строку
    утверждения, строку улики и её источник: карточка ответа, список знаний и карточка
    утверждения пользуются одним текстом, чтобы все витрины читали одни и те же записанные
    факты (расхождение бейджей между карточкой и списком — риск ADR-0035 §4 п.3).

    Голова берётся по действующему снимку правил и `assessment_state = 'current'` — тот же
    отбор, что у `assessment_view` и у списка знаний: правило смотрит на ту оценку, которую
    витрина уже показала, и никогда на прежнюю или pending. Ролей или записанных групп нет →
    колонки NULL → правило молчит, а не додумывает (fail-closed).
    """
    return f"""
            LEFT JOIN LATERAL (
                SELECT a.id AS current_assessment_id,
                       a.source_independence_snapshot_id AS snapshot_id
                FROM claim_assessment_heads h
                JOIN claim_assessments a ON a.id = h.current_assessment_id
                WHERE h.claim_id = {claim_ref}
                  AND h.config_snapshot_id = {_EFF_SNAP}
                  AND h.assessment_state = 'current'
            ) cur_a ON TRUE
            LEFT JOIN assessment_evidence cur_ae
                   ON cur_ae.assessment_id = cur_a.current_assessment_id
                  AND cur_ae.evidence_id = {evidence_ref}
            LEFT JOIN source_independence_members cur_sim
                   ON cur_sim.snapshot_id = cur_a.snapshot_id
                  AND cur_sim.source_id = {source_ref}
    """


def _assessment_explanations(
    fact_rows: Iterable[Any],
    reasons_by_assessment: Mapping[str, Sequence[str]],
) -> dict[str, JsonDict]:
    """{claim_id: {"single_producer", "reasons"}} по правилу ADR-0035 вариант A.

    `fact_rows` — улики с колонками действующей оценки (`claim_id`, `assessment_id`, колонки из
    `CURRENT_ASSESSMENT_FACT_COLUMNS` плюс `scope`, `parent_source_id`, `parent_uri`). Никакого
    пересчёта групп и ролей: правило читает те же записанные значения, которые витрина уже
    показала (fail-closed внутри `apps.web.producer_view`).
    """
    grouped: dict[str, list[Mapping[str, Any]]] = {}
    assessment_of: dict[str, str] = {}
    for row in fact_rows:
        if not isinstance(row, Mapping) or row.get("claim_id") is None:
            continue
        claim_id = str(row["claim_id"])
        grouped.setdefault(claim_id, []).append(row)
        if row.get("assessment_id") is not None:
            assessment_of[claim_id] = str(row["assessment_id"])
    return {
        claim_id: {
            "single_producer": producer_publication_name(
                rows, reasons=reasons_by_assessment.get(assessment_of.get(claim_id, ""), ())
            ),
            "reasons": list(reasons_by_assessment.get(assessment_of.get(claim_id, ""), [])),
        }
        for claim_id, rows in grouped.items()
    }


async def current_assessment_explanation(
    db: AsyncSession, claim_ids: Sequence[str], *, floor: datetime | None = None
) -> dict[str, JsonDict]:
    """Объяснение действующей оценки для набора утверждений: имя производителя + причины.

    Один запрос фактов улик и один запрос причин на весь набор — сколько бы утверждений ни
    показывала страница (N+1 запрещён и проверяется тестом списка). `floor` — самая ранняя из
    этих claim-строк: та же нижняя граница ленты, что у карточки ответа (`assessment_reason_rows`).

    Возврат — {claim_id: {"single_producer": str | None, "reasons": [коды]}}. Утверждения, у
    которых нет ни одной улики действующей оценки, в результат не попадают: витрина показывает
    для них прежнюю подпись (fail-closed), а не выдуманное объяснение.
    """
    if not claim_ids:
        return {}
    clause, params = _in_params("c", claim_ids)
    facts = list(
        (
            await db.execute(
                text(
                    f"""
                    SELECT e.claim_id,
                           cur_a.current_assessment_id AS assessment_id,
                           {CURRENT_ASSESSMENT_FACT_COLUMNS},
                           e.scope, s.parent_source_id, ps.canonical_uri AS parent_uri
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN sources ps ON ps.id = s.parent_source_id
                    {current_assessment_evidence_joins("e.claim_id", "e.id", "e.source_id")}
                    WHERE e.claim_id IN ({clause})
                    ORDER BY e.claim_id, e.created_at, e.id
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    assessment_ids = sorted({str(r["assessment_id"]) for r in facts if r["assessment_id"] is not None})
    reasons = await assessment_reason_rows(db, assessment_ids, floor)
    return _assessment_explanations(facts, reasons)


#: Рамка строки причины на витрине (T7.87, ADR-0035 §6.1): «причина: …» + уже записанная
#: подпись rules engine из категории `assessment_reason` (словарь T7.73). Рамку добавляет
#: витрина — так же, как она добавляет «было:/стало:» в истории перепроверки (T7.74,
#: `apps.web.answer.reverify_history_view`). Подписи для незнакомой причины витрина не
#: выдумывает: строка просто не появляется (запасной путь `describe` возвращает сам код, и
#: на простой экран такой код не должен попадать — AGENTS §7 T7.65).
REASON_LINE_LEAD: Final = "причина"


def assessment_reason_lines(reasons: Sequence[str]) -> list[str]:
    """Видимые строки причин действующей оценки (T7.87) — только подписи словаря.

    Одна записанная причина = одна строка; порядок повторяет порядок причин в событии оценки,
    витрина их не пересортировывает и не сокращает. Пустой список причин даёт пустой список
    строк: выдуманной причины на экране быть не может.
    """
    table = LABELS.get("assessment_reason", {})
    lines: list[str] = []
    for code in reasons:
        entry = table.get(code if isinstance(code, str) else str(code))
        if entry is None:
            continue
        label = str(entry["label"])
        if not label:
            continue
        line = f"{REASON_LINE_LEAD}: {label}"
        if line not in lines:
            lines.append(line)
    return lines


def grade_reason_view(reasons: Sequence[str]) -> list[JsonDict]:
    """Подписанные причины оценки для витрины (T7.73).

    Порядок — как их записал rules engine; подписи — только из словаря, выдуманных причин здесь
    нет. Одна функция на карточку ответа, список знаний и карточку утверждения: причины на всех
    трёх витринах обязаны быть одними и теми же (T7.87).
    """
    return [{"code": code, **describe("assessment_reason", code)} for code in reasons]


def _min_grade_for(rules: Mapping[str, Any], claim_type: Any) -> str | None:
    rule = rules.get(str(claim_type)) if claim_type is not None else None
    if isinstance(rule, Mapping):
        grade = rule.get("min_grade_for_supported")
        if isinstance(grade, str):
            return grade
    return None


def assessment_view(
    *,
    claim_type: Any,
    head_state: Any,
    epistemic_status: Any,
    effective_grade: Any,
    freshness_status: Any,
    rules: Mapping[str, Any],
    single_producer: str | None = None,
) -> dict[str, Any]:
    """Аддитивные человеческие подписи к уже вычисленной оценке утверждения.

    `single_producer` (T7.87, ADR-0035 вариант A) — имя производителя, которое
    `apps.web.producer_view` вывел из записанных фактов этой же оценки. Оно передаётся в
    `reliability` и может поменять только подписи случая «hypothesis + слабый уровень»;
    уровень, grade и пороги правила не пересматривают. Одна функция подписи на карточке
    ответа, в списке знаний и на карточке утверждения — иначе витрины разошлись бы в
    бейджах (риск ADR-0035 §4 п.3).
    """
    head = str(head_state) if head_state is not None else "none"
    grade = str(effective_grade) if effective_grade is not None else None
    return {
        "type_label": describe("claim_type", claim_type)["label"],
        "head_label": describe("claim_head_state", head)["label"],
        "freshness_label": describe("freshness_status", freshness_status)["label"],
        "grade_label": describe("evidence_grade", grade)["label"] if grade is not None else None,
        "reliability": reliability(
            str(claim_type) if claim_type is not None else None,
            str(epistemic_status) if epistemic_status is not None else None,
            grade,
            head_state=head,
            min_grade_for_supported=_min_grade_for(rules, claim_type),
            single_producer=single_producer,
        ),
    }


async def list_claims(
    db: AsyncSession, *, limit: int = 50, offset: int = 0, state: str | None = None
) -> JsonDict:
    """Claims with their head state on the effective snapshot."""
    total = (
        await db.execute(
            text("SELECT count(*) FROM claims"),
        )
    ).scalar_one()
    # a dynamic WHERE clause: SQLAlchemy's text() does not convert a
    # nullable bind parameter (asyncpg cannot infer its type from None)
    where = ""
    params: dict[str, Any] = {"limit": limit, "offset": offset}
    if state is not None:
        where = " AND COALESCE(h.assessment_state, 'none') = :state"
        params["state"] = state
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT c.id, c.statement, c.claim_type, c.freshness_status,
                           c.as_of, c.reverify_after, c.created_at,
                           COALESCE(h.assessment_state, 'none') AS head_state,
                           h.epistemic_status, a.effective_grade, a.confidence
                    FROM claims c
                    LEFT JOIN claim_assessment_heads h
                           ON h.claim_id = c.id
                          AND h.config_snapshot_id = {_EFF_SNAP}
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    WHERE 1 = 1{where}
                    ORDER BY c.created_at DESC, c.id
                    LIMIT :limit OFFSET :offset
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    rules = await effective_claim_rules(db)
    # T7.87 (ADR-0035 вариант A): список знаний обязан показывать ТОТ ЖЕ бейдж, что и карточка
    # ответа, и объяснять оценку теми же записанными причинами. Два запроса на всю страницу,
    # сколько бы утверждений на ней ни было: N+1 запрещён (проверяется тестом).
    explanations = await current_assessment_explanation(
        db,
        [str(r["id"]) for r in rows],
        floor=min((r["created_at"] for r in rows if r["created_at"] is not None), default=None),
    )
    claims: list[JsonDict] = []
    for r in rows:
        explanation = explanations.get(str(r["id"]))
        reasons = list(explanation["reasons"]) if explanation is not None else []
        item: JsonDict = {
            "id": _uuid(r["id"]),
            "statement": r["statement"],
            "claim_type": r["claim_type"],
            "freshness_status": r["freshness_status"],
            "as_of": _iso(r["as_of"]),
            "reverify_after": _iso(r["reverify_after"]),
            "created_at": _iso(r["created_at"]),
            "head_state": r["head_state"],
            "epistemic_status": r["epistemic_status"],
            "effective_grade": r["effective_grade"],
            "confidence": float(r["confidence"]) if r["confidence"] is not None else None,
        }
        item.update(
            assessment_view(
                claim_type=r["claim_type"],
                head_state=r["head_state"],
                epistemic_status=r["epistemic_status"],
                effective_grade=r["effective_grade"],
                freshness_status=r["freshness_status"],
                rules=rules,
                single_producer=explanation["single_producer"] if explanation is not None else None,
            )
        )
        # T7.73 (ADR-0018) + T7.87: список подписывает причины действующей оценки теми же
        # подписями словаря, что и карточка ответа, и той же рамкой «причина: …».
        item["grade_reasons"] = grade_reason_view(reasons)
        item["grade_reason_lines"] = assessment_reason_lines(reasons)
        claims.append(item)
    return {
        "total": int(total),
        "claims": claims,
    }


async def claim_detail(db: AsyncSession, claim_id: uuid.UUID) -> JsonDict:
    """One claim: body, all heads (effective first), evidence, dependencies."""
    claim = (
        await db.execute(
            text(
                """
                SELECT c.id, c.statement, c.claim_type, c.freshness_status,
                       c.valid_from, c.valid_to, c.as_of, c.observed_at,
                       c.reverify_after, c.dependency_fingerprint, c.topic,
                       c.created_in_session, c.created_at
                FROM claims c WHERE c.id = :id
                """
            ),
            {"id": claim_id},
        )
    ).mappings().first()
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")

    heads = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT h.config_snapshot_id, h.assessment_state, h.epistemic_status,
                           h.current_assessment_id, h.prepared_by, h.updated_at,
                           cs.activation_state, cs.activation_mode,
                           a.effective_grade, a.confidence, a.rules_version,
                           a.assessed_scope, a.created_at AS assessment_created_at
                    FROM claim_assessment_heads h
                    LEFT JOIN claim_assessments a ON a.id = h.current_assessment_id
                    LEFT JOIN config_snapshots cs ON cs.id = h.config_snapshot_id
                    WHERE h.claim_id = :id
                    ORDER BY (h.config_snapshot_id = {_EFF_SNAP}) DESC, h.config_snapshot_id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    evidence = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT e.id, e.claim_id, e.relation, e.evidence_kind, e.identity_hash,
                           e.scope, e.source_id, e.chunk_id,
                           e.observation_artifact_id, e.environment_manifest_id,
                           e.created_in_session, e.created_at,
                           s.source_type, s.canonical_uri, s.content_hash,
                           s.parent_source_id, ps.canonical_uri AS parent_uri,
                           ar.sha256 AS artifact_sha256,
                           cur_a.current_assessment_id AS assessment_id,
                           {CURRENT_ASSESSMENT_FACT_COLUMNS}
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN sources ps ON ps.id = s.parent_source_id
                    LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id
                    {current_assessment_evidence_joins("e.claim_id", "e.id", "e.source_id")}
                    WHERE e.claim_id = :id
                    ORDER BY e.created_at, e.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    deps_out = (
        (
            await db.execute(
                text(
                    """
                    SELECT d.to_claim_id, d.kind, tc.statement
                    FROM claim_dependencies d
                    JOIN claims tc ON tc.id = d.to_claim_id
                    WHERE d.from_claim_id = :id
                    ORDER BY d.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )
    deps_in = (
        (
            await db.execute(
                text(
                    """
                    SELECT d.from_claim_id, d.kind, fc.statement
                    FROM claim_dependencies d
                    JOIN claims fc ON fc.id = d.from_claim_id
                    WHERE d.to_claim_id = :id
                    ORDER BY d.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    detail: JsonDict = {
        "id": _uuid(claim["id"]),
        "statement": claim["statement"],
        "claim_type": claim["claim_type"],
        "freshness_status": claim["freshness_status"],
        "valid_from": _iso(claim["valid_from"]),
        "valid_to": _iso(claim["valid_to"]),
        "as_of": _iso(claim["as_of"]),
        "observed_at": _iso(claim["observed_at"]),
        "reverify_after": _iso(claim["reverify_after"]),
        "dependency_fingerprint": claim["dependency_fingerprint"],
        "topic": claim["topic"],
        "created_in_session": _uuid(claim["created_in_session"]),
        "created_at": _iso(claim["created_at"]),
        "heads": [
            {
                "config_snapshot_id": _uuid(h["config_snapshot_id"]),
                "activation_mode": h["activation_mode"],
                "activation_state": h["activation_state"],
                "assessment_state": h["assessment_state"],
                "epistemic_status": h["epistemic_status"],
                "current_assessment_id": _uuid(h["current_assessment_id"]),
                "prepared_by": h["prepared_by"],
                "updated_at": _iso(h["updated_at"]),
                "effective_grade": h["effective_grade"],
                "confidence": (
                    float(h["confidence"]) if h["confidence"] is not None else None
                ),
                "rules_version": h["rules_version"],
                "assessed_scope": h["assessed_scope"],
                "assessment_created_at": _iso(h["assessment_created_at"]),
            }
            for h in heads
        ],
        "evidence": [
            {
                "id": _uuid(e["id"]),
                "relation": e["relation"],
                "evidence_kind": e["evidence_kind"],
                "identity_hash": e["identity_hash"],
                "scope": e["scope"],
                "source_id": _uuid(e["source_id"]),
                "chunk_id": e["chunk_id"],
                "observation_artifact_id": _uuid(e["observation_artifact_id"]),
                "environment_manifest_id": _uuid(e["environment_manifest_id"]),
                "source_type": e["source_type"],
                "source_uri": e["canonical_uri"],
                "source_content_hash": e["content_hash"],
                "artifact_sha256": e["artifact_sha256"],
                "created_at": _iso(e["created_at"]),
            }
            for e in evidence
        ],
        "depends_on": [
            {"claim_id": _uuid(d["to_claim_id"]), "kind": d["kind"], "statement": d["statement"]}
            for d in deps_out
        ],
        "depended_by": [
            {"claim_id": _uuid(d["from_claim_id"]), "kind": d["kind"], "statement": d["statement"]}
            for d in deps_in
        ],
    }

    # Аддитивные подписи (T7.64): действующая оценка берётся из первой head —
    # запрос уже ставит снапшот действует/не действует на первое место.
    head = heads[0] if len(heads) > 0 else None
    head_state = str(head["assessment_state"]) if head is not None else "none"
    rules = await effective_claim_rules(db)
    # T7.87 (ADR-0035 §4 п.3): карточка утверждения объясняет оценку теми же записанными
    # причинами и тем же правилом про производителя, что карточка ответа и список знаний:
    # один источник фактов (`current_assessment_evidence_joins`) и одна функция подписи.
    explanation = _assessment_explanations(
        evidence,
        await assessment_reason_rows(
            db,
            sorted({str(e["assessment_id"]) for e in evidence if e["assessment_id"] is not None}),
            claim["created_at"],
        ),
    ).get(str(claim["id"]))
    reasons = list(explanation["reasons"]) if explanation is not None else []
    detail["head_state"] = head_state
    detail.update(
        assessment_view(
            claim_type=claim["claim_type"],
            head_state=head_state,
            epistemic_status=head["epistemic_status"] if head is not None else None,
            effective_grade=head["effective_grade"] if head is not None else None,
            freshness_status=claim["freshness_status"],
            rules=rules,
            single_producer=explanation["single_producer"] if explanation is not None else None,
        )
    )
    detail["grade_reasons"] = grade_reason_view(reasons)
    detail["grade_reason_lines"] = assessment_reason_lines(reasons)
    # «как проверено» — только из реально переданных полей доказательств.
    detail["verification"] = describe_verification(detail["evidence"])
    return detail


async def claim_provenance(db: AsyncSession, claim_id: uuid.UUID) -> JsonDict:
    """Provenance navigation for one claim: evidence → source (and its
    parent) / artifact, plus the independence groups the CURRENT
    assessment fixed for this claim (read-only: the view never creates
    new snapshots)."""
    claim = (
        await db.execute(
            text("SELECT id FROM claims WHERE id = :id"), {"id": claim_id}
        )
    ).first()
    if claim is None:
        raise HTTPException(status_code=404, detail="claim not found")

    evidence = (
        (
            await db.execute(
                text(
                    """
                    SELECT e.id, e.relation, e.evidence_kind, e.identity_hash,
                           e.source_id, e.chunk_id,
                           e.observation_artifact_id, e.environment_manifest_id,
                           s.source_type, s.canonical_uri, s.content_hash,
                           s.retrieved_at, s.parent_source_id,
                           ps.canonical_uri AS parent_uri,
                           ps.content_hash AS parent_content_hash,
                           ar.sha256 AS artifact_sha256, ar.size AS artifact_size,
                           ar.trust_class, ar.origin AS artifact_origin,
                           em.protocol_hash AS env_protocol_hash
                    FROM evidence e
                    LEFT JOIN sources s ON s.id = e.source_id
                    LEFT JOIN sources ps ON ps.id = s.parent_source_id
                    LEFT JOIN artifacts ar ON ar.id = e.observation_artifact_id
                    LEFT JOIN environment_manifests em ON em.id = e.environment_manifest_id
                    WHERE e.claim_id = :id
                    ORDER BY e.created_at, e.id
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .all()
    )

    # the current assessment (effective snapshot): its fixed independence
    # snapshots + the per-evidence roles
    current = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT a.source_independence_snapshot_id,
                           a.environment_independence_snapshot_id
                    FROM claim_assessment_heads h
                    JOIN claim_assessments a ON a.id = h.current_assessment_id
                    WHERE h.claim_id = :id
                      AND h.config_snapshot_id = {_EFF_SNAP}
                      AND h.assessment_state = 'current'
                    """
                ),
                {"id": claim_id},
            )
        )
        .mappings()
        .first()
    )

    source_groups: list[dict[str, Any]] = []
    env_groups: list[dict[str, Any]] = []
    roles: list[dict[str, Any]] = []
    if current is not None:
        src_snap = _uuid(current["source_independence_snapshot_id"])
        if src_snap is not None:
            members = (
                (
                    await db.execute(
                        text(
                            """
                            SELECT m.source_id, m.group_id, m.basis,
                                   s.canonical_uri
                            FROM source_independence_members m
                            LEFT JOIN sources s ON s.id = m.source_id
                            WHERE m.snapshot_id = :snap
                            ORDER BY m.source_id
                            """
                        ),
                        {"snap": src_snap},
                    )
                )
                .mappings()
                .all()
            )
            source_groups = [
                {
                    "source_id": _uuid(m["source_id"]),
                    "uri": m["canonical_uri"],
                    "group_id": m["group_id"],
                    "basis": m["basis"],
                }
                for m in members
            ]
        env_snap = _uuid(current["environment_independence_snapshot_id"])
        if env_snap is not None:
            members = (
                (
                    await db.execute(
                        text(
                            """
                            SELECT m.environment_manifest_id, m.group_id,
                                   m.relation, m.basis
                            FROM environment_independence_members m
                            WHERE m.snapshot_id = :snap
                            ORDER BY m.environment_manifest_id
                            """
                        ),
                        {"snap": env_snap},
                    )
                )
                .mappings()
                .all()
            )
            env_groups = [
                {
                    "environment_manifest_id": _uuid(m["environment_manifest_id"]),
                    "group_id": m["group_id"],
                    "relation": m["relation"],
                    "basis": m["basis"],
                }
                for m in members
            ]
        roles_rows = (
            (
                await db.execute(
                    text(
                        f"""
                        SELECT ae.evidence_id, ae.role, e.evidence_kind
                        FROM assessment_evidence ae
                        JOIN claim_assessment_heads h
                               ON h.claim_id = :id
                              AND h.config_snapshot_id = {_EFF_SNAP}
                              AND h.assessment_state = 'current'
                        JOIN evidence e ON e.id = ae.evidence_id
                        WHERE ae.assessment_id = h.current_assessment_id
                        ORDER BY ae.evidence_id, ae.role
                        """
                    ),
                    {"id": claim_id},
                )
            )
            .mappings()
            .all()
        )
        roles = [
            {
                "evidence_id": _uuid(r["evidence_id"]),
                "evidence_kind": r["evidence_kind"],
                "role": r["role"],
            }
            for r in roles_rows
        ]

    evidence_items: list[JsonDict] = [
            {
                "id": _uuid(e["id"]),
                "relation": e["relation"],
                "evidence_kind": e["evidence_kind"],
                "identity_hash": e["identity_hash"],
                "source": (
                    {
                        "id": _uuid(e["source_id"]),
                        "source_type": e["source_type"],
                        "canonical_uri": e["canonical_uri"],
                        "content_hash": e["content_hash"],
                        "retrieved_at": _iso(e["retrieved_at"]),
                        "chunk_id": e["chunk_id"],
                        "parent": (
                            {
                                "id": _uuid(e["parent_source_id"]),
                                "canonical_uri": e["parent_uri"],
                                "content_hash": e["parent_content_hash"],
                            }
                            if e["parent_source_id"] is not None
                            else None
                        ),
                    }
                    if e["source_id"] is not None
                    else None
                ),
                "artifact": (
                    {
                        "id": _uuid(e["observation_artifact_id"]),
                        "sha256": e["artifact_sha256"],
                        "size": int(e["artifact_size"]) if e["artifact_size"] is not None else None,
                        "trust_class": e["trust_class"],
                        "origin": e["artifact_origin"],
                    }
                    if e["observation_artifact_id"] is not None
                    else None
                ),
                "environment_manifest_id": _uuid(e["environment_manifest_id"]),
                "environment_protocol_hash": e["env_protocol_hash"],
            }
            for e in evidence
    ]

    return {
        "claim_id": _uuid(claim_id),
        "evidence": evidence_items,
        "source_groups": source_groups,
        "environment_groups": env_groups,
        "assessment_evidence_roles": roles,
        # Операторские исправления графа источников этого утверждения (T7.81):
        # только уже записанные строки коррекции + причина из журнала; view ничего
        # не оценивает и действующую оценку не прячет.
        "operator_corrections": await claim_source_corrections(db, claim_id),
        # «как проверено»: независимость групп источников упоминается только
        # когда эти группы действительно зафиксированы действующей оценкой.
        "verification": describe_verification(
            evidence_items, source_groups=source_groups, environment_groups=env_groups
        ),
    }


async def claim_source_corrections(db: AsyncSession, claim_id: uuid.UUID) -> list[JsonDict]:
    """Оспорено ли утверждение оператором и по какому основанию (T7.81).

    Строки `source_graph_corrections` уже лежат в базе; причина читается из
    payload'а события журнала, на которое указывает коррекция. Подписи — только
    из единого словаря: вид (склейка/разделение), состояние (оспорено/снято) и
    вход оператора. Grade здесь не назначается и не меняет ничего.
    """
    rows = await list_claim_disputes(db, claim_id)
    items: list[JsonDict] = []
    for row in rows:
        state = DISPUTED_STATE if row["valid"] else WITHDRAWN_STATE
        entry = describe("graph_correction_kind", row["kind"])
        state_entry = describe("dispute_state", state)
        actor_entry = describe("dispute_actor", row["actor"])
        items.append(
            {
                "correction_id": row["correction_id"],
                "kind": row["kind"],
                "kind_label": entry["label"],
                "kind_hint": entry["hint"],
                "state": state,
                "state_label": state_entry["label"],
                "state_hint": state_entry["hint"],
                "actor": row["actor"],
                "actor_label": actor_entry["label"],
                "reason": row["reason"],
                "retelling_uri": row["retelling_uri"],
                "primary_uri": row["primary_uri"],
                "rules_version": row["rules_version"],
                "created_at": row["created_at"],
                "withdrawn_by": row["withdrawn_by"],
                "withdrawn_by_label": (
                    describe("dispute_actor", row["withdrawn_by"])["label"]
                    if row["withdrawn_by"]
                    else None
                ),
                "withdrawn_at": row["withdrawn_at"],
                "withdraw_reason": row["withdraw_reason"],
            }
        )
    return items


async def list_dependencies(
    db: AsyncSession, *, claim_id: uuid.UUID | None = None, limit: int = 200
) -> JsonDict:
    """Dependency edges (from depends on to; §8.6), optionally narrowed
    to one claim (either direction)."""
    limit = max(1, min(limit, 1000))
    where = ""
    params: dict[str, Any] = {"limit": limit}
    if claim_id is not None:
        where = " AND (d.from_claim_id = :cid OR d.to_claim_id = :cid)"
        params["cid"] = claim_id
    rows = (
        (
            await db.execute(
                text(
                    f"""
                    SELECT d.id, d.from_claim_id, d.to_claim_id, d.kind,
                           fc.statement AS from_statement,
                           tc.statement AS to_statement
                    FROM claim_dependencies d
                    JOIN claims fc ON fc.id = d.from_claim_id
                    JOIN claims tc ON tc.id = d.to_claim_id
                    WHERE 1 = 1{where}
                    ORDER BY d.id
                    LIMIT :limit
                    """
                ),
                params,
            )
        )
        .mappings()
        .all()
    )
    return {
        "dependencies": [
            {
                "id": _uuid(r["id"]),
                "from_claim_id": _uuid(r["from_claim_id"]),
                "to_claim_id": _uuid(r["to_claim_id"]),
                "kind": r["kind"],
                "from_statement": r["from_statement"],
                "to_statement": r["to_statement"],
            }
            for r in rows
        ]
    }
