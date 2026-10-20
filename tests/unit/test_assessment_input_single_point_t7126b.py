"""Unit guard T7.126b (дополнение ADR-0039): ВХОД оценки собирается в ОДНОЙ точке.

Дефект разбора стенда `.92` (утверждение `83e7a1c8`): предикат «нет действующего отзыва связи»
стоял на ВХОДЕ группировщика и на ВХОДЕ рабочего переоценки, но НЕ стоял на ВХОДЕ оценки
предпросмотра независимости и на ВХОДЕ оценки при коммите сессии. Отозванная связь исключена из
групп, поэтому в `evaluate` получала резервную группу `UNTRACKED_GROUP` — вторую «независимую»
группу — и подъём оценки (E1 hypothesis 0.15 → E3 supported 0.75).

Этот тест закрепляет решение: одну общую точку сборки улик утверждения для оценки
(`claim_evidence_for_assessment`, `packages/memory/source_graph.py`) во всех четырёх входах,
ОДНУ копию предиката, отсутствие фильтра в движке правил и в алгоритме группировки, и то, что
дедуп улик по `identity_hash` (T7.78) НЕ фильтруется — иначе повторное предложение отозванной
связи создало бы вторую строку `evidence` и обошло бы отзыв оператора.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]

#: модули, где оценка утверждения получает свои улики: (путь, сколько раз вызывается общая точка)
ASSESSMENT_INPUT_SITES: dict[str, int] = {
    # коммит сессии: `all_evidence` для `_assess`
    "packages/memory/service.py": 1,
    # предпросмотр независимости: вход оценки «стало»
    "packages/memory/independence_preview.py": 1,
    # рабочий батч переоценки: вход `evaluate`
    "packages/memory/reassessment.py": 1,
    # offline-кандидат правил: вход `evaluate` теневых голов
    "hostctl/offline_rules.py": 1,
}

#: единственное место вне общей точки, где отзыв читается hand-written SQL-предикатом: витринный
#: показ строки отзыва (ADR-0039 §6, T7.126 часть в). Показ — не вход оценки.
DISPLAY_ONLY_PREDICATE_FILES = {"apps/web/knowledge.py"}


#: CHECK колонки отмены (`packages/domain/models/memory.py`, миграция 0026): свойство строки
#: отзыва, а не фильтр входа.
STRUCTURAL_PREDICATE_FILES = {"packages/domain/models/memory.py"}


def _text(relpath: str) -> str:
    return (REPO_ROOT / relpath).read_text(encoding="utf-8")


@pytest.mark.parametrize(("relpath", "expected"), sorted(ASSESSMENT_INPUT_SITES.items()))
def test_assessment_input_comes_from_the_shared_point(relpath: str, expected: int) -> None:
    text = _text(relpath)
    assert text.count("claim_evidence_for_assessment(") == expected, relpath
    # сборка входа не обращается к таблице отзывов сама и не заводит вторую копию предиката
    for forbidden in ("FROM evidence_link_revocations", "active_link_revocation_exclusion"):
        code = "\n".join(
            line for line in text.splitlines() if not line.lstrip().startswith("#")
        )
        assert forbidden not in code, (relpath, forbidden)


def test_predicate_has_exactly_one_definition_and_one_sql_copy() -> None:
    definitions: list[str] = []
    hand_written: list[str] = []
    production_dirs = ("packages", "apps", "hostctl")
    for sub in production_dirs:
        for path in sorted((REPO_ROOT / sub).rglob("*.py")):
            rel = path.relative_to(REPO_ROOT).as_posix()
            text = path.read_text(encoding="utf-8")
            if "def active_link_revocation_exclusion" in text:
                definitions.append(rel)
            if rel == "packages/memory/source_graph.py":
                continue
            code = "\n".join(
                line for line in text.splitlines() if not line.lstrip().startswith("#")
            )
            if "cancelled_at IS NULL" in code:
                hand_written.append(rel)
    assert definitions == ["packages/memory/source_graph.py"], definitions
    # CHECK колонки отмены живёт в модели (`packages/domain/models/memory.py`) и в миграции 0026 —
    # это свойство строки отзыва, а не фильтр входа; здесь оно не считается копией предиката
    assert set(hand_written) <= (
        DISPLAY_ONLY_PREDICATE_FILES | STRUCTURAL_PREDICATE_FILES
    ), hand_written


def test_shared_point_filters_only_active_revocations_of_that_claim() -> None:
    text = _text("packages/memory/source_graph.py")
    start = text.index("async def claim_evidence_for_assessment(")
    body = text[start : text.index("async def ", start + 10)]
    assert 'select(ORMEvidence)' in body
    assert "ORMEvidence.claim_id == claim_id" in body
    assert 'active_link_revocation_exclusion("evidence.id")' in body
    # детерминированный порядок: один и тот же вход даёт один и тот же evidence_set_hash
    assert ".order_by(ORMEvidence.id)" in body
    # фильтр только ВХОД: пороги, правила и группировка в этом модуле не тронуты
    assert "min_independence_groups" not in body


def test_rules_engine_and_grouping_do_not_know_about_revocations() -> None:
    for relpath in ("packages/memory/rules_engine.py", "packages/memory/independence.py"):
        text = _text(relpath)
        assert "evidence_link_revocations" not in text, relpath
        assert "claim_evidence_for_assessment" not in text, relpath


def test_evidence_dedup_lookup_stays_unfiltered() -> None:
    """Дедуп T7.78 (та же `identity_hash` той же улики того же утверждения) обязан видеть и
    отозванные строки: иначе повторное предложение создало БЫ ВТОРУЮ строку `evidence` без
    отзыва — обход решения оператора (закрыто сценарным тестом т4 T7.126b)."""

    text = _text("packages/memory/service.py")
    assert text.count("ORMEvidence.identity_hash == identity") == 1
    start = text.index("ORMEvidence.identity_hash == identity")
    window = text[max(0, start - 900) : start + 400]
    assert "revocation" not in window, window
