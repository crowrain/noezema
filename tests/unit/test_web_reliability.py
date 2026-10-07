"""Unit: представление надёжности ответа (T7.64, ADR-0026).

Presentation-слой не оценивает знание: он переводит уже вычисленные rules
engine статус и уровень в понятную шкалу. Здесь проверяются инварианты этой
переводки — в том числе честность: чего нет в данных, того нет и в подписи
(ADR-0026). Пороги типов сверяются с payload'ом действующих правил, чтобы
экран не начал «помнить» другие пороги.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.web.reliability import (
    LEVEL_DEFERRED,
    LEVEL_DISPUTED,
    LEVEL_REFUTED,
    LEVEL_UNVERIFIED,
    LEVEL_VERIFIED,
    LEVEL_WEAK,
    NO_DETAILS,
    RELIABILITY_LEVELS,
    TYPE_MIN_GRADE,
    TYPE_REQUIREMENT,
    describe_verification,
    reliability,
)
from packages.domain.models.enums import ClaimType, EffectiveGrade, EpistemicStatus

pytestmark = pytest.mark.unit

CONFIG_PAYLOAD = (
    Path(__file__).resolve().parents[2] / "docs" / "eval" / "config-v13-payload.json"
)

GRADES = [g.value for g in EffectiveGrade]
STATUSES = [s.value for s in EpistemicStatus]
TYPES = [t.value for t in ClaimType]


def _levels() -> dict[str, str]:
    """level → color по всей шкале (подсказка всегда непустая)."""
    colors: dict[str, str] = {}
    for status in STATUSES:
        for grade in GRADES:
            out = reliability("external_fact", status, grade, head_state="current")
            colors[out["level"]] = out["color"]
            assert out["label"]
            assert out["hint"]
            assert set(out) == {"level", "label", "hint", "color"}
    return colors


# ─── матрица: тип × уровень × статус ──────────────────────────────────────


def test_verified_is_exactly_supported() -> None:
    for claim_type in TYPES:
        for grade in GRADES:
            level = reliability(claim_type, EpistemicStatus.SUPPORTED.value, grade)["level"]
            assert level == LEVEL_VERIFIED, (claim_type, grade, level)


def test_refuted_disputed_deferred_do_not_depend_on_grade() -> None:
    for status in (LEVEL_REFUTED, LEVEL_DISPUTED, LEVEL_DEFERRED):
        baseline = reliability("external_fact", status, "E0")
        for grade in GRADES:
            out = reliability("external_fact", status, grade)
            assert out["level"] == status, (status, grade, out["level"])
            # level не зависит от уровня подтверждения, и hint объясняет его всегда
            assert out["hint"] == baseline["hint"]


def test_weak_only_for_hypothesis_with_partial_support() -> None:
    """Жёлтый уровень — только предположение с частичными подтверждениями (E1–E2).

    Правила rules-v2 не выдают предположению E3/E4; если такая запись всё же
    пришла (повреждённая или устаревшая оценка), она НЕ показывается как
    «подтверждено слабо» — уровень остаётся «не проверено» с честным текстом.
    """
    for claim_type in TYPES:
        for grade in GRADES:
            out = reliability(claim_type, EpistemicStatus.HYPOTHESIS.value, grade)
            if grade in ("E1", "E2"):
                assert out["level"] == LEVEL_WEAK, (claim_type, grade)
            else:
                assert out["level"] == LEVEL_UNVERIFIED, (claim_type, grade, out["level"])
    anomaly = reliability("external_fact", EpistemicStatus.HYPOTHESIS.value, "E4")
    assert anomaly["level"] == LEVEL_UNVERIFIED
    assert "переоценк" in anomaly["hint"]


def test_weak_hint_names_what_is_missing_per_claim_type() -> None:
    """Подсказка обязана называть отсутствующее строго по порогу типа (§8.7)."""
    for claim_type in TYPES:
        threshold = TYPE_MIN_GRADE[claim_type]
        for grade in ("E1", "E2"):
            out = reliability(claim_type, EpistemicStatus.HYPOTHESIS.value, grade)
            assert out["level"] == LEVEL_WEAK
            assert TYPE_REQUIREMENT[claim_type].split(" (")[0] in out["hint"], out["hint"]
            assert threshold in out["hint"], (claim_type, grade, out["hint"])
            if _rank(grade) < _rank(threshold):
                assert f"ниже порога {threshold}" in out["hint"], out["hint"]


def test_hypothesis_without_support_is_not_called_weak() -> None:
    for claim_type in TYPES:
        out = reliability(claim_type, EpistemicStatus.HYPOTHESIS.value, EffectiveGrade.E0.value)
        assert out["level"] == LEVEL_UNVERIFIED
        assert "E0" in out["hint"]


def test_missing_assessment_fields_are_reported_honestly() -> None:
    """pending/invalid/never-assessed head: статус и уровень в БД NULL."""
    for head_state, fragment in (
        ("none", "Оценки нет"),
        ("pending", "ещё не стала"),
        ("invalid", "потеряла силу"),
    ):
        out = reliability("external_fact", None, None, head_state=head_state)
        assert out["level"] == LEVEL_UNVERIFIED
        assert fragment in out["hint"], out["hint"]
    # ни статуса, ни уровня, и head не опознан — честная заглушка, а не выдумка
    out = reliability("external_fact", None, None)
    assert out["level"] == LEVEL_UNVERIFIED
    assert out["hint"] == NO_DETAILS


def test_scale_is_stable_and_colors_are_logical_tokens() -> None:
    colors = _levels()
    assert set(RELIABILITY_LEVELS) <= set(colors)
    assert colors[LEVEL_VERIFIED] == "green"
    assert colors[LEVEL_WEAK] == "yellow"
    assert colors[LEVEL_UNVERIFIED] == "gray"
    assert colors[LEVEL_DISPUTED] == "orange"
    assert colors[LEVEL_REFUTED] == "red"
    assert colors[LEVEL_DEFERRED] == "gray"


def test_labels_of_the_scale_are_russian_and_distinct() -> None:
    seen: dict[str, str] = {}
    for status, grade in (
        (EpistemicStatus.SUPPORTED.value, "E3"),
        (EpistemicStatus.HYPOTHESIS.value, "E1"),
        (EpistemicStatus.HYPOTHESIS.value, "E0"),
        (EpistemicStatus.DISPUTED.value, "E1"),
        (EpistemicStatus.REFUTED.value, "E0"),
        (EpistemicStatus.DEFERRED.value, "E0"),
    ):
        out = reliability("external_fact", status, grade)
        seen[out["level"]] = out["label"]
    assert set(seen) == set(RELIABILITY_LEVELS)
    assert len(set(seen.values())) == len(RELIABILITY_LEVELS)
    for level in RELIABILITY_LEVELS:
        text = seen[level]
        assert text
        assert "§" not in text and "_" not in text


# ─── пороги берутся из правил, а не из памяти экрана ──────────────────────


def test_fallback_thresholds_match_the_rules_payload() -> None:
    """Запасная таблица модуля обязана совпадать с `claim_type_rules` правил."""
    payload: dict[str, Any] = json.loads(CONFIG_PAYLOAD.read_text(encoding="utf-8"))
    rules = payload["claim_type_rules"]
    assert set(rules) == set(TYPE_MIN_GRADE), "типы утверждений разъехались с правилами"
    for claim_type, expected in TYPE_MIN_GRADE.items():
        assert rules[claim_type]["min_grade_for_supported"] == expected, (
            claim_type,
            rules[claim_type]["min_grade_for_supported"],
            expected,
        )


def test_threshold_from_snapshot_wins_over_the_fallback() -> None:
    """Если вызывающий код передал порог из снапшота — используется он."""
    out = reliability(
        "computed_result",
        EpistemicStatus.HYPOTHESIS.value,
        "E2",
        min_grade_for_supported="E4",
    )
    assert out["level"] == LEVEL_WEAK
    assert "ниже порога E4" in out["hint"], out["hint"]
    # порог из снапшота может быть и ниже запасного: тогда фразы про порог нет
    out = reliability(
        "external_fact",
        EpistemicStatus.HYPOTHESIS.value,
        "E2",
        min_grade_for_supported="E2",
    )
    assert "ниже порога" not in out["hint"], out["hint"]


def test_unknown_claim_type_is_still_explained() -> None:
    out = reliability("some_future_type", EpistemicStatus.HYPOTHESIS.value, "E1")
    assert out["level"] == LEVEL_WEAK
    assert "подтверждений по правилам этого типа не хватает" in out["hint"]


# ─── «как проверено» ──────────────────────────────────────────────────────


def test_no_evidence_data_says_so_instead_of_inventing_a_check() -> None:
    assert describe_verification(None) == [NO_DETAILS]
    empty = describe_verification([])
    assert len(empty) == 1
    assert "подтверждений нет" in empty[0]


def test_computation_is_named_as_computation_with_its_artifact() -> None:
    phrases = describe_verification(
        [
            {"relation": "supports", "evidence_kind": "computation", "artifact_sha256": "abc"},
            {"relation": "supports", "evidence_kind": "computation"},
        ]
    )
    assert any("выполнено вычисление: 2" in p for p in phrases), phrases
    assert any("артефакт результата сохранён" in p for p in phrases), phrases
    # источников не было — экран не должен их упоминать
    assert not any("источник" in p for p in phrases), phrases


def test_one_source_versus_two_sources_and_independence_claims() -> None:
    single = describe_verification(
        [{"relation": "supports", "evidence_kind": "source_assertion", "source_uri": "https://a"}]
    )
    assert any("использован один источник" in p for p in single), single

    two_rows = [
        {"relation": "supports", "evidence_kind": "source_assertion", "source_uri": "https://a"},
        {"relation": "supports", "evidence_kind": "quote_integrity", "source_uri": "https://b"},
    ]
    without_groups = describe_verification(two_rows)
    assert any("источников прочитано: 2" in p for p in without_groups), without_groups
    # независимость не заявляется, пока группы источников не зафиксированы
    assert not any("независим" in p for p in without_groups), without_groups

    with_groups = describe_verification(
        two_rows,
        source_groups=[{"source_id": "1", "group_id": "g1"}, {"source_id": "2", "group_id": "g2"}],
    )
    assert any("разнесены по 2 независимым группам" in p for p in with_groups), with_groups


def test_independent_replication_is_named_only_from_environment_groups() -> None:
    rows = [
        {"relation": "supports", "evidence_kind": "experiment_run", "artifact_sha256": "x"},
        {"relation": "supports", "evidence_kind": "experiment_run", "artifact_sha256": "y"},
    ]
    plain = describe_verification(rows)
    assert any("проведён опыт: 2" in p for p in plain), plain
    assert not any("независим" in p for p in plain), plain

    grouped = describe_verification(
        rows,
        environment_groups=[
            {"environment_manifest_id": "1", "group_id": "e1"},
            {"environment_manifest_id": "2", "group_id": "e2"},
        ],
    )
    assert any("повторения получены независимо: 2 групп условий" in p for p in grouped), grouped


def test_counterevidence_is_visible_in_the_phrases() -> None:
    phrases = describe_verification(
        [
            {"relation": "supports", "evidence_kind": "computation"},
            {"relation": "counters", "evidence_kind": "source_assertion", "source_uri": "https://a"},
            {"relation": "counters", "evidence_kind": "local_observation"},
        ]
    )
    assert any("есть возражения: 2" in p for p in phrases), phrases


def test_quote_integrity_is_not_a_check_of_the_source_itself() -> None:
    phrases = describe_verification(
        [{"relation": "supports", "evidence_kind": "quote_integrity", "source_uri": "https://a"}]
    )
    assert any("цитата сверена с источником" in p for p in phrases), phrases
    # ни изоляции, ни независимого подтверждения экран не обещает
    assert not any("независим" in p for p in phrases), phrases
    assert not any("изолир" in p or "песочниц" in p for p in phrases), phrases


def test_unrecognized_evidence_rows_do_not_become_a_fake_check() -> None:
    assert describe_verification(
        [{"relation": "supports", "evidence_kind": "mystery_kind"}]
    ) == [NO_DETAILS]
    assert describe_verification([{"relation": None, "evidence_kind": None}]) == [NO_DETAILS]


def test_retelling_is_named_only_when_the_host_recorded_the_pointer() -> None:
    """T7.75 (ADR-0029 B): экран говорит о пересказе только если у источника есть указатель
    происхождения. Фраза не утверждает ни наличие, ни отсутствие независимости."""

    flat = [
        {
            "relation": "supports",
            "evidence_kind": "source_assertion",
            "canonical_uri": "https://a.example/news",
            "parent_uri": "https://rosstat.gov.ru/",
        },
        {
            "relation": "supports",
            "evidence_kind": "source_assertion",
            "canonical_uri": "https://b.example/news",
            "parent_uri": "https://rosstat.gov.ru/",
        },
    ]
    phrases = describe_verification(flat)
    assert any("источников прочитано: 2" in p for p in phrases), phrases
    assert any("часть прочитанного — пересказ первоисточника: 2" in p for p in phrases), phrases

    # форма provenance инженера — вложенный parent
    nested = [
        {
            "relation": "supports",
            "evidence_kind": "source_assertion",
            "source": {"canonical_uri": "https://a.example/news"},
            "parent": {"canonical_uri": "https://rosstat.gov.ru/"},
        }
    ]
    assert any("пересказ первоисточника: 1" in p for p in describe_verification(nested)), nested

    # указателя нет — фразы нет: presentation не додумывает происхождение
    plain = [{k: v for k, v in row.items() if k != "parent_uri"} for row in flat]
    assert not any("пересказ" in p for p in describe_verification(plain)), describe_verification(plain)


def _rank(grade: str) -> int:
    return GRADES.index(grade)
