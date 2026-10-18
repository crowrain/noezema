"""Unit (T7.111 часть 2): классификация отказа research.fetch, хост URL и правила журнала.

Замер менеджера (фикстура `fixtures-t7111/step-budget-2026-10-14.txt`): 14 отказов чтения, 5 из них
— повторный заход к уже отказавшему хосту (`alfabank.ru`: 403 → 403 → 429; `stats.hh.ru`, `tass.ru`).
Проверяются чистые функции модуля `apps/orchestrator/host_availability.py`: какие отказы считаются
подходящими, как из пары action_started/action_failed строится список хостов (окно 7 суток, >=2 отказа, успех
после последнего отказа снимает хост) и как выглядит секция «Недавно недоступны». Wiring цикла и
read-only SELECT журнала — tests/scenario/test_explorer_host_availability_t7111.py.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from apps.orchestrator.host_availability import (
    HOST_AVAILABILITY_MAX_HOSTS,
    HOST_AVAILABILITY_SECTION_CHAR_LIMIT,
    HOST_UNAVAILABLE_MIN_FAILURES,
    HTTP_DENY_CODES,
    HostAvailability,
    JournalRow,
    classify_fetch_error,
    fetch_url_host,
    render_host_availability_section,
    summarize_host_journal,
)

pytestmark = [pytest.mark.unit]


def test_named_constants_match_the_decision() -> None:
    # коды отказа названы решением (401/403/429 + таймаут); 404 и свойства страницы — мимо;
    # два отказа — минимум журнала, порог минимальной длины поиска относится к части 1
    assert HTTP_DENY_CODES == frozenset({"401", "403", "429"})  # noqa: SIM300 — сравнение читается слева направо
    assert HOST_UNAVAILABLE_MIN_FAILURES == 2


# ─── (а) классификация отказа и хост ────────────────────────────────────────────


def test_classify_fetch_error_counts_only_qualifying_refusals() -> None:
    # 401/403/429/таймаут — отказ хоста, помечает его (пункт (а) решения)
    assert classify_fetch_error("non-200 status: 401") == "401"
    assert classify_fetch_error("non-200 status: 403") == "403"
    assert classify_fetch_error("non-200 status: 429") == "429"
    assert classify_fetch_error("timeout: read timed out") == "timeout"
    # свойства страницы или канала, а не хоста: 404, нечитаемый артефакт, размер, сетевой сбой — мимо
    assert classify_fetch_error("non-200 status: 404") is None
    assert classify_fetch_error("normalized artifact unreadable") is None
    assert classify_fetch_error("no normalized text available for this content") is None
    assert classify_fetch_error("time budget exhausted") is None
    assert classify_fetch_error("transport error: connection refused") is None
    assert classify_fetch_error("domain not in private allowlist") is None
    assert classify_fetch_error("") is None
    assert classify_fetch_error("upstream rate limit exceeded") is None


def test_fetch_url_host_normalizes_host_label() -> None:
    assert fetch_url_host("https://cbr.ru/press/?id=1") == "cbr.ru"
    assert fetch_url_host("http://www.cbr.ru/a") == "cbr.ru"  # один ведущий www. — тот же хост
    assert fetch_url_host("https://WWW.Stats.HH.RU/x") == "stats.hh.ru"
    assert fetch_url_host("https://rosstat.gov.ru:443/a#b") == "rosstat.gov.ru"
    # stats.hh.ru и hh.ru — разные хосты (соглашение host() из step_budget.py)
    assert fetch_url_host("https://stats.hh.ru/x") != fetch_url_host("https://hh.ru/x")
    assert fetch_url_host("") == ""
    assert fetch_url_host("не адрес") == ""


# ─── (б) правила журнала «Недавно недоступны» ───────────────────────────────────

NOW = datetime.now(UTC)


def _started(sequence: int, at: datetime, url: str, action_id: str, session_id: str = "s1") -> JournalRow:
    return JournalRow(
        type="action_started",
        session_id=session_id,
        sequence=sequence,
        at=at,
        payload={"action_id": action_id, "tool": "research.fetch", "arguments": json.dumps({"url": url})},
    )


def _failed(sequence: int, at: datetime, action_id: str, error: str, session_id: str = "s1") -> JournalRow:
    return JournalRow(
        type="action_failed",
        session_id=session_id,
        sequence=sequence,
        at=at,
        payload={"action_id": action_id, "ok": False, "error": error, "data": None},
    )


def _read_ok(sequence: int, at: datetime, url: str, session_id: str = "s1") -> JournalRow:
    return JournalRow(
        type="research_content_read",
        session_id=session_id,
        sequence=sequence,
        at=at,
        payload={"url": url, "normalized_sha256": "0" * 64},
    )


def test_qualifying_pair_counts_single_failure_does_not() -> None:
    rows = [
        _started(1, NOW - timedelta(days=2), "https://dead.example/a", "d1"),
        _failed(2, NOW - timedelta(days=2), "d1", "non-200 status: 403"),
        _started(3, NOW - timedelta(days=1), "https://dead.example/b", "d2"),
        _failed(4, NOW - timedelta(days=1), "d2", "timeout: boom"),
        _started(5, NOW - timedelta(days=1), "https://once.example/x", "o1"),
        _failed(6, NOW - timedelta(days=1), "o1", "non-200 status: 429"),
    ]
    reports = summarize_host_journal(rows)
    assert [r.host for r in reports] == ["dead.example"]  # once.example — один отказ: не список
    dead = reports[0]
    assert dead.failures == 2 and dead.kind == "timeout"  # вид последнего отказа
    assert dead.last_failure_at > NOW - timedelta(days=1, minutes=1)


def test_success_after_last_failure_removes_host_but_earlier_success_does_not() -> None:
    healed = [
        _started(1, NOW - timedelta(days=3), "https://healed.example/a", "h1"),
        _failed(2, NOW - timedelta(days=3), "h1", "non-200 status: 403"),
        _started(3, NOW - timedelta(days=2), "https://healed.example/b", "h2"),
        _failed(4, NOW - timedelta(days=2), "h2", "timeout: boom"),
        _read_ok(5, NOW - timedelta(days=1), "https://healed.example/c"),  # починился
    ]
    assert summarize_host_journal(healed) == []

    relapsed = [
        _started(1, NOW - timedelta(days=4), "https://back.example/a", "b1"),
        _failed(2, NOW - timedelta(days=4), "b1", "non-200 status: 403"),
        _read_ok(3, NOW - timedelta(days=3), "https://back.example/a"),  # успех был ДО отказов
        _started(4, NOW - timedelta(days=2), "https://back.example/d", "b2"),
        _failed(5, NOW - timedelta(days=2), "b2", "non-200 status: 403"),
        _started(6, NOW - timedelta(days=1), "https://back.example/e", "b3"),
        _failed(7, NOW - timedelta(days=1), "b3", "non-200 status: 429"),
    ]
    reports = summarize_host_journal(relapsed)
    assert [r.host for r in reports] == ["back.example"] and reports[0].failures == 3


def test_non_qualifying_failures_never_build_the_list() -> None:
    rows = [
        _started(1, NOW - timedelta(days=2), "https://pages.example/a", "p1"),
        _failed(2, NOW - timedelta(days=2), "p1", "non-200 status: 404"),
        _started(3, NOW - timedelta(days=1), "https://pages.example/b", "p2"),
        _failed(4, NOW - timedelta(days=1), "p2", "non-200 status: 404"),
        _started(5, NOW - timedelta(days=1), "https://artifact.example/c", "g1"),
        _failed(6, NOW - timedelta(days=1), "g1", "normalized artifact unreadable"),
        _started(7, NOW - timedelta(days=1), "https://artifact.example/d", "g2"),
        _failed(8, NOW - timedelta(days=1), "g2", "time budget exhausted"),
        # отказ действия другого инструмента (нет пары action_started этого окна) не участвует
        _failed(9, NOW - timedelta(days=1), "no-such-started", "timeout: other tool"),
    ]
    assert summarize_host_journal(rows) == []


def test_www_variants_and_case_collapse_into_one_host() -> None:
    rows = [
        _started(1, NOW - timedelta(days=2), "https://www.stats.hh.ru/x", "w1"),
        _failed(2, NOW - timedelta(days=2), "w1", "non-200 status: 429"),
        _started(3, NOW - timedelta(days=1), "https://Stats.HH.RU/y", "w2"),
        _failed(4, NOW - timedelta(days=1), "w2", "timeout: boom"),
        _started(5, NOW - timedelta(days=1), "https://hh.ru/z", "w3"),  # другой хост
        _failed(6, NOW - timedelta(days=1), "w3", "non-200 status: 403"),
    ]
    reports = summarize_host_journal(rows)
    assert [r.host for r in reports] == ["stats.hh.ru"] and reports[0].failures == 2


def test_truncated_arguments_json_still_yields_the_host() -> None:
    # _cap_args срезает канонический JSON по потолку — fallback должен достать начало URL
    truncated = '{"url": "https://cut.example/very/long/page'
    rows = [
        JournalRow(
            type="action_started",
            session_id="s1",
            sequence=1,
            at=NOW - timedelta(days=2),
            payload={"action_id": "c1", "tool": "research.fetch", "arguments": truncated},
        ),
        _failed(2, NOW - timedelta(days=2), "c1", "non-200 status: 403"),
        _started(3, NOW - timedelta(days=1), "https://cut.example/other", "c2"),
        _failed(4, NOW - timedelta(days=1), "c2", "non-200 status: 403"),
    ]
    reports = summarize_host_journal(rows)
    assert [r.host for r in reports] == ["cut.example"] and reports[0].failures == 2


def test_section_is_short_whole_lines_capped_and_absent_when_empty() -> None:
    assert render_host_availability_section([]) == ""

    entries = [
        HostAvailability(
            host=f"h{i}.example",
            kind="timeout" if i % 2 == 0 else "403",
            last_failure_at=NOW - timedelta(days=1, hours=i),
            failures=2 + i,
        )
        for i in range(HOST_AVAILABILITY_MAX_HOSTS + 6)
    ]
    section = render_host_availability_section(entries)
    assert section.startswith("# Недавно недоступны")
    assert len(section.encode("utf-8")) <= HOST_AVAILABILITY_SECTION_CHAR_LIMIT
    host_lines = [line for line in section.splitlines() if line.startswith("- ")]
    # не более MAX_HOSTS строк; хосты выносятся целиком, половинок домена нет
    assert len(host_lines) == HOST_AVAILABILITY_MAX_HOSTS
    assert all(" — " in line and "(отказов: " in line for line in host_lines)
    assert "- h0.example — таймаут" in section and "403" in section
    assert "Не стройте поиск вокруг них" in section
