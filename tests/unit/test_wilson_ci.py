"""Wilson 95% CI for the §22.2 ratio-gate report (T7.7, EVAL-3).

§22.2 requires publishing the 95% confidence interval with the gate
results. Reporting only: the gate outcome is decided by the point
ratio alone, never by the interval.
"""

from __future__ import annotations

import pytest

from packages.evaluation.gates import wilson_ci95


@pytest.mark.unit
def test_empty_denominator_is_none() -> None:
    assert wilson_ci95(0, 0) is None


@pytest.mark.unit
def test_reference_values() -> None:
    assert wilson_ci95(51, 53) == (0.8725, 0.9896)
    assert wilson_ci95(20, 20) == (0.8389, 1.0)
    assert wilson_ci95(0, 20) == (0.0, 0.1611)
    assert wilson_ci95(14, 51) == (0.1711, 0.4095)
    assert wilson_ci95(3, 20) == (0.0524, 0.3604)


@pytest.mark.unit
def test_interval_contains_point_ratio() -> None:
    for k, n in ((1, 20), (5, 20), (19, 20), (20, 20), (14, 51), (0, 20)):
        lo, hi = wilson_ci95(k, n)
        assert lo is not None and hi is not None
        assert lo <= k / n <= hi
