"""T7.85 (ADR-0033, дополнение): побайтово та же формулировка при другом `claim_type`.

Стендовая форма (сессия a09977e1, .92): одно и то же предложение записано дважды — как
`external_fact` 2d010d53 и как `temporal_fact` 58e2d5d4. Числовой гейт T7.83/T7.83a требует **того же
типа**, поэтому такую пару он не видит вовсе, а побайтовый дедуп T7.9 требует ещё и того же типа —
рядом с готовым утверждением появляется второе, с другой оценкой и другим бейджем на одной карточке.

Правило хоста (чистый детектор, применяется в `_curator`): операция без `existing_claim_id`, чья
формулировка побайтово совпадает ровно с одним утверждением контекст-пака, но тип другой, — это ТО ЖЕ
утверждение; она конвертируется в его перепроверку и оценивается по типу якоря. Ограничения по связям
те же, что у числового гейта: улика-опровержение не пристёгивается к чужой записи, без поддерживающей
улики кандидату нечего добавить. Два кандидата с тем же текстом — решение остаётся куратору.

Отдельно закреплено: фильтр «кандидат обязан быть объявлен в `relied_claim_ids`» (T7.83a) здесь НЕ нужен —
при побайтовом совпадении значение, период и показатель совпадают по построению, выбирать между двумя
разными формулировками хосту не из чего. Побайтовое совпадение при том же типе — территория дедупа T7.9:
детектор молчит.
"""

from __future__ import annotations

import pytest

from apps.orchestrator.value_duplicate import (
    ValueDuplicateCandidate,
    ValueDuplicateClaim,
    find_identical_statement_duplicates,
    find_value_duplicates,
)

pytestmark = pytest.mark.unit

# фикстурный текст стенда .92 (2d010d53 / 58e2d5d4), побайтово один и тот же
ANCHOR_TEXT = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по данным декабрьского "
    "макроэкономического опроса Банка России составил 6,3%."
)
#: то же утверждение другими словами: побайтового совпадения нет — это территория числового гейта
PARAPHRASE = (
    "Прогноз аналитиков по годовой инфляции в России на конец 2025 года по декабрьскому опросу "
    "Банка России — 6,3%."
)


def _op(
    statement: str = ANCHOR_TEXT,
    claim_type: str = "temporal_fact",
    *,
    index: int = 0,
    supports: bool = True,
    counters: bool = False,
) -> ValueDuplicateClaim:
    return ValueDuplicateClaim(
        index=index,
        statement=statement,
        claim_type=claim_type,
        as_of_year=2025,
        has_support_link=supports,
        has_counter_link=counters,
    )


def _anchor(
    claim_id: str = "2d010d53-0e98-4a48-bc47-98cd152f6ca5",
    statement: str = ANCHOR_TEXT,
    claim_type: str = "external_fact",
    *,
    relied: bool = False,
) -> ValueDuplicateCandidate:
    return ValueDuplicateCandidate(
        claim_id=claim_id,
        statement=statement,
        claim_type=claim_type,
        as_of_year=2026,
        relied=relied,
    )


# ── главный случай: тот же текст, другой тип → перепроверка якоря ─────────────────


def test_identical_statement_with_other_type_is_reverify_of_that_claim() -> None:
    decisions = find_identical_statement_duplicates([_op()], [_anchor()])

    assert len(decisions) == 1
    entry = decisions[0]
    assert set(entry) == {"claim_index", "target", "action", "reason"}
    assert entry["claim_index"] == 0
    assert entry["action"] == "reverified"
    assert entry["target"] == "2d010d53-0e98-4a48-bc47-98cd152f6ca5"
    assert entry["reason"].startswith("same statement, type relabelled temporal_fact → external_fact")


def test_reliance_is_not_required_for_a_byte_identical_statement() -> None:
    """Фильтр T7.83a здесь лишний: выбирать между формулировками не из чего."""

    with_reliance = find_identical_statement_duplicates([_op()], [_anchor(relied=True)])
    without = find_identical_statement_duplicates([_op()], [_anchor(relied=False)])
    assert with_reliance == without
    assert without[0]["action"] == "reverified"


def test_numeric_gate_is_silent_on_this_pair_and_the_rules_are_complementary() -> None:
    """Числовой гейт требует того же типа — на стендовой паре он молчит, решает побайтовое правило."""

    claim = _op()
    candidate = _anchor()
    assert find_value_duplicates([claim], [candidate]) == []
    assert find_identical_statement_duplicates([claim], [candidate]) != []


# ── границы: молчание там, где решение не хостовое ────────────────────────────────


