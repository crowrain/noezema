"""T7.106 (часть б) + T7.106a: unit-тесты раздела исправления куратора (ADR-0034 вариант C).

`build_correction_section` — чистый модуль apps/orchestrator/curator_correction.py:
он детерминирован, чист (ни БД, ни шлюза) и несёт только причины хоста и собственные
данные модели (никакого внешнего текста). Правило T7.106a (ADR-0034 §9): раздел
НИКОГДА не теряет операции и связи — JSON отклонённого предложения ПОЛНЫЙ (без omitted_*
счётчиков), перечень `[i]` — по ВСЕМ claims (короткие фрагменты); целыми строками с
маркером режутся только причины хоста. Если полный раздел не помещается в потолок
`CORRECTION_SECTION_MAX_CHARS`, построитель возвращает `CorrectionOverLimit` (с длиной),
а не урезанный текст — раунда исправления не будет (сценарная проверка:
tests/scenario/test_curator_correction_round_t7106.py, т8).
"""

from __future__ import annotations

import dataclasses
import json
import uuid

import pytest

from apps.orchestrator.curator_correction import (
    CORRECTION_CLAIM_SNIPPET_CHARS,
    CORRECTION_SECTION_MAX_CHARS,
    BoundaryAccepted,
    BoundaryRejected,
    CorrectionOverLimit,
    build_correction_section,
)
from packages.domain.schemas.staging import ClaimProposal, CuratorProposal, EvidenceLink

pytestmark = pytest.mark.unit

_JSON_MARKER = "Отклонённое предложение (JSON, как ты его вернул):\n"
_CLAIMS_MARKER = "\nClaims отклонённого предложения (всего "


def _q(text: str) -> dict[str, str]:
    return {"text": text, "origin": "model_proposal"}


def _fits(reasons: list[str], proposal: CuratorProposal) -> str:
    """Тот же вызов, что делает `_curator`: полный раздел обязан поместиться (иначе
    раунда вовсе не будет — это отдельный тест)."""
    section = build_correction_section(reasons, proposal)
    assert isinstance(section, str), "построитель сказал, что полный раздел не помещается"
    return section


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
    section = _fits([_REASON], _small_proposal())

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

    # T7.106a: раздел ничего не теряет — счётчиков опущенного нет ни в JSON, ни в перечне
    assert "omitted" not in section
    assert "ограничения длины" not in section


def test_section_is_deterministic() -> None:
    proposal = _small_proposal()
    reasons = [_REASON, "claim[1]: temporal_fact requires as_of"]
    first = _fits(reasons, proposal)
    second = _fits(reasons, _small_proposal())
    assert first == second


# ── T7.106a (т1): реалистичное предложение не теряет ни claims, ни связи ──────────

