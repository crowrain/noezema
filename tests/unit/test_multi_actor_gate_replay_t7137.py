"""Unit (T7.137 части 1в, 3б, 4): реплей ВСЕХ поисков фикстуры через гейт повторного сводного
поиска. Выборка `tests/fixtures/t7137/session_searches.jsonl` — извлечённая из стендовых фикстур
пара «текст вопроса -> список выполненных web.search (запрос, успешно ли)» по всем 71 завершённой
сессии; целые файлы фикстур в репозиторий не переносятся.

Ожидаемые числа — таблица замера части 1 (ADR-0036 §10), они ВПИСАНЫ в тест, а не вычисляются из
того же кода: повторный сводный поиск (первый сводный уже выполнен успешно) — 26 отказов; из них
в сессии 09b513d2 — пять (поиск №6 и далее), и ни один поиск, называющий <= 2 названных акторов,
не отказывается; в сессиях вопросов, называющих < 3 акторов каталога, гейт молчит.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from apps.orchestrator.question_actors import ActorSearchTracker, named_query_actors, named_question_actors
from apps.orchestrator.tool_context import render_search_multi_actor_repeat_note, search_near_duplicate

pytestmark = [pytest.mark.unit]

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "t7137"

# таблица части 1: (сессия по 8-значному префиксу -> порядковые номера поисков, которые гейт
# отказал бы). Всего 26 — «повторный сводный» после ПЕРВОГО УСПЕШНО выполненного сводного.
EXPECTED_DENIED: dict[str, tuple[int, ...]] = {
    "0466c909": (3, 4, 5, 6, 7, 8),
    "09b513d2": (6, 8, 9, 10, 11),
    "1e4ef28d": (7, 8),
    "33298707": (9, 10, 12),
    "705d5464": (2, 4, 5, 6, 10),
    "70b19b00": (3, 6, 8),
    "c7e825e6": (7, 8),
}


def _sessions() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in (FIXTURES / "session_searches.jsonl").read_text(encoding="utf-8").splitlines()
        if line
    ]


def _replay(session: dict[str, Any]) -> tuple[ActorSearchTracker, list[int]]:
    """Реплей одного прогона: перед каждым поиском — решение гейта, после УСПЕШНОГО — отметка."""
    tracker = ActorSearchTracker(named=named_question_actors(str(session["question_text"])))
    denied: list[int] = []
    for ordinal, search in enumerate(session["searches"], start=1):
        if tracker.repeat_refusal(str(search["q"])) is not None:
            denied.append(ordinal)
        if search["ok"]:
            tracker.mark_executed(str(search["q"]), ordinal)
    return tracker, denied


def test_replay_of_all_fixture_searches_denies_exactly_the_table() -> None:
    sessions = _sessions()
    assert len(sessions) == 71
    total = 0
    for session in sessions:
        _tracker, denied = _replay(session)
        prefix = str(session["session_id"])[:8]
        expected = EXPECTED_DENIED.get(prefix, ())
        assert tuple(denied) == expected, f"сессия {prefix}: гейт отказал {denied}, ждали {expected}"
        total += len(denied)
    assert total == 26, "таблица части 1: ровно 26 повторных сводных поиска"


def test_no_search_naming_two_or_fewer_question_actors_is_denied() -> None:
    for session in _sessions():
        named = set(named_question_actors(str(session["question_text"])))
        _tracker, denied = _replay(session)
        for ordinal in denied:
            query = str(session["searches"][ordinal - 1]["q"])
            assert len(set(named_query_actors(query)) & named) >= 3, (
                f"сессия {str(session['session_id'])[:8]} поиск #{ordinal}: отказан запрос, "
                f"называющий < 3 названных акторов: {query}"
            )


def test_replay_of_session_09b513d2_first_aggregate_runs_then_five_denied() -> None:
    """Сценария менеджера (сессия 09b513d2, вопрос c3): первый сводный выполняется, второй и
    далее — отказы; пять одиночных поисков по одному актору не затронуты."""
    session = next(s for s in _sessions() if str(s["session_id"]).startswith("09b513d2"))
    tracker, denied = _replay(session)
    assert len(session["searches"]) == 11
    assert tuple(denied) == (6, 8, 9, 10, 11)
    # поиск №1 — уже сводный, но ПЕРВЫЙ: выполнен. Поиски 2..5 и 7 — по одному актору: выполнены.
    assert denied and 1 not in denied and 7 not in denied
    assert {2, 3, 4, 5, 7} <= set(range(1, 12)) - set(denied)
    assert tracker.uncovered() == ("Банк России", "Сбербанк")


def test_note_at_second_aggregate_of_09b513d2_names_uncovered_actors() -> None:
    """Заметка отказа на шаге ВТОРОГО сводного поиска (порядковый №6) называет Сбербанк,
    Банк России и Газпромбанк — к тому шагу отдельным поисками были только ВТБ и Альфа-Банк."""
    session = next(s for s in _sessions() if str(s["session_id"]).startswith("09b513d2"))
    tracker = ActorSearchTracker(named=named_question_actors(str(session["question_text"])))
    note = ""
    for ordinal, search in enumerate(session["searches"], start=1):
        refusal = tracker.repeat_refusal(str(search["q"]))
        if refusal is not None:
            assert ordinal == 6, "первое срабатывание — на шестом поиске"
            note = render_search_multi_actor_repeat_note(
                ordinal, refusal[0], refusal[1], tracker.uncovered()
            )
            break
        if search["ok"]:
            tracker.mark_executed(str(search["q"]), ordinal)
    assert "Без отдельного поиска пока: Банк России, Сбербанк, Газпромбанк." in note
    assert "Сводный поиск по нескольким акторам находит старые обзорные статьи" in note
    assert "Не вставляй в запрос уже известные числа" in note


def test_denied_aggregates_never_would_hit_near_duplicate_gate() -> None:
    """Непересечение счётчиков двух гейтов (часть 3, обоснование порядка): НИ ОДНА из 26
    отказанных сводных формулировок не является почти-дубликатом ранее выполненного поиска —
    значит, стоящий первым гейт почти-дубликата отпустил бы их все, и отказ засчитывается ровно
    одному счётчику честности (ADR-0036 §10)."""
    for session in _sessions():
        named = named_question_actors(str(session["question_text"]))
        if len(named) < 3:
            continue
        tracker = ActorSearchTracker(named=named)
        executed: list[tuple[str, int]] = []
        for ordinal, search in enumerate(session["searches"], start=1):
            query = str(search["q"])
            if tracker.repeat_refusal(query) is not None:
                assert search_near_duplicate(query, executed) is None, (
                    f"сессия {str(session['session_id'])[:8]} поиск #{ordinal}: формулировка "
                    "попала бы в оба гейта сразу"
                )
            if search["ok"]:
                tracker.mark_executed(query, ordinal)
                executed.append((query, ordinal))


def test_gate_silent_for_sessions_of_questions_naming_fewer_than_three_actors() -> None:
    """Вопросы c1 (Росстат+hh.ru), c2 (Росстат) и контрольный (0 акторов): во всех их сессиях
    гейт молчит, даже когда запрос называет >= 3 акторов каталога."""
    checked = 0
    for session in _sessions():
        named = named_question_actors(str(session["question_text"]))
        if len(named) >= 3:
            continue
        _tracker, denied = _replay(session)
        assert denied == [], f"гейт заговорил в сессии {str(session['session_id'])[:8]}"
        checked += 1
    assert checked >= 41, "в фикстуре 41 сессия вопросов с < 3 названными акторами"
