"""Freshness rule (T3.7, §8.6) — the single source of truth.

Expiry of the reverify deadline changes ONLY the freshness status —
never the grade or confidence. The status is a pure function of
(``reverify_after``, ``now``):

- ``reverify_after`` is NULL → ``UNKNOWN`` (no deadline; ``valid_to=NULL``
  means an open, unknown end of action, §8.6);
- ``now < reverify_after`` → ``FRESH``;
- ``now >= reverify_after`` → ``DUE`` (the deadline has passed).

Every consumer that must be time-accurate at ANY moment evaluates the
rule at read time instead of trusting the stored ``claims.
freshness_status`` column: the gate ``due_stale_time_sensitive`` (§22.2)
and claim retrieval (§5.4) do not depend on whether a background
reassessment (or an activation flip) ever ran (T7.27, ADR-0014). The
stored column remains a display cache updated by every write path
(commit, reassessment) — see ADR-0014.
"""

from __future__ import annotations

from datetime import datetime

from packages.domain.models.enums import FreshnessStatus


def freshness_status(reverify_after: datetime | None, now: datetime) -> FreshnessStatus:
    """The §8.6/T3.7 freshness rule for one claim at ``now``."""
    if reverify_after is None:
        return FreshnessStatus.UNKNOWN
    if now < reverify_after:
        return FreshnessStatus.FRESH
    return FreshnessStatus.DUE
