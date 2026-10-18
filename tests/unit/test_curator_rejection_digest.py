"""T7.84 (часть A): дайджест ОТКЛОНЁННОГО кураторского предложения в журнале отказа.

Стендовый дефект (сессия ed36f4a0, .92, config-v20): куратор принёс два temporal_fact
без as_of, хост отклонил всё предложение — и то, ЧТО именно предложила модель, не
записано нигде: `claim_created` не пишется, сырой ответ модели в `model_runs` не
сохраняется. Отказ объясним («temporal_fact requires as_of»), но необъясним по
содержимому. Дайджест — компактный снимок отклонённого предложения в payload ТОГО ЖЕ
события отказа: ни нового AuditEventType, ни миграции, ни попадания текста модели в
улики или знание.

Проверяются границы (размер, обрезка с меткой, маскирование NUL) и то, что отказ
списка не обрывает запись на середине записи.
"""

from __future__ import annotations

import json
import uuid

import pytest

from apps.orchestrator.rejected_proposal import (
    REJECTED_PROPOSAL_MAX_CHARS,
    REJECTED_RELIED_LIMIT,
    REJECTED_STATEMENT_CHARS,
    REJECTED_SUMMARY_CHARS,
    rejected_proposal_digest,
)
from packages.domain.models.enums import ClaimType, EvidenceRelation, QuestionOrigin
from packages.domain.schemas.staging import (
    ClaimProposal,
    CuratorProposal,
    EvidenceLink,
    QuestionProposal,
)

pytestmark = pytest.mark.unit


def _proposal(*claims: ClaimProposal, links: int = 0, relied: list[str] | None = None) -> CuratorProposal:
    return CuratorProposal(
        summary="перепроверка двух записанных утверждений",
        claims=list(claims),
        evidence_links=[
            EvidenceLink(
                evidence_index=i, claim_index=i % max(len(claims), 1), relation=EvidenceRelation.SUPPORTS
            )
            for i in range(links)
        ],
        relied_claim_ids=list(relied or []),
    )


def test_digest_names_every_claim_op_and_its_identity() -> None:
    """Каждая операция claim представлена отдельной записью со всеми полями, по которым
    отказ можно опознать: формулировка, тип, дата, ссылка перепроверки, ключи scope."""
    anchor = str(uuid.uuid4())
    proposal = _proposal(
        ClaimProposal(
            statement="Официальная годовая инфляция 2025 года составила 5,59% (по данным Росстата).",
            claim_type=ClaimType.TEMPORAL_FACT,
            as_of=None,
            scope={"показатель": "инфляция", "period": "2025"},
        ),
        ClaimProposal(
            statement="Наблюдаемая населением инфляция в декабре 2025 года составила 14,5%.",
            claim_type=ClaimType.TEMPORAL_FACT,
            as_of=None,
            existing_claim_id=anchor,
            scope={},
        ),
        links=2,
        relied=[anchor],
    )
    digest = rejected_proposal_digest(proposal)

    assert sorted(digest) == [
        "claims",
        "evidence_links",
        "new_questions",
        "relied_claim_ids",
        "summary",
    ]
    claims = digest["claims"]
    assert isinstance(claims, list) and len(claims) == 2
    first, second = claims
    assert sorted(first) == [
        "as_of",
        "claim_index",
        "claim_type",
        "existing_claim_id",
        "scope_keys",
        "statement",
    ]
    assert first["claim_index"] == 0
    assert first["claim_type"] == "temporal_fact"
    assert first["as_of"] is None
    assert first["existing_claim_id"] is None
    assert first["scope_keys"] == ["period", "показатель"]  # порядок ключей детерминирован
    assert second["claim_index"] == 1
    assert second["existing_claim_id"] == anchor
    assert second["scope_keys"] == []

    assert digest["evidence_links"] == [
        {"claim_index": 0, "evidence_index": 0, "relation": "supports"},
        {"claim_index": 1, "evidence_index": 1, "relation": "supports"},
    ]
    assert digest["relied_claim_ids"] == [anchor]
    # число новых вопросов — ровно число, без текста вопроса (текст вопроса — отдельный бюджет)
    assert digest["new_questions"] == 0
    assert digest["summary"] == "перепроверка двух записанных утверждений"


def test_as_of_is_serialized_as_the_iso_string_the_model_declared() -> None:
    proposal = CuratorProposal(
        summary="s",
        claims=[
            ClaimProposal(
                statement="Инфляция 2025 года — 5,59%.",
                claim_type=ClaimType.TEMPORAL_FACT,
                as_of="2026-01-21T00:00:00+00:00",
            )
        ],
    )
    entry = rejected_proposal_digest(proposal)["claims"][0]
    assert entry["as_of"] == "2026-01-21T00:00:00+00:00"


