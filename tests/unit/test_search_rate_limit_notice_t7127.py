"""T7.127 (тест 5б): чистая арифметика освобождения слота и текст отказа по лимиту поиска.

Ни БД, ни сети: `UpstreamWindow` — снимок, который читает `upstream_window_state`, а
`minutes_until_release` и `rate_limit_notice` — чистые функции (AGENTS: рабочая логика выносится
в тестируемую функцию). Код отказа `rate_limited` остаётся прежним — меняется только то, что
понимает исследователь.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from apps.research_proxy.search import (
    UpstreamWindow,
    minutes_until_release,
    rate_limit_notice,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 20, 11, 32, 40, tzinfo=UTC)


def _window(requests: int, oldest: datetime | None, window_seconds: int = 3600) -> UpstreamWindow:
    return UpstreamWindow(requests=requests, oldest=oldest, now=NOW, window_seconds=window_seconds)


def test_empty_window_has_nothing_to_predict() -> None:
    """Пустое окно: oldest отсутствует — освобождать нечего, минуты не выдумываются."""
    assert minutes_until_release(_window(requests=0, oldest=None)) is None


def test_oldest_exactly_at_the_boundary_releases_now() -> None:
    """Самый старый запрос ровно на границе окна: слот освобождается сейчас (0, не 1)."""
    assert minutes_until_release(_window(requests=20, oldest=NOW - timedelta(seconds=3600))) == 0


def test_all_window_requests_already_older_than_the_window_releases_now() -> None:
    """Все запросы окна старше самого окна (часы могли уйти вперёд): остатка нет — 0, не минус."""
    assert minutes_until_release(_window(requests=20, oldest=NOW - timedelta(seconds=4300))) == 0


def test_fresh_window_releases_after_the_whole_window() -> None:
    """Только что ушедший запрос: до освобождения — почти всё окно, округление вверх."""
    assert minutes_until_release(_window(requests=20, oldest=NOW - timedelta(seconds=5))) == 60


@pytest.mark.parametrize(
    ("remaining_seconds", "expected_minutes"),
    [(60, 1), (61, 2), (3599, 60), (1, 1), (120, 2), (121, 3)],
)
def test_fractional_remainder_rounds_up(remaining_seconds: int, expected_minutes: int) -> None:
    """Дробная минута округляется ВВЕРХ: обещать меньше остатка нельзя."""
    window = _window(requests=60, oldest=NOW - timedelta(seconds=3600 - remaining_seconds))
    assert minutes_until_release(window) == expected_minutes


def test_naive_journal_timestamp_is_read_as_utc() -> None:
    """asyncpg может отдать naive-время — трактовка та же, что во всём модуле (UTC): запрос
    45 минут назад при окне 3600 с освобождает слот через 15 минут."""
    naive = (NOW - timedelta(minutes=45)).replace(tzinfo=None)
    assert minutes_until_release(_window(requests=20, oldest=naive)) == 15


def test_shorter_window_shrinks_the_wait() -> None:
    """Окно берётся из снапшота, а не из константы: 900 с — 15 минут ожидания."""
    window = _window(requests=60, oldest=NOW - timedelta(seconds=300), window_seconds=900)
    assert minutes_until_release(window) == 10


def test_notice_names_limit_window_and_minutes() -> None:
    """Текст отказа называет лимит, окно и минуты до освобождения (требование части 3)."""
    notice = rate_limit_notice(_window(requests=60, oldest=NOW - timedelta(minutes=15)), limit=60)
    assert "60 из 60 запросов" in notice
    assert "3600 с" in notice
    assert "60 мин" in notice
    assert "через 45 мин" in notice


def test_notice_keeps_machine_code_and_the_way_out() -> None:
    """Код `rate_limited` в тексте (по нему отказ отличают), подсказка не слать новые поиски."""
    notice = rate_limit_notice(_window(requests=20, oldest=NOW - timedelta(minutes=10)), limit=20)
    assert "rate_limited" in notice
    assert "дальнейшие поиски тоже будут отклонены" in notice
    assert "research.fetch" in notice
    assert "заканчивай" in notice


def test_notice_survives_the_observation_cut() -> None:
    """Оркестратор режет текст наблюдения до 500 знаков — отказ обязан влезать целиком."""
    empty = rate_limit_notice(_window(requests=0, oldest=None), limit=1)
    full = rate_limit_notice(_window(requests=1000, oldest=NOW - timedelta(seconds=1)), limit=1000)
    assert len(empty) <= 500
    assert len(full) <= 500
    assert empty.startswith("внешний поиск отклонён: лимит поиска узла исчерпан")


def test_notice_without_window_data_says_so_instead_of_inventing_minutes() -> None:
    """Пустое окно в тексте честно говорит «предсказать нечего», а не «через 0 мин»."""
    notice = rate_limit_notice(_window(requests=1, oldest=None), limit=1)
    assert "не по чему" in notice
    assert "через" not in notice


def test_notice_agrees_with_the_window_it_was_counted_from() -> None:
    """Числа в тексте берутся из того же снимка окна, по которому посчитан отказ."""
    window = UpstreamWindow(requests=23, oldest=NOW - timedelta(minutes=40), now=NOW, window_seconds=3600)
    notice = rate_limit_notice(window, limit=25)
    assert "23 из 25 запросов" in notice


def test_ru_pluralisation_of_the_limit() -> None:
    """Счётчик в тексте согласуется с числом: 1 запрос, 22 запроса, 1000 запросов."""
    assert "из 1 запрос " in rate_limit_notice(_window(requests=1, oldest=NOW), limit=1)
    assert "из 22 запроса " in rate_limit_notice(_window(requests=22, oldest=NOW), limit=22)
    assert "из 1000 запросов " in rate_limit_notice(_window(requests=1000, oldest=NOW), limit=1000)
