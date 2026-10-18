"""T7.59(в): the node state a command is decided on — DB first, in-memory only for what web owns.

Pure resolution (`effective_node_state`), unit-tested: the DB value is taken as is, except a
`session_running` marker that describes no running session (no nonterminal session row, and this
web process owns no session task) — such a leftover must not wedge the node forever.
"""

from __future__ import annotations

import pytest

from apps.web.api import effective_node_state

pytestmark = [pytest.mark.unit]


@pytest.mark.parametrize("raw", ["idle", "paused"])
def test_idle_and_paused_are_taken_from_the_db_as_is(raw: str) -> None:
    """An operator/auto pause is sticky until the operator resume (§5.2.1)."""
    assert effective_node_state(raw, web_owns_session=False, db_has_nonterminal_session=False) == raw
    assert effective_node_state(raw, web_owns_session=True, db_has_nonterminal_session=True) == raw


def test_external_tick_holding_a_session_keeps_session_running() -> None:
    """The invariant the fix must not weaken: a real external session keeps wake_now refused."""
    assert (
        effective_node_state(
            "session_running", web_owns_session=False, db_has_nonterminal_session=True
        )
        == "session_running"
    )


def test_web_own_session_keeps_session_running_even_without_a_visible_row_yet() -> None:
    """The session this web started is in memory before its row exists — still not a free node."""
    assert (
        effective_node_state(
            "session_running", web_owns_session=True, db_has_nonterminal_session=False
        )
        == "session_running"
    )


def test_leftover_marker_without_any_session_resolves_to_idle() -> None:
    """The stand defect: an external tick ended (or died) and left the marker behind."""
    assert (
        effective_node_state(
            "session_running", web_owns_session=False, db_has_nonterminal_session=False
        )
        == "idle"
    )
