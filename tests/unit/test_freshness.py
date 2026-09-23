"""Unit: the §8.6/T3.7 freshness rule — the single source of truth
(T7.27, ADR-0014; T7.32, ADR-0017). Expiry changes ONLY the freshness
status, never the grade or confidence. T7.32 (ADR-0017): a NULL
reverify_after means "no deadline BY CONSTRUCTION" — the claim is
about a fixed point (explicit question date / dateless question with
the model's as_of), later events cannot spoil it, it is valid forever
→ ``evergreen`` (NOT ``unknown``: the system knows there is no
deadline; and NOT ``due``: a fixed point cannot become overdue)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from packages.domain.models.enums import FreshnessStatus
from packages.memory.freshness import freshness_status

NOW = datetime(2026, 9, 22, 8, 0, tzinfo=UTC)


def test_no_deadline_is_evergreen() -> None:
    # T7.32 (ADR-0017): reverify_after is NULL iff the claim's date
    # anchor is not relative (a fixed point) — no deadline by
    # construction, valid forever, never fresh/due/unknown
    assert freshness_status(None, NOW) is FreshnessStatus.EVERGREEN


def test_before_deadline_is_fresh() -> None:
    assert freshness_status(NOW + timedelta(days=1), NOW) is FreshnessStatus.FRESH


def test_deadline_passed_is_due() -> None:
    assert freshness_status(NOW - timedelta(days=1), NOW) is FreshnessStatus.DUE


def test_boundary_now_equals_deadline_is_due() -> None:
    # fresh only while now < reverify_after — the instant of expiry
    # the claim is already due
    assert freshness_status(NOW, NOW) is FreshnessStatus.DUE


def test_rule_is_pure_in_now() -> None:
    # the same claim is fresh before the deadline and due after — the
    # status is a function of (reverify_after, now), not of any stored
    # state or of whether a background process ran
    deadline = datetime(2026, 7, 1, tzinfo=UTC)
    assert freshness_status(deadline, datetime(2026, 6, 30, tzinfo=UTC)) is FreshnessStatus.FRESH
    assert freshness_status(deadline, datetime(2026, 7, 1, tzinfo=UTC)) is FreshnessStatus.DUE
    assert freshness_status(deadline, datetime(2026, 9, 22, tzinfo=UTC)) is FreshnessStatus.DUE


def test_evergreen_does_not_flip_with_time() -> None:
    # T7.32 (ADR-0017): a claim without a deadline is evergreen at ANY
    # moment — a year from now it still cannot become due (the "Спутник
    # 1957" class that was due forever under the pre-T7.32 rule)
    assert freshness_status(None, NOW) is FreshnessStatus.EVERGREEN
    assert (
        freshness_status(None, NOW + timedelta(days=3650)) is FreshnessStatus.EVERGREEN
    )
