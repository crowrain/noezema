"""Unit: the §22.2 gate comparison directions are a CLOSED set.

The direction of every ratio gate is one of exactly three values
("at_least" / "at_most" / "below", ARCHITECTURE.md:2602–2611). The set
is closed at the type level (``GateDirection`` Literal — mypy strict
rejects any other value statically) and at runtime: ``_gate()`` fails
with ``ValueError`` (gate name when available, plus the received
value) instead of silently treating an unknown value as a non-strict
"at_most" — the class of boundary mismatch fixed in T7.28 (ADR-0015:
"below" vs "at_most" at exactly 20%).
"""

from __future__ import annotations

import pytest

from packages.evaluation.gates import (
    _GATE_DIRECTION,
    GateDirection,
    _gate,
)

pytestmark = pytest.mark.unit

#: the closed set of the three §22.2 directions
KNOWN_DIRECTIONS = frozenset({"at_least", "at_most", "below"})


def test_gate_direction_literal_is_closed() -> None:
    # the type-level set is exactly the three known directions
    assert set(GateDirection.__args__) == KNOWN_DIRECTIONS


def test_gate_direction_values_are_all_known() -> None:
    # every value of _GATE_DIRECTION is one of the three known
    # directions — no free string, no silent default
    for gate_name, direction in _GATE_DIRECTION.items():
        assert direction in KNOWN_DIRECTIONS, gate_name
    # and all three directions are actually in use (spec «≥»/«≤»/«<»)
    assert set(_GATE_DIRECTION.values()) == KNOWN_DIRECTIONS


@pytest.mark.parametrize(
    "bad", ["below ", "at-least", "AT_LEAST", "at_most ", ""]
)
def test_gate_unknown_direction_raises_value_error(bad: str) -> None:
    # a misspelled/unknown direction fails closed with ValueError and
    # reports the received value — it must NOT silently become a
    # non-strict "at_most"
    with pytest.raises(ValueError) as excinfo:
        _gate(
            numerator=1,
            denominator=20,
            threshold=0.20,
            direction=bad,  # type: ignore[arg-type]
        )
    assert repr(bad) in str(excinfo.value)


def test_gate_unknown_direction_error_names_the_gate() -> None:
    # when the gate name is available (every real call site passes it)
    # the error names the gate AND the received value
    with pytest.raises(ValueError) as excinfo:
        _gate(
            numerator=6,
            denominator=30,
            threshold=0.20,
            direction="at-least",  # type: ignore[arg-type]
            gate_name="due_stale_time_sensitive",
        )
    msg = str(excinfo.value)
    assert "due_stale_time_sensitive" in msg
    assert "'at-least'" in msg


def test_gate_at_least_boundary_passes() -> None:
    # «≥80%» (g1): exactly at the threshold passes
    assert (
        _gate(numerator=16, denominator=20, threshold=0.80, direction="at_least")[
            "outcome"
        ]
        == "passed"
    )
    assert (
        _gate(numerator=15, denominator=20, threshold=0.80, direction="at_least")[
            "outcome"
        ]
        == "failed"
    )


def test_gate_at_most_boundary_passes() -> None:
    # «≤15%» (g4): exactly at the threshold passes
    assert (
        _gate(numerator=3, denominator=20, threshold=0.15, direction="at_most")[
            "outcome"
        ]
        == "passed"
    )
    assert (
        _gate(numerator=4, denominator=20, threshold=0.15, direction="at_most")[
            "outcome"
        ]
        == "failed"
    )


def test_gate_below_boundary_fails() -> None:
    # «<20%» (g6) — STRICT (T7.28, ADR-0015): exactly at the threshold
    # FAILS; one more denominator passes
    assert (
        _gate(numerator=6, denominator=30, threshold=0.20, direction="below")[
            "outcome"
        ]
        == "failed"
    )
    assert (
        _gate(numerator=6, denominator=31, threshold=0.20, direction="below")[
            "outcome"
        ]
        == "passed"
    )


def test_gate_insufficient_sample_is_independent_of_direction() -> None:
    # N < 20 → insufficient_sample before any direction is applied
    for direction in KNOWN_DIRECTIONS:
        assert (
            _gate(numerator=1, denominator=5, threshold=0.20, direction=direction)[
                "outcome"
            ]
            == "insufficient_sample"
        )
