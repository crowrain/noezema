"""Unit: wake schedule pure logic (T3.29, §5.2.1).

Covers the schedule timing (interval + minimum gap + backoff window), the
wake_schedule validation (fail-closed) and the exponential backoff math.
The DB-backed admission and state rules are in tests/scenario/test_wake_scheduler.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from apps.orchestrator.scheduler import (
    WAIT_BACKOFF,
    WAIT_INTERVAL,
    WAIT_MIN_INTERVAL,
    WakeSchedule,
    WakeScheduleError,
    backoff_delay_seconds,
    evaluate_schedule_timing,
)
from packages.domain.config import BOOTSTRAP_PAYLOAD

pytestmark = [pytest.mark.unit]

T0 = datetime(2026, 9, 14, 12, 0, 0, tzinfo=UTC)


def _schedule_kwargs() -> dict[str, Any]:
    return {
        "interval_seconds": 3600,
        "min_session_interval_seconds": 600,
        "backoff_base_seconds": 60,
        "backoff_multiplier": 2,
        "backoff_max_seconds": 86400,
        "max_consecutive_failures": 3,
        "disk_quota_mb": 1024,
        "gpu_required": False,
    }


def _schedule() -> WakeSchedule:
    return WakeSchedule.from_payload(BOOTSTRAP_PAYLOAD["wake_schedule"])


def test_bootstrap_wake_schedule_is_valid() -> None:
    s = _schedule()
    assert s.interval_seconds == 3600
    assert s.min_session_interval_seconds == 600
    assert s.backoff_base_seconds == 60
    assert s.backoff_multiplier == 2
    assert s.backoff_max_seconds == 86400
    assert s.max_consecutive_failures == 3
    assert s.disk_quota_mb == 1024
    assert s.gpu_required is False


def test_from_payload_rejects_non_mapping() -> None:
    for bad in (None, [], "x", 42):
        with pytest.raises(WakeScheduleError):
            WakeSchedule.from_payload(bad)


@pytest.mark.parametrize("key", list(_schedule_kwargs().keys()))
def test_from_payload_rejects_missing_key(key: str) -> None:
    payload = _schedule_kwargs()
    del payload[key]
    with pytest.raises(WakeScheduleError, match=key):
        WakeSchedule.from_payload(payload)


def test_from_payload_rejects_wrong_types() -> None:
    # bool is not an int for the numeric fields
    payload = _schedule_kwargs()
    payload["interval_seconds"] = True
    with pytest.raises(WakeScheduleError, match="interval_seconds"):
        WakeSchedule.from_payload(payload)

    payload = _schedule_kwargs()
    payload["backoff_multiplier"] = "2"
    with pytest.raises(WakeScheduleError, match="backoff_multiplier"):
        WakeSchedule.from_payload(payload)

    payload = _schedule_kwargs()
    payload["gpu_required"] = 0
    with pytest.raises(WakeScheduleError, match="gpu_required"):
        WakeSchedule.from_payload(payload)


def test_from_payload_rejects_bad_values() -> None:
    cases: list[tuple[str, Any]] = [
        ("interval_seconds", 0),
        ("min_session_interval_seconds", -5),
        ("backoff_base_seconds", 0),
        ("backoff_multiplier", 0.5),
        ("backoff_max_seconds", 1),  # < base (60)
        ("max_consecutive_failures", 0),
        ("disk_quota_mb", 0),
    ]
    for key, bad in cases:
        payload = _schedule_kwargs()
        payload[key] = bad
        with pytest.raises(WakeScheduleError, match=key):
            WakeSchedule.from_payload(payload)


# ── schedule timing ───────────────────────────────────────────────────────


def test_first_tick_is_due() -> None:
    due, reason = evaluate_schedule_timing(
        T0,
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=None,
        backoff_until=None,
    )
    assert due is True
    assert reason is None


def test_first_tick_blocked_by_backoff_window() -> None:
    due, reason = evaluate_schedule_timing(
        T0,
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=None,
        backoff_until=T0 + timedelta(seconds=300),
    )
    assert due is False
    assert reason == WAIT_BACKOFF


def test_interval_not_elapsed() -> None:
    due, reason = evaluate_schedule_timing(
        T0 + timedelta(seconds=3599),
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=T0,
        backoff_until=None,
    )
    assert due is False
    assert reason == WAIT_INTERVAL


def test_interval_elapsed_is_due() -> None:
    due, reason = evaluate_schedule_timing(
        T0 + timedelta(seconds=3600),
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=T0,
        backoff_until=None,
    )
    assert due is True
    assert reason is None


def test_min_interval_enforced_when_wider_than_interval() -> None:
    due, reason = evaluate_schedule_timing(
        T0 + timedelta(seconds=3600),
        interval_seconds=3600,
        min_session_interval_seconds=14400,
        last_session_finished_at=T0,
        backoff_until=None,
    )
    assert due is False
    assert reason == WAIT_MIN_INTERVAL


def test_backoff_window_blocks_after_interval() -> None:
    due, reason = evaluate_schedule_timing(
        T0 + timedelta(seconds=7200),
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=T0,
        backoff_until=T0 + timedelta(seconds=7500),
    )
    assert due is False
    assert reason == WAIT_BACKOFF


def test_backoff_elapsed_is_due() -> None:
    due, reason = evaluate_schedule_timing(
        T0 + timedelta(seconds=7500),
        interval_seconds=3600,
        min_session_interval_seconds=600,
        last_session_finished_at=T0,
        backoff_until=T0 + timedelta(seconds=7500),
    )
    assert due is True
    assert reason is None


# ── backoff math ──────────────────────────────────────────────────────────


def test_backoff_grows_exponentially() -> None:
    base, mult, cap = 60, 2.0, 86400
    assert backoff_delay_seconds(consecutive_failures=1, base_seconds=base, multiplier=mult, max_seconds=cap) == 60
    assert backoff_delay_seconds(consecutive_failures=2, base_seconds=base, multiplier=mult, max_seconds=cap) == 120
    assert backoff_delay_seconds(consecutive_failures=3, base_seconds=base, multiplier=mult, max_seconds=cap) == 240


def test_backoff_is_capped() -> None:
    assert (
        backoff_delay_seconds(consecutive_failures=50, base_seconds=60, multiplier=2.0, max_seconds=86400) == 86400
    )


def test_backoff_requires_a_failure() -> None:
    with pytest.raises(ValueError):
        backoff_delay_seconds(consecutive_failures=0, base_seconds=60, multiplier=2.0, max_seconds=86400)