def test_same_text_and_same_type_is_left_to_dedup_t79() -> None:
    decisions = find_identical_statement_duplicates(
        [_op(claim_type="external_fact")], [_anchor(claim_type="external_fact")]
    )
    assert decisions == []


def test_different_wording_is_not_this_rules_territory() -> None:
    assert find_identical_statement_duplicates([_op(statement=PARAPHRASE)], [_anchor()]) == []


def test_empty_statement_is_silent() -> None:
    assert find_identical_statement_duplicates([_op(statement="")], [_anchor()]) == []


# ── два кандидата с тем же текстом: «тот же самый» решает куратор, не хост ────────


def test_two_candidates_with_the_same_text_are_kept_as_proposed() -> None:
    decisions = find_identical_statement_duplicates(
        [_op()],
        [
            _anchor(claim_id="aaaaaaaa-0000-0000-0000-000000000001", claim_type="external_fact"),
            _anchor(claim_id="bbbbbbbb-0000-0000-0000-000000000002", claim_type="dispute"),
        ],
    )

    assert len(decisions) == 1
    entry = decisions[0]
    assert entry["action"] == "kept" and entry["target"] is None
    assert "byte-identical to 2 context-pack claims of another type" in entry["reason"]
    for claim_id in ("aaaaaaaa-0000-0000-0000-000000000001", "bbbbbbbb-0000-0000-0000-000000000002"):
        assert claim_id in entry["reason"]


def test_candidate_of_the_same_type_blocks_a_guess_about_another_one() -> None:
    """Побайтовое совпадение с утверждением того же типа ведёт дедуп T7.9 — хост не подменяет его."""

    decisions = find_identical_statement_duplicates(
        [_op(claim_type="temporal_fact")],
        [
            _anchor(claim_id="aaaaaaaa-0000-0000-0000-000000000001", claim_type="temporal_fact"),
            _anchor(claim_id="bbbbbbbb-0000-0000-0000-000000000002", claim_type="external_fact"),
        ],
    )
    assert decisions == []


# ── связи: те же ограничения, что у числового гейта ───────────────────────────────


def test_counterevidence_link_is_not_attached_to_someone_elses_claim() -> None:
    decisions = find_identical_statement_duplicates([_op(counters=True)], [_anchor()])

    assert len(decisions) == 1 and decisions[0]["action"] == "kept" and decisions[0]["target"] is None
    assert "a dispute stays a dispute" in decisions[0]["reason"]


def test_op_without_supporting_evidence_adds_nothing_to_the_candidate() -> None:
    decisions = find_identical_statement_duplicates([_op(supports=False, counters=True)], [_anchor()])

    assert len(decisions) == 1 and decisions[0]["action"] == "kept" and decisions[0]["target"] is None
    assert decisions[0]["reason"].startswith(
        "byte-identical statement of 2d010d53-0e98-4a48-bc47-98cd152f6ca5, but the op carries"
    )

    without_evidence = find_identical_statement_duplicates([_op(supports=False)], [_anchor()])
    assert without_evidence[0]["action"] == "kept"
    assert "nothing new to add to the candidate" in without_evidence[0]["reason"]


# ── порядок и несколько операций одного предложения ───────────────────────────────


def test_decisions_follow_the_proposal_order() -> None:
    decisions = find_identical_statement_duplicates(
        [
            _op(statement=PARAPHRASE, index=0),
            _op(index=1),
            _op(statement="Совсем другое утверждение про 7,7%.", index=2, claim_type="external_fact"),
        ],
        [_anchor()],
    )

    assert [entry["claim_index"] for entry in decisions] == [1]


def test_candidates_are_ordered_by_id_so_the_reason_is_deterministic() -> None:
    first = find_identical_statement_duplicates(
        [_op()],
        [
            _anchor(claim_id="zzzzzzzz-0000-0000-0000-000000000009"),
            _anchor(claim_id="aaaaaaaa-0000-0000-0000-000000000001"),
        ],
    )
    second = find_identical_statement_duplicates(
        [_op()],
        [
            _anchor(claim_id="aaaaaaaa-0000-0000-0000-000000000001"),
            _anchor(claim_id="zzzzzzzz-0000-0000-0000-000000000009"),
        ],
    )
    assert first == second
    assert first[0]["action"] == "kept" and first[0]["target"] is None
    # порядок id в причине не зависит от порядка кандидатов: перечисление детерминировано
    reason = first[0]["reason"]
    assert reason.index("aaaaaaaa-0000-0000-0000-000000000001") < reason.index(
        "zzzzzzzz-0000-0000-0000-000000000009"
    )