def _realistic_proposal() -> CuratorProposal:
    """8 claims с настоящими по форме scope (5–6 ключей), search_statements, as_of и
    dependencies — форма ответа куратора на стенде (curator-v14)."""

    dep_id_a = str(uuid.UUID("11111111-1111-4111-8111-111111111111"))
    dep_id_b = str(uuid.UUID("22222222-2222-4222-8222-222222222222"))
    scope_tmpl = {
        "metric": "уровень безработицы",
        "territory": "Тармения",
        "period": "2026-08",
        "units": "%",
        "source_type": "official",
        "adjustment": "с сезонной корректировкой",
    }
    claims = [
        ClaimProposal(
            statement=(
                f"Уровень безработицы в Тармении в {month} 2026 года составил {value}% "
                "(по данным службы занятости, с сезонной корректировкой)."
            ),
            claim_type="temporal_fact",
            scope={**scope_tmpl, "period": f"2026-{mm}"},
            search_statements=[
                f"Unemployment rate in Tarmenia in {month_en} 2026: {value} percent, "
                "seasonally adjusted, published by the employment service"
            ],
            as_of=f"2026-{mm}-28T00:00:00+00:00",
        )
        for month, mm, month_en, value in (("июле", "07", "July", "2,3"), ("августе", "08", "August", "2,2"))
    ]
    claims += [
        ClaimProposal(
            statement=(
                "Сервис «Веер»: конкуренция среди соискателей в Тармении в апреле 2026 года "
                "составила 10,3 резюме на одну вакансию при опубликованной норме до 8."
            ),
            claim_type="external_fact",
            scope={
                "metric": "резюме на вакансию",
                "territory": "Тармения",
                "period": "2026-04",
                "units": "ед./вакансия",
                "source_type": "third_party",
            },
            search_statements=[
                "Competition among job seekers in Tarmenia in April 2026: 10.3 resumes per vacancy"
            ],
            dependencies=[{"claim_id": dep_id_a, "kind": "evidential"}],
        ),
        ClaimProposal(
            statement=(
                "Аналитический обзор сервиса «Веер»: в 2026 году количество вакансий в Тармении "
                "снизилось на 25% год к году."
            ),
            claim_type="external_fact",
            scope={
                "metric": "динамика вакансий",
                "territory": "Тармения",
                "period": "2026",
                "units": "% год к году",
                "source_type": "third_party",
            },
            search_statements=["Job postings in Tarmenia fell by 25 percent year over year in 2026"],
            dependencies=[{"claim_id": dep_id_b, "kind": "evidential"}],
        ),
        ClaimProposal(
            statement=(
                "Обзор «Веер»: число резюме в Тармении выросло на 36% за 9 месяцев 2026 года."
            ),
            claim_type="external_fact",
            scope={
                "metric": "динамика резюме",
                "territory": "Тармения",
                "period": "9 месяцев 2026",
                "units": "%",
                "source_type": "third_party",
            },
            search_statements=["Resume submissions in Tarmenia rose by 36 percent over nine months"],
        ),
        ClaimProposal(
            statement=(
                "Газета «Тарменика»: независимый обзор оценок службы занятости и сервиса «Веер» "
                "подтверждает расхождение не более чем в полпроцентного пункта."
            ),
            claim_type="external_fact",
            scope={
                "metric": "расхождение оценок",
                "territory": "Тармения",
                "period": "2026",
                "units": "п.п.",
                "source_type": "media",
            },
        ),
        ClaimProposal(
            statement=(
                "Портал «Трудовики»: в Тармении за 9 месяцев 2026 года число резюме выросло на 36 "
                "процентов, вакансии сократились на 25 процентов."
            ),
            claim_type="external_fact",
            scope={
                "metric": "резюме и вакансии",
                "territory": "Тармения",
                "period": "9 месяцев 2026",
                "units": "%",
                "source_type": "third_party",
            },
            search_statements=["Tarmenia job market nine months 2026: resumes up 36 percent"],
        ),
        ClaimProposal(
            statement=(
                "Служба занятости Тармении публикует уровень безработицы ежемесячно; августовский "
                "выпуск от 30 сентября 2026 года пересмотру не подлежал."
            ),
            claim_type="external_fact",
            scope={
                "metric": "график публикации",
                "territory": "Тармения",
                "period": "ежемесячно",
                "source_type": "official",
            },
            as_of="2026-09-30T00:00:00+00:00",
        ),
    ]
    return CuratorProposal(
        summary="Официальные данные и независимые оценки рынка труда Тармении за 2026 год.",
        claims=claims,
        evidence_links=[
            EvidenceLink(evidence_index=i % 6, claim_index=i, relation="supports") for i in range(8)
        ]
        + [
            EvidenceLink(evidence_index=5, claim_index=1, relation="supports"),
            EvidenceLink(evidence_index=4, claim_index=5, relation="counters", note="частичное расхождение"),
            EvidenceLink(evidence_index=3, claim_index=6, relation="supports"),
            EvidenceLink(evidence_index=2, claim_index=7, relation="supports"),
        ],
        new_questions=[
            _q("Пересматривалась ли оценка июля при выходе августовского выпуска?"),
            _q("Кто устанавливает норму конкуренции 8 резюме на вакансию?"),
        ],
        relied_claim_ids=[dep_id_a, dep_id_b],
    )


def test_realistic_eight_claim_proposal_keeps_full_json_and_all_list_lines() -> None:
    """Т7.106a т1: реалистичное предложение (8 claims с настоящими scope, search_statements,
    as_of, dependencies) — раздел несёт ВСЕ восемь строк `[0]…[7]` и полный JSON;
    счётчиков omitted_* нет ни в JSON, ни в перечне."""
    proposal = _realistic_proposal()
    reasons = [_REASON, "claim 0 (temporal_fact): as_of 2026-07-28T00:00:00+00:00 старше окна вопроса"]
    section = _fits(reasons, proposal)

    assert len(section) <= CORRECTION_SECTION_MAX_CHARS

    list_lines = [line for line in section.splitlines() if line.startswith("[")]
    assert len(list_lines) == 8
    for i in range(8):
        assert list_lines[i].startswith(f"[{i}] «")

    # JSON — полный: каждый claim и каждая связь на месте, сериализация дословная
    payload = _json_slice(section)
    assert payload == proposal.model_dump(mode="json")
    assert len(payload["claims"]) == 8 and len(payload["evidence_links"]) == 12

    # T7.106a: резки нет ни в JSON, ни в перечне
    assert "omitted" not in section
    assert "ограничения длины" not in section


