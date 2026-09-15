"""Unit tests: repetition protection (§9, T5.4, stage 4).

Deterministic parts: config fail-closed semantics, the closed strategy
list and its rotation, the strategy notes.
"""

from __future__ import annotations

import pytest

from packages.cognition.repetition import (
    REPEAT_STRATEGIES,
    RepetitionConfig,
    RepetitionConfigError,
    choose_strategy,
    is_skip_strategy,
    strategy_context_note,
)

pytestmark = pytest.mark.unit


def test_defaults_match_bootstrap():
    cfg = RepetitionConfig.from_section(None)
    assert cfg.enabled is False  # NULL section = disabled (MVP unchanged)
    assert cfg.rephrase_threshold == 0.6
    assert cfg.plan_cycle_threshold == 0.5
    assert cfg.no_progress_limit == 2


def test_section_parsing():
    cfg = RepetitionConfig.from_section(
        {"enabled": True, "rephrase_threshold": 0.4, "plan_cycle_threshold": 0.7, "no_progress_limit": 3}
    )
    assert cfg.enabled is True
    assert cfg.rephrase_threshold == 0.4
    assert cfg.plan_cycle_threshold == 0.7
    assert cfg.no_progress_limit == 3


def test_invalid_sections_fail_closed():
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section({"enabled": "yes"})
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section({"rephrase_threshold": 1.5})
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section({"plan_cycle_threshold": -0.1})
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section({"no_progress_limit": 0})
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section({"no_progress_limit": 2.5})
    with pytest.raises(RepetitionConfigError):
        RepetitionConfig.from_section(["not", "a", "mapping"])


def test_closed_strategy_list_matches_spec_order():
    """The §9 cycle strategies, in the order the rotation follows."""
    assert REPEAT_STRATEGIES == (
        "compare_previous_session",
        "opposite_hypothesis",
        "change_source_type",
        "experiment",
        "defer_question",
        "choose_different_area",
    )


def test_strategy_rotation_is_deterministic_and_bounded():
    # below the limit → the first strategy
    assert choose_strategy(1, 2) == "compare_previous_session"
    # at the limit → first; each further no-progress session advances
    assert choose_strategy(2, 2) == "compare_previous_session"
    assert choose_strategy(3, 2) == "opposite_hypothesis"
    assert choose_strategy(4, 2) == "change_source_type"
    assert choose_strategy(5, 2) == "experiment"
    assert choose_strategy(6, 2) == "defer_question"
    assert choose_strategy(7, 2) == "choose_different_area"
    # wraps around the closed list (never an index error)
    assert choose_strategy(8, 2) == "compare_previous_session"
    assert choose_strategy(100, 1) in REPEAT_STRATEGIES


def test_skip_strategies():
    assert is_skip_strategy("defer_question")
    assert is_skip_strategy("choose_different_area")
    assert not is_skip_strategy("compare_previous_session")
    assert not is_skip_strategy("opposite_hypothesis")
    assert not is_skip_strategy("change_source_type")
    assert not is_skip_strategy("experiment")


def test_strategy_notes_are_host_generated():
    for strategy in (
        "compare_previous_session",
        "opposite_hypothesis",
        "change_source_type",
        "experiment",
    ):
        note = strategy_context_note(strategy, "Сколько будет 6*7?")
        assert "Цикл" in note
        assert "Сколько будет 6*7?" in note
    # skip strategies have no note (the selection itself is the effect)
    assert strategy_context_note("defer_question", "x") == ""
    assert strategy_context_note("choose_different_area", "x") == ""
