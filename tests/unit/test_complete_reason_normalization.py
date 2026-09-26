"""T7.49 (ADR-0022): host-side normalization of the model completion
reason. The host may derive SUCCESS only from an EXPLICIT CompleteReason
token at the start of the reason string — never from free text (a false
succeeded is worse than a false partial). Measurement: docs/eval/
SMOKE-V13-K2-report.md §6 (class B = token + separator + suffix)."""

from __future__ import annotations

import pytest

from packages.domain.models.enums import CompleteReason
from packages.domain.schemas.decision import normalize_complete_reason


@pytest.mark.unit
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # exact tokens (case-insensitive, trimmed)
        ("goal_reached", CompleteReason.GOAL_REACHED),
        ("GOAL_REACHED", CompleteReason.GOAL_REACHED),
        (" goal_reached ", CompleteReason.GOAL_REACHED),
        ("Goal_ReachEd", CompleteReason.GOAL_REACHED),
        ("budget_exhausted", CompleteReason.BUDGET_EXHAUSTED),
        ("BUDGET_EXHAUSTED", CompleteReason.BUDGET_EXHAUSTED),
        ("operator_stop", CompleteReason.OPERATOR_STOP),
        # surrounding quotes / backticks / period
        ("`goal_reached`", CompleteReason.GOAL_REACHED),
        ('"goal_reached"', CompleteReason.GOAL_REACHED),
        ("'goal_reached'", CompleteReason.GOAL_REACHED),
        ("goal_reached.", CompleteReason.GOAL_REACHED),
        ('"goal_reached."', CompleteReason.GOAL_REACHED),
        # token at the start + separator + suffix (SMOKE-V13-K2 0d0c1b3f)
        (
            "goal_reached — утверждение: последняя стабильная версия Go — Go 1.27.1",
            CompleteReason.GOAL_REACHED,
        ),
        # SMOKE-V8-K2 c79140b5 / SMOKE-V11-Halogen 1bcf6776
        (
            "goal_reached: По обоим заданным источникам последняя стабильная версия Python — 3.14.7",
            CompleteReason.GOAL_REACHED,
        ),
        ("goal_reached. Вопрос отвечен и подтверждён", CompleteReason.GOAL_REACHED),
        ("goal_reached (двумя источниками)", CompleteReason.GOAL_REACHED),
        ("goal_reached, подтверждено", CompleteReason.GOAL_REACHED),
        ("no_progress — два хода без результата", CompleteReason.NO_PROGRESS),
        ("blocked: нет доступа к источнику", CompleteReason.BLOCKED),
        ("budget_exhausted — 10 шагов", CompleteReason.BUDGET_EXHAUSTED),
    ],
)
def test_normalize_recognizes_explicit_token(raw: str, expected: CompleteReason) -> None:
    assert normalize_complete_reason(raw) is expected


@pytest.mark.unit
@pytest.mark.parametrize(
    "raw",
    [
        # token glued to a suffix / internal token / not at the start
        "goal_reachedX",
        "not goal_reached",
        "Вопрос отвечен и подтверждён (goal_reached)",
        "Вопрос отвечен и подтверждён двумя источниками",
        # empty / non-string
        "",
        None,
        "   ",
        # wrong spelling
        "goal reached",
        "goal_reached_partially",
        "goal_reached!",
        # free-form text (class D of the SMOKE measurement) — never success
        "Цель достигнута: по обоим указанным источникам",
        "Вопрос выполнен: notes/plan.md создан с тремя пунктами плана",
        "custom_host_reason",
    ],
)
def test_normalize_rejects_non_explicit(raw: object) -> None:
    assert normalize_complete_reason(raw) is None


@pytest.mark.unit
def test_first_token_wins() -> None:
    # token X at the start, another token mentioned in the suffix — X wins
    assert normalize_complete_reason("blocked — goal_reached не достигнут") is CompleteReason.BLOCKED
    assert normalize_complete_reason("no_progress; goal_reached") is CompleteReason.NO_PROGRESS


@pytest.mark.unit
def test_non_string_inputs() -> None:
    assert normalize_complete_reason(123) is None  # type: ignore[arg-type]
    assert normalize_complete_reason(["goal_reached"]) is None  # type: ignore[arg-type]