def test_claim_snippets_are_capped_and_named() -> None:
    long_statement = "Утверждение «Веер»: " + "значение показателя повторяется " * 30
    proposal = CuratorProposal(
        summary="Длинный случай.",
        claims=[ClaimProposal(statement=long_statement, claim_type="external_fact")],
        evidence_links=[EvidenceLink(evidence_index=0, claim_index=0, relation="supports")],
        new_questions=[],
    )
    section = _fits(["claim[0]: длинная причина"], proposal)
    line = next(ln for ln in section.splitlines() if ln.startswith("[0] "))
    inner = line[len("[0] «") : -len("»")]
    assert len(inner) <= CORRECTION_CLAIM_SNIPPET_CHARS
    assert inner.endswith("…")


# ── потолок длины: полный раздел не режется, а называет переполнение (T7.106a) ────

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


def test_full_hostile_section_over_cap_returns_over_limit_not_trimmed_text() -> None:
    """Т7.106a (т2, unit): полный раздел hostile-предложения не влезает в потолок —
    построитель возвращает `CorrectionOverLimit` (длину и потолок называет), а НЕ
    урезанный текст с omitted_*. Прежде (6000 знаков) этот случай резался целыми
    записями и терял claims из JSON и перечня."""
    reasons, proposal = _hostile()
    result = build_correction_section(reasons, proposal)

    assert isinstance(result, CorrectionOverLimit), "полный раздел обязан быть назван переполненным"
    assert result.limit == CORRECTION_SECTION_MAX_CHARS == 24_000  # потолок закреплен обоснованием ADR-0034 §9
    assert result.chars > result.limit

    # длина названа честно: это длина ПОЛНОГО раздела (целиком JSON + перечень по всем
    # claims), а не случайная цифра — нижняя и верхняя границы считаем без internals
    full_json_len = len(json.dumps(proposal.model_dump(mode="json"), ensure_ascii=False))
    assert result.chars > full_json_len  # JSON входит в раздел целиком, плюс прочие части
    assert result.chars < full_json_len + len(reasons) * 400 + 12 * 200 + 4_000

    # детерминизм и при переполнении: та же длина при том же входе
    again = build_correction_section(reasons, _hostile()[1])
    assert again == result


def test_reasons_are_cut_by_whole_lines_and_counted() -> None:
    """Причины хоста — единственная часть раздела, которую разрешено резать целыми
    строками с маркером (T7.106a, правило 1)."""
    reasons = [f"r{i} " + "д" * 100 for i in range(120)]
    proposal = CuratorProposal(
        summary="Короткое.",
        claims=[ClaimProposal(statement="короткое утверждение", claim_type="external_fact")],
        evidence_links=[],
        new_questions=[],
    )
    section = _fits(reasons, proposal)
    shown = [line for line in section.splitlines() if line.startswith("- r")]
    assert 0 < len(shown) < 120
    omitted = 120 - len(shown)
    assert f"…ещё {omitted} причин не показано из-за ограничения длины" in section
    # дословность показанных строк сохранена полностью (никто не резан посередине)
    assert all(line == "- " + reasons[int(line[3:].split(" ")[0])] for line in shown)


def _reasons_three() -> list[str]:
    return [f"причина {i}: средняя строка " + "х" * 40 for i in range(3)]


def test_section_carries_no_foreign_text_and_no_raw_nul() -> None:
    proposal = CuratorProposal(
        summary="Три claims с NUL внутри.",
        claims=[
            ClaimProposal(
                statement=("Утверждение " + "показатель значения "*24).replace("Утверждение", "Утвер\x00ждение"),
                claim_type="external_fact",
            )
            for _ in range(3)
        ],
        evidence_links=[EvidenceLink(evidence_index=i % 2, claim_index=i, relation="supports") for i in range(3)],
        new_questions=[],
    )
    section = _fits([*_reasons_three(), "причина с NUL\x00 внутри"], proposal)

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