def test_statement_and_summary_are_clipped_with_a_marker_not_cut_silently() -> None:
    long_text = "инфляция " * 400  # > REJECTED_STATEMENT_CHARS и > max_length схемы
    proposal = CuratorProposal(
        summary=long_text[:2000],
        claims=[ClaimProposal(statement=long_text[:2000], claim_type=ClaimType.EXTERNAL_FACT)],
    )
    digest = rejected_proposal_digest(proposal)
    statement = digest["claims"][0]["statement"]
    summary = digest["summary"]

    assert len(statement) == REJECTED_STATEMENT_CHARS
    assert statement.endswith("…")  # обрезка названа, а не прятана
    assert len(summary) == REJECTED_SUMMARY_CHARS
    assert summary.endswith("…")


def test_total_size_is_bounded_and_what_did_not_fit_is_counted_openly() -> None:
    """Предел — общий размер дайджеста, а не только длина одной строки. Не поместившееся
    снимается ЦЕЛЫМИ записями и называется числом (урок T7.76: усечённая посередине
    запись уже не запись)."""
    claims = [
        ClaimProposal(
            statement=f"инфляция региона {i} за 2025 год: {i},5% " + "д" * 1900,
            claim_type=ClaimType.EXTERNAL_FACT,
        )
        for i in range(40)
    ]
    proposal = CuratorProposal(
        summary="s" * 2000,
        claims=claims,
        evidence_links=[
            EvidenceLink(evidence_index=i, claim_index=i, relation=EvidenceRelation.SUPPORTS)
            for i in range(40)
        ],
        new_questions=[
            QuestionProposal(text="уточнить источник", origin=QuestionOrigin.MODEL_PROPOSAL)
        ]
        * 3,
    )
    digest = rejected_proposal_digest(proposal)
    size = len(json.dumps(digest, ensure_ascii=False))

    assert size <= REJECTED_PROPOSAL_MAX_CHARS
    assert digest["claims"], "хотя бы одна операция обязана быть видна"
    assert all(len(entry["statement"]) <= REJECTED_STATEMENT_CHARS for entry in digest["claims"])
    assert len(digest["claims"]) < len(claims)
    assert digest["omitted_claims"] == len(claims) - len(digest["claims"]) > 0
    assert digest["new_questions"] == 3


def test_relied_ids_are_capped_like_the_host_records_them() -> None:
    relied = [str(uuid.uuid4()) for _ in range(20)]
    digest = rejected_proposal_digest(_proposal(*[], relied=relied))
    assert len(digest["relied_claim_ids"]) == REJECTED_RELIED_LIMIT
    assert digest["relied_claim_ids"] == relied[:REJECTED_RELIED_LIMIT]


def test_empty_proposal_still_yields_a_digest() -> None:
    """Отказ по связям/бюджетам (операций нет) тоже обязан показать, что именно пришли
    хосту: пустые списки вместо отсутствия ключа."""
    proposal = CuratorProposal(
        summary="ничего не найдено",
        claims=[],
        evidence_links=[EvidenceLink(evidence_index=7, claim_index=0, relation=EvidenceRelation.COUNTERS)],
    )
    digest = rejected_proposal_digest(proposal)
    assert digest["claims"] == []
    assert digest["evidence_links"] == [
        {"claim_index": 0, "evidence_index": 7, "relation": "counters"}
    ]
    assert digest["new_questions"] == 0


def test_model_text_reaches_the_journal_masked_not_as_a_control_character() -> None:
    """Журнал не должен уметь уронить транзакцию отказом (T7.46a): NUL становится
    видимой меткой \\x00 — той же, что во всех остальных payload'ах."""
    proposal = CuratorProposal(
        summary="с\x00метка",
        claims=[ClaimProposal(statement="инфляция 5,59%\x00x", claim_type=ClaimType.EXTERNAL_FACT)],
    )
    digest = rejected_proposal_digest(proposal)
    text = json.dumps(digest, ensure_ascii=False)

    assert "\x00" not in text
    assert "\\x00" in digest["claims"][0]["statement"]
    assert "\\x00" in digest["summary"]


def test_digest_is_json_serializable_for_jsonb() -> None:
    """Payload события — JSONB: только строковые ключи и простые значения."""
    proposal = _proposal(
        ClaimProposal(
            statement="Ставка 13,7% в 2025", claim_type=ClaimType.TEMPORAL_FACT, scope={"объект": "ставка"}
        )
    )
    payload = {
        "curator_rejected": ["claim[0]: temporal_fact requires as_of"],
        "rejected_proposal": rejected_proposal_digest(proposal),
    }
    restored = json.loads(json.dumps(payload, ensure_ascii=False))
    assert restored["rejected_proposal"]["claims"][0]["scope_keys"] == ["объект"]
