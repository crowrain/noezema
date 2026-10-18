"""Curator staging proposal schema (T1.13; T7.34 reverify — ADR-0018).

The curator proposes; the trusted host validates and records these as
session_staging operations (in M1 — in memory; the durable table arrives
with M2). Grade/epistemic status are NOT proposed here: only the
deterministic rules engine assigns them (§3.7).

A claim op is one of two: a NEW claim (statement + type), or a REVERIFY
of an existing one (`existing_claim_id` — the host-issued id, full or a
unique prefix). Reverify changes no claim row: it re-derives the
reference date/scope and produces a fresh assessment (the reverify
record, bound to the session) — the gate-5 "reverified" path.
"""

from __future__ import annotations

import uuid
from collections.abc import Set as AbstractSet
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import (
    ClaimType,
    DependencyKind,
    EvidenceRelation,
    QuestionOrigin,
)

#: hard cap on declared dependencies per claim (host-side budget)
MAX_DEPENDENCIES_PER_CLAIM = 10


class ClaimDependencyProposal(BaseModel):
    """A dependency of the new claim on an EXISTING corpus claim (T4.1).

    The target must be a claim ID the model saw in the knowledge
    context; the host re-validates existence at the commit boundary.
    """

    model_config = ConfigDict(extra="forbid")

    claim_id: uuid.UUID
    kind: DependencyKind = DependencyKind.EVIDENTIAL


class ClaimProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    statement: str = Field(min_length=1, max_length=2000)
    # T7.34 (ADR-0018): reverify of an EXISTING claim — the host-issued
    # id the model REFERENCEs (never mints), as seen in the knowledge
    # context line `[c:<uuid>]`. String (not UUID) on purpose: the
    # engine schema profile (ADR-0012) strips `format`, and the model
    # has truncated UUIDs (EVAL-4d pack 7) — the host resolves a full
    # UUID or a UNIQUE prefix among the session-visible claims,
    # fail-closed. When set, the op attaches to that claim: no new
    # claim row, the statement is a restatement (audit-only), and the
    # reverify record is the fresh assessment row (the session's
    # verification moment).
    existing_claim_id: str | None = Field(default=None, min_length=8, max_length=36)
    # ADR-0006 rev (cross-lingual search): the model renders the
    # statement in the other corpus language (MVP: English) so that
    # retrieval matches queries in either language. Search index only —
    # the knowledge text is always ``statement``.
    search_statements: list[str] = Field(
        default_factory=list,
        max_length=2,
        description="Англоязычные варианты формулировки (1–2), ТОЛЬКО для поиска",
    )
    claim_type: ClaimType
    scope: JsonDict = Field(default_factory=dict)
    as_of: datetime | None = None
    dependencies: list[ClaimDependencyProposal] = Field(
        default_factory=list, max_length=MAX_DEPENDENCIES_PER_CLAIM
    )


class EvidenceLink(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_index: int = Field(ge=0)
    claim_index: int = Field(ge=0)
    relation: EvidenceRelation
    note: str | None = Field(default=None, max_length=500)


class QuestionProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=2000)
    origin: QuestionOrigin
    rationale: str | None = Field(default=None, max_length=1000)


class CuratorProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(min_length=1, max_length=2000)
    claims: list[ClaimProposal] = Field(default_factory=list)
    evidence_links: list[EvidenceLink] = Field(default_factory=list)
    new_questions: list[QuestionProposal] = Field(default_factory=list)
    # T7.82 (B), ADR-0032: id СУЩЕСТВУЮЩИХ claim этого контекст-пака, на которые
    # опирается ответ, без перепроверки и без новой оценки (факультативное поле
    # prompt curator-v9). Никакого лимита в схеме нет специально: лишнее и мусорное
    # отбраковывает не pydantic-гейт всего предложения, а хостовый разрешатель в
    # _curator — честной отказной пометкой в payload уже существующего события
    # CLAIM_CREATED. Ни staging-операций, ни новых типов событий это поле не заводит.
    relied_claim_ids: list[str] = Field(default_factory=list)

    def validate_against(self, evidence_count: int, questions_max: int = 4) -> list[str]:
        """Host-side validation of references and budgets. Returns problem
        list (empty = valid).

        T7.84 (часть B, ADR-0033 §8): состав и порядок строк этого метода сохранены прежними
        (проверено тестом) — он остаётся точкой, где проверка опорной `as_of` ещё идёт по
        ВИДУ ОПЕРАЦИИ ИЗ ПРЕДЛОЖЕНИЯ. Оркестратор вызывает вместо него два отдельных шага:
        `structural_problems` (тому безразличен вид операции) и `temporal_as_of_problems`
        (после того, как вид операции определён окончательно)."""
        return self._walk(evidence_count, questions_max, check_as_of=True)

    def structural_problems(self, evidence_count: int, questions_max: int = 4) -> list[str]:
        """T7.84 (часть B): проверки, которым безразлично, каким ВИДОМ операция закончит —
        новой записью или перепроверкой. Это индексы уликовых связей, бюджеты новых
        вопросов, пустые/слишком длинные search_statements и дубли dependencies: ни одна из
        них не зависит от разрешённых перепроверок, подмены типа якоря и гейта дублей по
        значению, поэтому они продолжают отказывать всё предложение первыми (ADR-0032 §4:
        бюджет — свойство предложения целиком, выбрасывать операции по частям тут нельзя).

        Порядок строк — тот же, что у `validate_against`, минус строка про `as_of`."""
        return self._walk(evidence_count, questions_max, check_as_of=False)

    def temporal_as_of_problems(self, as_of_exempt: AbstractSet[int] = frozenset()) -> list[str]:
        """T7.84 (часть B): проверка опорной даты `temporal_fact` как отдельный шаг — после
        разрешения перепроверок (ADR-0018), разрешения `relied_claim_ids` (ADR-0032) и гейта
        дублей по значению (ADR-0033).

        Причина отдельного шага та же, что у T7.73 (ловушка AGENTS §7): проверка зависит от
        вида операции, значит обязана идти ПОСЛЕ того, как хост этот вид определил. Операция,
        которую гейт дублей превратил в перепроверку (`action: "reverified"`), передаётся в
        `as_of_exempt`: дата якоря принадлежит `packages/memory/reverify.py`, а не модели.
        Операция, оставшаяся новой и датless, называется той же строкой, что раньше, — политика
        «temporal_fact без даты красит всё предложение» не меняется."""
        problems: list[str] = []
        for i, claim in enumerate(self.claims):
            if self._needs_reference_date(claim, index=i, exempt=as_of_exempt):
                problems.append(f"claim[{i}]: temporal_fact requires as_of")
        return problems

    @staticmethod
    def _needs_reference_date(
        claim: ClaimProposal, *, index: int, exempt: AbstractSet[int]
    ) -> bool:
        """Опорная дата требуется, если это `temporal_fact` без даты и операция НЕ перепроверка.

        T7.73 (ADR-0018 уточнение): перепроверка существующего claim может не приносить дату —
        хост сохраняет якорную (`packages/memory/reverify.py`). Прежняя ловушка: проверка
        шла ДО подмены типа якоря и DO гейта дублей, поэтому датless-перепроверка гарантированно
        давала `as_of_missing`. `exempt` — индексы операций, которые хост уже ведёт как
        перепроверку (явный `existing_claim_id` модели или конверсия гейта)."""
        return (
            claim.claim_type is ClaimType.TEMPORAL_FACT
            and claim.as_of is None
            and claim.existing_claim_id is None
            and index not in exempt
        )

    def _walk(self, evidence_count: int, questions_max: int, *, check_as_of: bool) -> list[str]:
        """Один обход структурных проверок — общий для `validate_against` и
        `structural_problems`, чтобы порядок и формулировки строк не разъезжались."""
        problems: list[str] = []
        for i, link in enumerate(self.evidence_links):
            if link.evidence_index >= evidence_count:
                problems.append(f"evidence_link[{i}]: index {link.evidence_index} out of range")
            if link.claim_index >= len(self.claims):
                problems.append(f"evidence_link[{i}]: claim index {link.claim_index} out of range")
        for i, claim in enumerate(self.claims):
            if check_as_of and self._needs_reference_date(claim, index=i, exempt=frozenset()):
                problems.append(f"claim[{i}]: temporal_fact requires as_of")
            for k, alt in enumerate(claim.search_statements):
                if not alt.strip():
                    problems.append(f"claim[{i}].search_statements[{k}]: empty")
                elif len(alt) > 300:
                    problems.append(f"claim[{i}].search_statements[{k}]: > 300 chars")
            seen: set[tuple[str, str]] = set()
            for j, dep in enumerate(claim.dependencies):
                key = (str(dep.claim_id), dep.kind.value)
                if key in seen:
                    problems.append(f"claim[{i}].dependencies[{j}]: duplicate {key[0][:8]}")
                seen.add(key)
        if len(self.new_questions) > questions_max:
            problems.append(f"new_questions: {len(self.new_questions)} > {questions_max}")
        return problems
