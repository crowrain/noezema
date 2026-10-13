"""T7.106 (часть б): unit-тесты раздела исправления куратора (ADR-0034 вариант C).

`build_correction_section` — чистый модуль apps/orchestrator/curator_correction.py:
он детерминирован, ограничен константой `CORRECTION_SECTION_MAX_CHARS` и несёт только
причины хоста и собственные данные модели (никакого внешнего текста). Проверяются:
состав раздела, индексы claims с нуля, дословность причин, резка ЦЕЛЫМИ записями с
названным пропущенным количеством, пригодность JSON'а после резки, маскирование NUL
и ограниченность длины. Сценарная сторона (раунд на одном вызове, ключи журнала) —
tests/scenario/test_curator_correction_round_t7106.py.
"""

from __future__ import annotations

import dataclasses
import json

import pytest

from apps.orchestrator.curator_correction import (
    CORRECTION_CLAIM_SNIPPET_CHARS,
    CORRECTION_SECTION_MAX_CHARS,
    BoundaryAccepted,
    BoundaryRejected,
    build_correction_section,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

pytestmark = pytest.mark.unit

_JSON_MARKER = "Отклонённое предложение (JSON, как ты его вернул):\n"
_CLAIMS_MARKER = "\nClaims отклонённого предложения (всего "


def _q(text: str) -> dict[str, str]:
    return {"text": text, "origin": "model_proposal"}


def _small_proposal() -> CuratorProposal:
    """Форма стендового случая 3701d1ce (без реальных страниц): 4 claims, из них два
    temporal_fact с as_of; связь evidence_link[3] называет claim_index 4 вне списка."""

    return CuratorProposal(
        summary="Официальные данные и оценки рынка труда.",
        claims=[
            ClaimProposal(
                statement=(
                    "Уровень безработицы в Тармении в августе 2026 года составил 2,2% "
                    "(по данным службы занятости)."
                ),
                claim_type="temporal_fact",
                as_of="2026-09-30T00:00:00+00:00",
            ),
            ClaimProposal(
                statement=(
                    "Уровень безработицы в Тармении в июле 2026 года составил 2,3% "
                    "(по данным службы занятости)."
                ),
                claim_type="temporal_fact",
                as_of="2026-09-02T00:00:00+00:00",
            ),
            ClaimProposal(
                statement=(
                    "Сервис «Веер»: конкуренция среди соискателей в Тармении в апреле 2026 года "
                    "составила 10,3 резюме на одну вакансию (при норме до 8)."
                ),
                claim_type="external_fact",
            ),
            ClaimProposal(
                statement=(
                    "Аналитический обзор сервиса «Веер»: в 2026 году наблюдается снижение "
                    "количества вакансий на 25% год к году и рост числа резюме на 36% за 9 месяцев."
                ),
                claim_type="external_fact",
            ),
        ],
        evidence_links=[
            EvidenceLink(evidence_index=2, claim_index=1, relation="supports"),
            EvidenceLink(evidence_index=3, claim_index=2, relation="supports"),
            EvidenceLink(evidence_index=5, claim_index=3, relation="supports"),
            EvidenceLink(evidence_index=4, claim_index=4, relation="supports"),
        ],
        new_questions=[_q("Что изменилось к июлю?"), _q("Кто публикует норму конкуренции?")],
    )


_REASON = "evidence_link[3]: claim index 4 out of range"


def _json_slice(section: str) -> dict[str, object]:
    start = section.index(_JSON_MARKER) + len(_JSON_MARKER)
    end = section.index(_CLAIMS_MARKER)
    return dict(json.loads(section[start:end]))


# ── состав и семантика ────────────────────────────────────────────────

def test_section_carries_reasons_verbatim_claims_with_zero_based_indices() -> None:
    section = build_correction_section([_REASON], _small_proposal())

    assert section.startswith("# Исправление предложения")
    assert "Хост отклонил твоё предложение ЦЕЛИКОМ" in section
    assert "Верни ПОЛНОЕ предложение заново" in section
    assert "Индексы claims и evidence — с нуля" in section
    assert "Ничего не добавляй ради исправления" in section

    # причины — дословно, одной строкой с маркером списка
    assert f"Причины хоста (дословно):\n- {_REASON}\n" in section

    # перечень claims: индексы с нуля, кавычки-«ёлочки», число claims названо
    assert f"{_CLAIMS_MARKER}4), индексы с нуля:\n" in section
    list_lines = [line for line in section.splitlines() if line.startswith("[")]
    assert len(list_lines) == 4
    assert list_lines[0].startswith('[0] «Уровень безработицы в Тармении в августе 2026 года')
    assert list_lines[3].startswith('[3] «Аналитический обзор сервиса «Веер»')

    # JSON — то, что вернула сама модель: индексы ПЕРВОГО ответа сохранены дословно
    payload = _json_slice(section)
    assert payload == _small_proposal().model_dump(mode="json")


def test_section_is_deterministic() -> None:
    proposal = _small_proposal()
    reasons = [_REASON, "claim[1]: temporal_fact requires as_of"]
    first = build_correction_section(reasons, proposal)
    second = build_correction_section(reasons, _small_proposal())
    assert first == second


def test_claim_snippets_are_capped_and_named() -> None:
    long_statement = "Утверждение «Веер»: " + "значение показателя повторяется " * 30
    proposal = CuratorProposal(
        summary="Длинный случай.",
        claims=[ClaimProposal(statement=long_statement, claim_type="external_fact")],
        evidence_links=[EvidenceLink(evidence_index=0, claim_index=0, relation="supports")],
        new_questions=[],
    )
    section = build_correction_section(["claim[0]: длинная причина"], proposal)
    line = next(ln for ln in section.splitlines() if ln.startswith("[0] "))
    inner = line[len("[0] «") : -len("»")]
    assert len(inner) <= CORRECTION_CLAIM_SNIPPET_CHARS
    assert inner.endswith("…")


# ── потолок длины и резка целыми записями ─────────────────────────────

def _hostile() -> tuple[list[str], CuratorProposal]:
    claims = [
        ClaimProposal(
            statement=f"Утверждение номер {i}. " + "с" * 1970,
            claim_type="external_fact",
        )
        for i in range(12)
    ]
    links = [
        EvidenceLink(evidence_index=i % 9, claim_index=i % 12, relation="supports")
        for i in range(40)
    ]
    questions = [_q("Вопрос-заполнитель " * 40) for _ in range(10)]
    reasons = [f"причина {i}: длинная строка причины " + "х" * 300 for i in range(10)]
    return reasons, CuratorProposal(
        summary="С" * 1500, claims=claims, evidence_links=links, new_questions=questions
    )


def test_hostile_proposal_stays_under_the_cap_and_parses() -> None:
    reasons, proposal = _hostile()
    section = build_correction_section(reasons, proposal)

    assert len(section) <= CORRECTION_SECTION_MAX_CHARS

    # JSON остаётся пригодным; пропущенное количество НАЗВАНО ключами самого JSON
    payload = _json_slice(section)
    assert payload["omitted_claims"] >= 1
    assert len(payload["claims"]) < 12

    # пропущенное названо и в перечне claims
    assert "не показано из-за ограничения длины" in section or "опущены из JSON" in section


def test_reasons_are_cut_by_whole_lines_and_counted() -> None:
    reasons = [f"r{i} " + "д" * 100 for i in range(40)]
    proposal = CuratorProposal(
        summary="Короткое.",
        claims=[ClaimProposal(statement="короткое утверждение", claim_type="external_fact")],
        evidence_links=[],
        new_questions=[],
    )
    section = build_correction_section(reasons, proposal)
    shown = [line for line in section.splitlines() if line.startswith("- r")]
    assert 0 < len(shown) < 40
    omitted = 40 - len(shown)
    assert f"…ещё {omitted} причин не показано из-за ограничения длины" in section
    # дословность показанных строк сохранена полностью (никто не резан посередине)
    assert all(line == "- " + reasons[int(line[3:].split(" ")[0])] for line in shown)


def test_section_carries_no_foreign_text_and_no_raw_nul() -> None:
    reasons, proposal = _hostile()
    proposal = proposal.model_copy(
        update={
            "claims": [
                c.model_copy(update={"statement": c.statement.replace("Утверждение", "Утвер\x00ждение")})
                for c in proposal.claims
            ]
        }
    )
    section = build_correction_section([*reasons, "причина с NUL\x00 внутри"], proposal)

    assert "\x00" not in section
    # раздел не несёт чужого текста: ни URL, ни fence-разметок страниц — только
    # причины хоста и собственные данные модели
    assert "http" not in section
    json.loads(json.dumps(_json_slice(section)))


# ── контракт исходов границы ───────────────────────────────────────────

def test_boundary_outcomes_are_frozen_contracts() -> None:
    proposal = _small_proposal()
    rejected = BoundaryRejected(
        payload={"curator_rejected": [_REASON]},
        public_summary="curator proposal rejected by host validation",
        reasons=[_REASON],
    )
    accepted = BoundaryAccepted(
        staged_claims=list(proposal.claims),
        dropped={},
        value_duplicates=[],
        relied_claim_ids=[],
        relied_claims_rejected=[],
    )
    assert rejected.reasons == [_REASON]
    assert accepted.staged_claims and accepted.dropped == {}
    with pytest.raises(dataclasses.FrozenInstanceError):
        rejected.payload = {}  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        accepted.staged_claims = []  # type: ignore[misc]
