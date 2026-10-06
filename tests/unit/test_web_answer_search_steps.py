"""Unit: шаг ленты «поискал в интернете» и честные замечания при поиске (T7.71).

`web.search` появился в ленте действий, поэтому у шага есть две границы, которые проверяются здесь:

1. рядом с названием действия показывается **только запрос**, записанный узлом (как домен страницы для
   `research.fetch`): одна строка, потолок длины, без управляющих символов; если в записи есть то, чего
   на простом экране быть не должно (символ спецификации, номер задачи плана, hex-подобный идентификатор,
   код инструмента) — запрос не показывается вовсе, шаг остаётся с одним названием действия;
2. поиск сам по себе **не делает работу «использующей внешние источники»**: доказательство даёт чтение
   страницы, поэтому замечание про внешние источники остаётся привязанным к `research.fetch` — и при этом
   не утверждает, что поиска не было.

Ничего из ранее закреплённого (`tests/unit/test_web_answer.py`) этими тестами не ослабляется.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from apps.web import answer as ans
from apps.web import labels as ui_labels

pytestmark = pytest.mark.unit


def _event(sequence: int, type_: str, **payload: Any) -> dict[str, Any]:
    return {"sequence": sequence, "type": type_, "payload": payload}


def _search_event(sequence: int, arguments: object, action_id: str = "s1") -> list[dict[str, Any]]:
    return [
        _event(sequence, "action_started", tool="web.search", action_id=action_id, arguments=arguments),
        _event(sequence + 1, "action_completed", action_id=action_id, ok=True),
    ]


def _fetch_event(sequence: int, url: str, action_id: str = "f1") -> list[dict[str, Any]]:
    return [
        _event(sequence, "action_started", tool="research.fetch", action_id=action_id, arguments={"url": url}),
        _event(sequence + 1, "action_completed", action_id=action_id, ok=True),
    ]


SEARCH_LABEL = ui_labels.describe("action_tool", "web.search")["label"]
FETCH_LABEL = ui_labels.describe("action_tool", "research.fetch")["label"]


# ─── подпись действия ───────────────────────────────────────────────────────


def test_search_action_has_a_plain_russian_label() -> None:
    """Новый инструмент не оставляет ленту без подписи и не показывает свой код."""
    entry = ui_labels.describe("action_tool", "web.search")
    assert entry["label"] and entry["hint"] and entry["action"]
    assert "web.search" not in entry["label"] + entry["hint"]
    # те же границы, что проверяет тест полноты подписей (AGENTS §7, T7.64)
    assert len(entry["label"]) <= ui_labels.MAX_LABEL_CHARS
    assert len(entry["hint"]) <= ui_labels.MAX_HINT_CHARS
    assert len(entry["action"]) <= ui_labels.MAX_ACTION_CHARS

    # подпись взята из реестра: инструмент без подписи краснит тест полноты
    from packages.policy import tools as policy_tools

    assert "web.search" in {spec.name for spec in policy_tools.all_tools()}


# ─── запрос в шаге ──────────────────────────────────────────────────────────


def test_search_step_names_the_query_recorded_by_the_host() -> None:
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": "стоимость владения сервером 2026"})),
    ]
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    step_text = next(t for t in texts if t.startswith(SEARCH_LABEL))
    assert step_text == f"{SEARCH_LABEL}: стоимость владения сервером 2026"


def test_search_step_cuts_the_query_to_the_display_cap() -> None:
    long_query = "оптимизация затрат на сервер " * 10
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": long_query})),
    ]
    groups, _failed = ans.executed_actions(events)
    detail = next(detail for tool, _count, detail in groups if tool == "web.search")
    assert len(detail) == ans.SEARCH_QUERY_CHARS
    assert detail == long_query[: ans.SEARCH_QUERY_CHARS]


def test_search_step_flattens_newlines_and_masks_nul_from_the_query() -> None:
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": "цена\nстойки\tв дата-центре\x00 2026"})),
    ]
    groups, _failed = ans.executed_actions(events)
    detail = next(d for tool, _count, d in groups if tool == "web.search")
    assert "\n" not in detail and "\t" not in detail
    assert "\\x00" in detail  # NUL показан как метка, а не как управляющий символ (AGENTS sanitization)
    step_text = next(s["text"] for s in ans.build_answer_steps(events) if s["text"].startswith(SEARCH_LABEL))
    assert "цена стойки в дата-центре" in step_text


@pytest.mark.parametrize(
    "query",
    [
        "сравнение §5.7 по сети",  # символ спецификации
        "как решено в T7.71",  # номер задачи плана
        "нужен source_assertion для вывода",  # служебный код snake_case
        "2f4c9a1b7d8e6053c4b2a9f10d3e8c7b",  # hex-подобный идентификатор
    ],
)
def test_search_step_refuses_to_display_an_unsafe_query(query: str) -> None:
    """Запрос показывается ровно тогда, когда его можно назвать человеческим языком. Иначе — только
    название действия: данные не переформулируются и не выдумываются."""
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": query})),
    ]
    groups, _failed = ans.executed_actions(events)
    detail = next(d for tool, _count, d in groups if tool == "web.search")
    assert detail == ""

    texts = [step["text"] for step in ans.build_answer_steps(events)]
    step_text = next(t for t in texts if t.startswith(SEARCH_LABEL))
    assert step_text == SEARCH_LABEL
    joined = " ".join(texts)
    assert query not in joined


@pytest.mark.parametrize(
    "arguments",
    [
        None,
        "",
        "не json",
        json.dumps({"url": "https://example.com"}),  # аргумент поиска — не адрес
        json.dumps({"query": ""}),
        json.dumps({"query": 12}),
    ],
)
def test_search_step_without_a_usable_query_stays_bare(arguments: object) -> None:
    events = [
        _event(1, "question_selected"),
        *_search_event(2, arguments),
    ]
    groups, _failed = ans.executed_actions(events)
    assert next(d for tool, _count, d in groups if tool == "web.search") == ""
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    assert SEARCH_LABEL in texts


def test_different_queries_become_separate_steps_not_one_query_twice() -> None:
    """T7.67a (стенд, сессия 66c7901a): два разных поиска — два шага.

    Прежняя реализация держила ОДНУ деталь на инструмент и показывала первый
    запрос дважды («поискал в интернете: <первый> — 2 раза»), теряя второй.
    Это было неправдой о выполненной работе, поэтому тест переписан по решению
    пользователя (обоснование — STATUS.md T7.67a): сворачивается только точный
    повтор; разные записанные запросы остаются отдельными шагами, третий никто
    не выдумывает и ничего не склеивает.
    """
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": "стоимость владения сервером"}), action_id="s1"),
        *_search_event(6, json.dumps({"query": "цена стойки в дата-центре"}), action_id="s2"),
    ]
    groups, _failed = ans.executed_actions(events)
    assert [(tool, count, detail) for tool, count, detail in groups] == [
        ("web.search", 1, "стоимость владения сервером"),
        ("web.search", 1, "цена стойки в дата-центре"),
    ]

    texts = [step["text"] for step in ans.build_answer_steps(events)]
    search_steps = [t for t in texts if t.startswith(SEARCH_LABEL)]
    assert len(search_steps) == 2
    assert any("стоимость владения сервером" in t and "цена стойки" not in t for t in search_steps)
    assert any("цена стойки в дата-центре" in t and "стоимость" not in t for t in search_steps)
    # ничто не утверждает повтор, которого не было
    assert all("2 раза" not in t for t in texts)


def test_identical_queries_collapse_into_one_step_with_a_true_count() -> None:
    """Точный повтор по-прежнему сворачивается — с настоящим числом (AGENTS §3)."""
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": "стоимость владения сервером"}), action_id="s1"),
        *_search_event(6, json.dumps({"query": "стоимость владения сервером"}), action_id="s2"),
    ]
    groups, _failed = ans.executed_actions(events)
    assert [(tool, count) for tool, count, _d in groups] == [("web.search", 2)]

    texts = [step["text"] for step in ans.build_answer_steps(events)]
    step_text = next(t for t in texts if t.startswith(SEARCH_LABEL))
    assert step_text == f"{SEARCH_LABEL}: стоимость владения сервером — 2 раза"


def test_two_different_pages_are_two_steps_not_one_page_read_twice() -> None:
    """Тот же закон для `research.fetch`: стенд показал «cbr.ru — 2 раза» там, где были cbr.ru и expert.ru."""
    events = [
        _event(1, "question_selected"),
        *_fetch_event(2, "https://cbr.ru/press", action_id="f1"),
        *_fetch_event(6, "https://expert.ru/article", action_id="f2"),
    ]
    groups, _failed = ans.executed_actions(events)
    assert [(tool, count, detail) for tool, count, detail in groups] == [
        ("research.fetch", 1, "cbr.ru"),
        ("research.fetch", 1, "expert.ru"),
    ]

    texts = [step["text"] for step in ans.build_answer_steps(events)]
    fetch_steps = [t for t in texts if t.startswith(FETCH_LABEL)]
    assert len(fetch_steps) == 2
    assert any(t == f"{FETCH_LABEL}: cbr.ru" for t in fetch_steps)
    assert any(t == f"{FETCH_LABEL}: expert.ru" for t in fetch_steps)
    assert all("2 раза" not in t for t in texts)


def test_identical_pages_collapse_into_one_step_with_a_true_count() -> None:
    events = [
        _event(1, "question_selected"),
        *_fetch_event(2, "https://cbr.ru/press", action_id="f1"),
        *_fetch_event(6, "https://cbr.ru/press?page=2", action_id="f2"),
    ]
    groups, _failed = ans.executed_actions(events)
    assert [(tool, count, detail) for tool, count, detail in groups] == [("research.fetch", 2, "cbr.ru")]

    texts = [step["text"] for step in ans.build_answer_steps(events)]
    step_text = next(t for t in texts if t.startswith(FETCH_LABEL))
    assert step_text == f"{FETCH_LABEL}: cbr.ru — 2 раза"


def test_search_step_is_not_invented_for_a_failed_or_aborted_search() -> None:
    events = [
        _event(1, "question_selected"),
        _event(2, "action_started", tool="web.search", action_id="s1", arguments='{"query":"что-то"}'),
        _event(3, "action_completed", action_id="s1", ok=False),
    ]
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    assert all(SEARCH_LABEL not in text for text in texts), texts
    groups, failed = ans.executed_actions(events)
    # в шаги сворачиваются только подтверждённые действия; «не удалось» считает action_failed,
    # а не сорванный action_completed (prezhnie zakreplyonnoe pravilo T7.65)
    assert groups == [] and failed == 0


# ─── честные замечания: поиск ≠ использованный источник ─────────────────────


def test_search_alone_does_not_claim_external_sources_were_used() -> None:
    """Доказательство даёт чтение страницы. Сессия, которая только искала, честно остаётся
    «без внешних источников» — и замечание не утверждает, что поиска не было."""
    groups, failed = ans.executed_actions(
        [
            _event(1, "question_selected"),
            *_search_event(2, json.dumps({"query": "стоимость владения сервером"})),
        ]
    )
    notes = ans.build_honesty_notes(
        saw_research_fetch=any(tool == "research.fetch" for tool, _count, _detail in groups),
        network_mode="curated",
        evidence_kinds={},
        answer_claim_count=1,
        failed_actions=failed,
    )
    text = " ".join(notes)
    assert ui_labels.describe("honesty_note", "no_external_sources")["label"] in text
    assert ui_labels.describe("honesty_note", "external_sources_used")["label"] not in text
    # формулировка не отрицает самого факта поиска: она про чтение страниц
    hint = ui_labels.describe("honesty_note", "no_external_sources")["hint"]
    assert "чтения" in hint and "поиск" not in hint.lower()


def test_search_then_fetch_is_reported_as_used_external_source() -> None:
    events = [
        _event(1, "question_selected"),
        *_search_event(2, json.dumps({"query": "стоимость владения сервером"}), action_id="s1"),
        _event(6, "action_started", tool="research.fetch", action_id="f1", arguments='{"url":"https://example.com/a"}'),
        _event(7, "action_completed", action_id="f1", ok=True),
    ]
    groups, failed = ans.executed_actions(events)
    notes = ans.build_honesty_notes(
        saw_research_fetch=any(tool == "research.fetch" for tool, _count, _detail in groups),
        network_mode="curated",
        evidence_kinds={"source_assertion": 1},
        answer_claim_count=1,
        failed_actions=failed,
    )
    text = " ".join(notes)
    assert ui_labels.describe("honesty_note", "external_sources_used")["label"] in text
    assert ui_labels.describe("honesty_note", "no_external_sources")["label"] not in text
