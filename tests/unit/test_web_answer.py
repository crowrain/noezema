"""Unit: сборка карточки ответа (`apps/web/answer.py`) — чистые построители (T7.65).

База не нужна: здесь проверяются три правила, на которых стоит страница ответа.

1. Шаги «как это получено» берутся только из ленты событий и только по
   `sequence` (AGENTS §7: время строк одной транзакции одинаковое), шаг = реально
   выполненное действие известного инструмента; повтор сворачивается, больше шести
   шагов не бывает, ничего не додумывается.
2. Честные замечания включаются только когда их условие видно в данных.
3. Ни один текст ответа не содержит кодов перечислений, ссылок на спецификацию и
   номера задач плана: экран говорит словами словаря.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from apps.web import answer as ans
from apps.web import labels as ui_labels

pytestmark = pytest.mark.unit


def _event(sequence: int, type_: str, **payload: Any) -> dict[str, Any]:
    return {"sequence": sequence, "type": type_, "payload": payload}


#: Лента реальной сессии с вычислением (замер T7.65): сокращена до значимых событий.
FULL_RUN: list[dict[str, Any]] = [
    _event(1, "session_started"),
    _event(5, "question_selected", question_id="11111111-1111-4111-8111-111111111111"),
    _event(7, "context_packed", total_tokens=1200),
    _event(9, "policy_evaluated", tool="python.execute", decision="allow"),
    _event(10, "action_started", tool="python.execute", action_id="a1", arguments='{"code":"print(6*7)"}'),
    _event(11, "action_completed", action_id="a1", ok=True, data='{"stdout":"42\\n"}'),
    _event(14, "staging_recorded", op="claim"),
    _event(17, "claim_created", claims=[{"statement": "6*7 равно 42"}]),
    _event(25, "session_committed", claims=1, terminal="succeeded"),
]


# ─── шаги работы ──────────────────────────────────────────────────────────


def test_full_run_steps_are_plain_russian_and_ordered_by_sequence() -> None:
    steps = ans.build_answer_steps(FULL_RUN)
    texts = [step["text"] for step in steps]
    assert steps[0]["n"] == 1
    assert texts[0] == ui_labels.describe("answer_step", "question_selected")["label"]
    assert "подготовил память" in texts[1]
    assert any("вычислени" in text for text in texts)
    assert texts[-1] == ui_labels.describe("answer_step", "recorded")["label"]
    assert len(steps) <= ans.MAX_STEPS


def test_step_order_follows_sequence_even_when_rows_come_shuffled() -> None:
    """Порядок строк на входе — не хронология: построитель сортирует по `sequence`."""
    shuffled = [FULL_RUN[8], FULL_RUN[3], FULL_RUN[6], FULL_RUN[0], FULL_RUN[5], FULL_RUN[4], FULL_RUN[1], FULL_RUN[2]]
    texts = [step["text"] for step in ans.build_answer_steps(shuffled)]
    selected = ui_labels.describe("answer_step", "question_selected")["label"]
    recorded = ui_labels.describe("answer_step", "recorded")["label"]
    assert texts[0] == selected and texts[-1] == recorded


def test_repeated_actions_collapse_into_one_step_with_a_real_count() -> None:
    events = [
        _event(1, "question_selected"),
        *[
            _event(10 + 2 * i, "action_started", tool="python.execute", action_id=f"a{i}")
            for i in range(3)
        ],
        *[_event(11 + 2 * i, "action_completed", action_id=f"a{i}", ok=True) for i in range(3)],
    ]
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    tool_label = ui_labels.describe("action_tool", "python.execute")["label"]
    assert [text for text in texts if text.startswith(tool_label)] == [f"{tool_label} — 3 раза"]
    assert "1 раз" not in " ".join(texts)


def test_many_actions_merge_into_one_step_and_the_cap_holds() -> None:
    tools = ["workspace.read", "workspace.list", "python.execute", "memory.search", "shell.execute"]
    events: list[dict[str, Any]] = [_event(1, "question_selected"), _event(2, "context_packed")]
    for index, tool in enumerate(tools):
        events.append(_event(10 + 2 * index, "action_started", tool=tool, action_id=f"a{index}"))
        events.append(_event(11 + 2 * index, "action_completed", action_id=f"a{index}", ok=True))
    events += [_event(40, "claim_created"), _event(41, "session_committed")]

    steps = ans.build_answer_steps(events)
    assert len(steps) <= ans.MAX_STEPS
    merged = ui_labels.describe("answer_step", "merged_actions")["label"]
    assert any(text.startswith(merged) and text.endswith(": 5") for text in (s["text"] for s in steps))


def test_failed_and_aborted_actions_never_become_steps() -> None:
    events = [
        _event(1, "question_selected"),
        _event(2, "action_started", tool="python.execute", action_id="a1"),
        _event(3, "action_completed", action_id="a1", ok=False),
        _event(4, "action_started", tool="memory.search", action_id="a2"),
        _event(5, "action_failed", action_id="a3"),
    ]
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    assert not any("вычислени" in text or "памят" in text for text in texts)
    groups, failed = ans.executed_actions(events)
    assert groups == [] and failed == 1


def test_unknown_tool_does_not_get_an_invented_name() -> None:
    """Ловушка AGENTS §7: снапшот предлагает инструменты, которых нет у исполнителя."""
    events = [
        _event(1, "question_selected"),
        _event(2, "action_started", tool="totally.new_tool", action_id="a1"),
        _event(3, "action_completed", action_id="a1", ok=True),
    ]
    texts = [step["text"] for step in ans.build_answer_steps(events)]
    joined = " ".join(texts)
    assert "totally.new_tool" not in joined
    assert ui_labels.describe("answer_step", "unnamed_action")["label"] in joined


def test_empty_or_partial_feed_produces_no_invented_steps() -> None:
    assert ans.build_answer_steps([]) == []
    # событие без payload и без известной фазы: шагов не появляется
    assert ans.build_answer_steps([{"sequence": 1, "type": "session_started", "payload": None}]) == []
    # action_started без исхода — действие не подтверждено, шага нет
    assert ans.build_answer_steps([_event(2, "action_started", tool="python.execute")]) == []


def test_research_fetch_step_names_only_the_domain_that_is_in_the_record() -> None:
    events = [
        _event(1, "question_selected"),
        _event(2, "action_started", tool="research.fetch", action_id="a1", arguments='{"url":"https://example.com/a/b?x=1"}'),
        _event(3, "action_completed", action_id="a1", ok=True),
    ]
    text = ans.build_answer_steps(events)[1]["text"]
    assert text.endswith("example.com")
    assert "/a/b" not in text and "x=1" not in text


@pytest.mark.parametrize(
    ("arguments", "expected"),
    [
        ('{"url":"https://Example.COM/path"}', "example.com"),
        ('{"url":"https://some_host.example.com"}', "some-host.example.com"),
        ('{"url":"https://0123456789abcdef.example.com"}', ""),
        ("не url вообще", ""),
        (None, ""),
    ],
)
def test_domain_extraction_is_taken_from_the_record_or_not_shown(arguments: Any, expected: str) -> None:
    assert ans.host_of_url(arguments) == expected


def test_times_phrase_russian_pluralization() -> None:
    assert ans.times_phrase(1) == ""
    assert ans.times_phrase(2) == "2 раза"
    assert ans.times_phrase(5) == "5 раз"
    assert ans.times_phrase(12) == "12 раз"
    assert ans.times_phrase(21) == "21 раз"


# ─── честные замечания ────────────────────────────────────────────────────


def test_external_source_note_follows_the_feed_and_the_snapshot() -> None:
    closed = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={"computation": 1},
        answer_claim_count=1,
        failed_actions=0,
    )
    assert ui_labels.describe("honesty_note", "no_external_sources_closed")["label"] in closed
    assert all("не использовались" not in note for note in closed)

    open_mode = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="research_proxy",
        evidence_kinds={"source_assertion": 1},
        answer_claim_count=1,
        failed_actions=0,
    )
    assert ui_labels.describe("honesty_note", "no_external_sources")["label"] in open_mode

    fetched = ans.build_honesty_notes(
        saw_research_fetch=True,
        network_mode="research_proxy",
        evidence_kinds={"source_assertion": 2},
        answer_claim_count=1,
        failed_actions=0,
    )
    joined = " ".join(fetched)
    assert ui_labels.describe("honesty_note", "external_sources_used")["label"] in joined
    assert "не использовались" not in joined and "сеть закрыта" not in joined


def test_computation_note_only_when_every_support_is_a_computation() -> None:
    only_math = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={"computation": 2},
        answer_claim_count=1,
        failed_actions=0,
    )
    assert ui_labels.describe("honesty_note", "computation_only")["label"] in only_math

    mixed = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={"computation": 2, "source_assertion": 1},
        answer_claim_count=1,
        failed_actions=0,
    )
    assert all("вычислени" not in note for note in mixed)

    nothing = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={},
        answer_claim_count=1,
        failed_actions=0,
    )
    assert ui_labels.describe("honesty_note", "no_evidence")["label"] in nothing


def test_failed_steps_note_names_the_number_only() -> None:
    notes = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={"computation": 1},
        answer_claim_count=1,
        failed_actions=2,
    )
    base = ui_labels.describe("honesty_note", "failed_steps")["label"]
    assert f"{base}: 2" in notes


# ─── итог по вопросу ──────────────────────────────────────────────────────


def _session(state: str, reason: str | None = None) -> dict[str, Any]:
    return {"state": state, "termination_reason": reason}


@pytest.mark.parametrize(
    ("question_state", "sessions", "claims", "expected"),
    [
        ("candidate", [], 0, "waiting"),
        ("candidate", [_session("exploring")], 0, "in_progress"),
        ("researching", [], 0, "in_progress"),
        ("verified", [_session("succeeded", "goal_reached")], 1, "answered"),
        ("rejected", [_session("failed", "no_question")], 0, "failed"),
        ("candidate", [_session("succeeded", "goal_reached")], 0, "no_answer"),
    ],
)
def test_result_kind_and_active_flag(
    question_state: str, sessions: list[dict[str, Any]], claims: int, expected: str
) -> None:
    result = ans.build_answer_result(
        question_state=question_state, sessions=sessions, answer_claim_count=claims
    )
    assert result["kind"] == expected
    assert result["active"] is (expected == "in_progress")
    entry = ui_labels.describe("answer_result", expected)
    assert result["label"] == entry["label"] and entry["action"] == result["action"]


def test_result_hint_appends_only_known_labels() -> None:
    known = ans.build_answer_result(
        question_state="rejected",
        sessions=[_session("failed", "attempt_aborted")],
        answer_claim_count=0,
    )
    assert "Последняя сессия:" in known["hint"]
    unknown = ans.build_answer_result(
        question_state="rejected",
        sessions=[_session("failed", "some_brand_new_reason")],
        answer_claim_count=0,
    )
    # неизвестный код не должен просочиться на экран как «подпись»
    assert "some_brand_new_reason" not in unknown["hint"]


def test_known_label_refuses_to_invent_a_label() -> None:
    assert ans.known_label("session_state", "exploring") == ui_labels.describe("session_state", "exploring")["label"]
    assert ans.known_label("session_state", "totally_new_state") == ""


# ─── связь вопроса с утверждением (T7.74) ─────────────────────────────────


def test_reverified_and_reused_claims_are_an_answer_too() -> None:
    """Действующее утверждение, которое сессия вопроса перепроверила или приняла повторно,
    — ответ вопроса: «Ответ не записан» был бы неправдой."""
    sessions = [_session("succeeded", "goal_reached")]
    for relation, expected in (
        ("created", "answered"),
        ("reverified", "reverified"),
        ("reused", "reused"),
    ):
        result = ans.build_answer_result(
            question_state="verified",
            sessions=sessions,
            answer_claim_count=1,
            relations=[relation],
        )
        assert result["kind"] == expected
        entry = ui_labels.describe("answer_result", expected)
        assert result["label"] == entry["label"] and result["action"] == entry["action"]
        assert result["active"] is False


def test_partial_work_is_called_partial_not_whole() -> None:
    """Частичность берётся из состояния вопроса или исхода последней сессии (T7.34)."""
    by_question = ans.build_answer_result(
        question_state="partially_answered",
        sessions=[_session("succeeded", "goal_reached")],
        answer_claim_count=1,
        relations=["reverified"],
    )
    by_session = ans.build_answer_result(
        question_state="verified",
        sessions=[_session("succeeded_partial", "budget_exhausted")],
        answer_claim_count=1,
        relations=["reverified"],
    )
    whole = ans.build_answer_result(
        question_state="verified",
        sessions=[_session("succeeded", "goal_reached")],
        answer_claim_count=1,
        relations=["reverified"],
    )
    assert by_question["kind"] == "partially_reverified"
    assert by_session["kind"] == "partially_reverified"
    assert whole["kind"] == "reverified"
    # живая сессия — не «частично»: работа ещё идёт, и это другой итог
    running = ans.build_answer_result(
        question_state="researching",
        sessions=[_session("exploring", None)],
        answer_claim_count=0,
        relations=["reverified"],
    )
    assert running["kind"] == "in_progress" and running["active"] is True


def test_relation_precedence_picks_the_strongest_link() -> None:
    """Созданное важнее перепроверенного, перепроверенное — принятого повторно."""
    assert ans.relation_kind(["reused", "reverified", "created"]) == "created"
    assert ans.relation_kind(["reused", "reverified"]) == "reverified"
    assert ans.relation_kind(["reused"]) == "reused"
    assert ans.relation_kind([]) is None
    # выдуманных отношений не бывает: неизвестное значение игнорируется
    assert ans.relation_kind(["made_up_relation"]) is None


def test_relations_act_only_when_there_is_an_answer_claim() -> None:
    """Без действующего утверждения итог остаётся прежним: отношения его не сочиняют."""
    for relations in (["reverified"], ["reused"], ["created"]):
        result = ans.build_answer_result(
            question_state="verified",
            sessions=[_session("succeeded", "goal_reached")],
            answer_claim_count=0,
            relations=relations,
        )
        assert result["kind"] == "no_answer"


def _history_row(
    *,
    claim_id: str,
    assessment_id: str,
    new_grade: str,
    new_status: str,
    old_grade: str | None,
    old_status: str | None,
) -> dict[str, Any]:
    return {
        "claim_id": claim_id,
        "assessment_id": assessment_id,
        "session_id": "11111111-1111-4111-8111-111111111111",
        "effective_grade": new_grade,
        "epistemic_status": new_status,
        "old_id": "22222222-2222-4222-8222-222222222222" if old_grade else None,
        "old_grade": old_grade,
        "old_status": old_status,
    }


def test_history_is_written_only_when_the_assessment_changed() -> None:
    """«Было → стало» появляется только когда оценка действительно изменилась."""
    claim_a, claim_b = "aaaaaaaa-0000-4000-8000-000000000001", "aaaaaaaa-0000-4000-8000-000000000002"
    rows = [
        _history_row(
            claim_id=claim_a,
            assessment_id="bbbbbbbb-0000-4000-8000-000000000001",
            new_grade="E1",
            new_status="hypothesis",
            old_grade="E3",
            old_status="supported",
        ),
        _history_row(
            claim_id=claim_b,
            assessment_id="bbbbbbbb-0000-4000-8000-000000000002",
            new_grade="E3",
            new_status="supported",
            old_grade="E3",
            old_status="supported",
        ),
    ]
    history = ans.reverify_history_view(rows, {})

    assert list(history) == [claim_a]
    entry = history[claim_a][0]
    assert entry["from"]["grade"] == "E3" and entry["to"]["grade"] == "E1"
    assert entry["from"]["grade_label"] == ui_labels.describe("evidence_grade", "E3")["label"]
    assert entry["to"]["status_label"] == ui_labels.describe("epistemic_status", "hypothesis")["label"]
    assert "было:" in entry["text"] and "стало:" in entry["text"]
    # прежней оценки нет — историю выдумывать нечем
    assert claim_b not in history


def test_history_explains_both_assessments_with_known_reasons() -> None:
    """Причины обеих оценок подписаны словарём (T7.73), их берёт тот же построитель."""
    claim_id = "aaaaaaaa-0000-4000-8000-000000000003"
    reason_code = next(iter(ui_labels.LABELS["assessment_reason"]))
    rows = [
        _history_row(
            claim_id=claim_id,
            assessment_id="bbbbbbbb-0000-4000-8000-000000000003",
            new_grade="E1",
            new_status="hypothesis",
            old_grade="E3",
            old_status="supported",
        )
    ]
    history = ans.reverify_history_view(
        rows,
        {
            "22222222-2222-4222-8222-222222222222": [reason_code],
            "bbbbbbbb-0000-4000-8000-000000000003": [reason_code],
        },
    )
    entry = history[claim_id][0]
    assert [item["label"] for item in entry["from_reasons"]] == [
        ui_labels.describe("assessment_reason", reason_code)["label"]
    ]
    assert [item["label"] for item in entry["reasons"]] == [
        ui_labels.describe("assessment_reason", reason_code)["label"]
    ]


def test_history_without_a_known_label_is_not_shown() -> None:
    """Нет подписи у оценки — нет и строки: витрина не называет grade своими словами."""
    rows = [
        _history_row(
            claim_id="aaaaaaaa-0000-4000-8000-000000000004",
            assessment_id="bbbbbbbb-0000-4000-8000-000000000004",
            new_grade="E9",
            new_status="supported",
            old_grade="E3",
            old_status="supported",
        )
    ]
    assert ans.reverify_history_view(rows, {}) == {}


def _outcome_texts() -> list[str]:
    """Тексты итога и истории оценки: обещать проверку они не имеют права."""
    texts: list[str] = []
    for kind, question_state, session in (
        ("reverified", "answered", _session("succeeded", "goal_reached")),
        ("partially_reverified", "partially_answered", _session("succeeded", "goal_reached")),
        ("reused", "answered", _session("succeeded", "goal_reached")),
    ):
        result = ans.build_answer_result(
            question_state=question_state,
            sessions=[session],
            answer_claim_count=1,
            relations=["reused" if kind == "reused" else "reverified"],
        )
        assert result["kind"] == kind
        entry = ui_labels.describe("answer_result", kind)
        texts.extend([result["label"], result["hint"], entry["hint"], entry["action"]])
    history = ans.reverify_history_view(
        [
            _history_row(
                claim_id="aaaaaaaa-0000-4000-8000-000000000005",
                assessment_id="bbbbbbbb-0000-4000-8000-000000000005",
                new_grade="E1",
                new_status="disputed",
                old_grade="E3",
                old_status="supported",
            )
        ],
        {},
    )
    for entry in history.values():
        for item in entry:
            texts.append(item["text"])
            for reason in [*item["from_reasons"], *item["reasons"]]:
                texts.extend([reason["label"], reason["hint"], reason["action"]])
    return texts


def _relation_texts() -> list[str]:
    texts: list[str] = []
    for relation in ans.RELATION_KEYS:
        entry = ui_labels.describe("claim_relation", relation)
        texts.extend([entry["label"], entry["hint"], entry["action"]])
    return texts


def test_relation_and_history_texts_carry_no_codes_or_verified_word() -> None:
    """Связь вопроса с утверждением и история оценки — только слова словаря (T7.65, T7.74).

    Итог и история не имеют права утверждать проверку: «проверено» имеет один источник —
    бейдж надёжности. Отношение «перепроверено этим вопросом» описывает СВЯЗЬ вопроса с
    уже записанным утверждением, а не обещает проверку; отдельное слово «проверено»
    запрещено и там (тот же lookbehind-скан, что у карточки).
    """
    outcome = "\n".join(_outcome_texts())
    relation = "\n".join(_relation_texts())
    assert outcome and relation
    for pattern in _FORBIDDEN:
        assert not pattern.search(outcome), f"{pattern.pattern} в итоге/истории: {outcome}"
        assert not pattern.search(relation), f"{pattern.pattern} в подписи связи: {relation}"
    lowered = outcome.lower()
    for fragment in ("проверен", "проверено"):
        assert fragment not in lowered, f"утверждение о проверке в итоге/истории: {lowered}"
    affirming = re.compile(r"(?<!не )\bпроверен")
    offenders = [text for text in _relation_texts() if affirming.search(text.lower())]
    assert not offenders, f"подпись связи утверждает проверку: {offenders}"


# ─── чистота текстов ответа ───────────────────────────────────────────────

_FORBIDDEN = (
    re.compile(r"§"),
    re.compile(r"\bT\d+\.\d+"),
    re.compile(r"[a-z]+_[a-z]+"),
    re.compile(r"\b[0-9a-fA-F]{8,}\b"),
)


def _answer_texts() -> list[str]:
    steps = [step["text"] for step in ans.build_answer_steps(FULL_RUN)]
    result = ans.build_answer_result(
        question_state="verified",
        sessions=[_session("succeeded", "goal_reached")],
        answer_claim_count=1,
    )
    honesty = ans.build_honesty_notes(
        saw_research_fetch=False,
        network_mode="none",
        evidence_kinds={"computation": 1},
        answer_claim_count=1,
        failed_actions=0,
    )
    return [*steps, result["label"], result["hint"], result["action"], *honesty]


def test_visible_answer_texts_carry_no_codes_or_spec_references() -> None:
    texts = _answer_texts()
    assert texts
    joined = "\n".join(texts)
    for pattern in _FORBIDDEN:
        assert not pattern.search(joined), f"{pattern.pattern} в тексте ответа: {joined}"


def test_verified_word_never_appears_in_answer_building_blocks() -> None:
    """Слово «проверено» имеет источник — бейдж надёжности (reliability.py).

    Построители шагов, итога и замечаний не имеют права выдавать проверку:
    шаг-сверка возможна только как факт события ленты и называется «сверил».
    """
    joined = "\n".join(_answer_texts()).lower()
    assert "проверен" not in joined
    assert "проверено" not in joined


def test_answer_keys_are_declared_and_labelled() -> None:
    """Каждый ключ построителя имеет подпись в словаре (иначе тест полноты краснеет)."""
    for key in ans.STEP_KEYS:
        assert ui_labels.describe("answer_step", key)["hint"], f"нет подписи шага: {key}"
    for kind in ans.RESULT_KINDS:
        assert ui_labels.describe("answer_result", kind)["hint"], f"нет подписи итога: {kind}"
    for key in ans.HONESTY_KEYS:
        assert ui_labels.describe("honesty_note", key)["hint"], f"нет подписи замечания: {key}"
