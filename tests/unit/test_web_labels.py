"""Unit: единый серверный словарь подписей (T7.64, ADR-0026).

Два инварианты проверяются здесь без базы:

1. ПОЛНОТА — каждое значение каждого пользовательского перечисления, каждой
   причины отказа/пропуска запуска и закрытого набора termination_reason имеет
   подпись. Источник значений — сам код (перечисления домена, константы
   планировщика, строки отказов Command API), поэтому новое значение enum без
   подписи краснит тест, а не тихо уходит в запасной путь.
2. ТЕКСТ — подписи читаемы по-русски, укладываются в длины и не содержат
   ссылок на спецификацию, номера задач плана, коды перечислений, uuid/hex.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import get_args

import pytest

import apps.orchestrator.scheduler as scheduler
import apps.web.api as web_api
import apps.web.host_status as host_status
from apps.web import labels
from apps.web.labels import LABELS, MAX_ACTION_CHARS, MAX_HINT_CHARS, MAX_LABEL_CHARS
from packages.domain.models.enums import (
    AssessmentEvidenceRole,
    AssessmentState,
    AuditEventType,
    BarrierStatus,
    ClaimType,
    CommitAttemptStatus,
    CompleteReason,
    DependencyKind,
    EffectiveGrade,
    EpistemicStatus,
    EvidenceKind,
    EvidenceRelation,
    FreshnessStatus,
    MessageState,
    NodeState,
    OperatorCommandState,
    OperatorCommandType,
    QuestionOrigin,
    QuestionState,
    ReassessmentJobStatus,
    SessionState,
)
from packages.domain.services.commit import FinalizeOutcome

pytestmark = pytest.mark.unit


# ─── источники значений: берутся из кода, а не переписываются в тест ──────

_RECOVERY_CONSTANTS = sorted(
    value
    for name, value in vars(host_status).items()
    if name.startswith("RECOVERY_") and isinstance(value, str)
)

_SCHEDULER_REASONS = sorted(
    value
    for name, value in vars(scheduler).items()
    if name.startswith(("REASON_", "WAIT_")) and isinstance(value, str)
)

# Закрытые литералы причин остановки, которые пишет оркестратор и сверитель
# (apps/orchestrator/orchestrator.py, apps/orchestrator/reconciler.py).
# Строки `commit_<итог>` берутся из FinalizeOutcome, а не на слух.
_ORCHESTRATOR_TERMINATIONS = (
    "no_question",
    "operator_abort",
    "unknown_action_outcome",
    "commit_boundary_error",
)

# Закрытые причины автопаузы: apps/web/api.py (_set_pause_reason "operator") и
# apps/orchestrator/scheduler.py (paused_reason="consecutive_failures").
_PAUSED_REASONS = ("operator", "consecutive_failures")


def _sources() -> dict[str, list[str]]:
    return {
        "question_state": [e.value for e in QuestionState],
        "question_origin": [e.value for e in QuestionOrigin],
        "session_state": [e.value for e in SessionState],
        "node_state": [*web_api.NODE_STATES, *(e.value for e in NodeState)],
        "epistemic_status": [e.value for e in EpistemicStatus],
        "freshness_status": [e.value for e in FreshnessStatus],
        "claim_type": [e.value for e in ClaimType],
        # 'none' синтезирует knowledge.py (COALESCE), rest = AssessmentState
        "claim_head_state": [*[e.value for e in AssessmentState], "none"],
        "evidence_grade": [e.value for e in EffectiveGrade],
        "command_type": [e.value for e in OperatorCommandType],
        "command_state": [e.value for e in OperatorCommandState],
        "wake_reason": _SCHEDULER_REASONS,
        "termination_reason": [
            *(e.value for e in CompleteReason),
            *(f"commit_{outcome}" for outcome in get_args(FinalizeOutcome)),
            *_ORCHESTRATOR_TERMINATIONS,
        ],
        "audit_event_type": [e.value for e in AuditEventType],
        "evidence_kind": [e.value for e in EvidenceKind],
        "evidence_relation": [e.value for e in EvidenceRelation],
        "assessment_evidence_role": [e.value for e in AssessmentEvidenceRole],
        "dependency_kind": [e.value for e in DependencyKind],
        "barrier_status": [e.value for e in BarrierStatus],
        "reassessment_job_status": [e.value for e in ReassessmentJobStatus],
        "commit_attempt_status": [e.value for e in CommitAttemptStatus],
        "message_state": [e.value for e in MessageState],
        "recovery_state": _RECOVERY_CONSTANTS,
        "paused_reason": list(_PAUSED_REASONS),
    }


def _refusal_literals_from_code() -> list[str]:
    """Строки отказа Command API, найденные в исходнике apps/web/api.py.

    Тест не переписывает фразы: он вытаскивает их из рабочего кода, поэтому
    новая фраза отказа без подписи краснит проверку.
    """
    source = Path(web_api.__file__).read_text(encoding="utf-8")
    literals = re.findall(r'"reason": "([^"]+)"', source)
    prefixes = re.findall(r'"reason": f"([^"{]+)', source)
    return sorted({*literals, *prefixes})


# ─── полнота ──────────────────────────────────────────────────────────────


def test_sources_are_not_empty_and_categories_exist() -> None:
    sources = _sources()
    assert sources
    for category in sources:
        assert category in LABELS, f"нет категории подписей: {category}"
        assert LABELS[category], f"пустая категория подписей: {category}"


def test_every_source_value_has_a_label() -> None:
    """Ни одно реальное значение не должно доходить до запасного пути."""
    missing: list[tuple[str, str]] = []
    for category, values in _sources().items():
        table = LABELS[category]
        for value in values:
            entry = table.get(value)
            if entry is None or not entry["label"] or not entry["hint"]:
                missing.append((category, value))
    assert not missing, f"значения без подписи: {missing}"


def test_completeness_categories_cover_the_whole_dictionary() -> None:
    assert set(labels.COMPLETENESS_CATEGORIES) == set(LABELS)


def test_scheduler_reasons_are_exposed_as_a_closed_set() -> None:
    """Причины допуска/расписания — закрытый набор планировщика: он и есть
    источник для категории подписей `wake_reason`."""
    assert _SCHEDULER_REASONS
    for reason in _SCHEDULER_REASONS:
        assert labels.describe_reason(reason)["hint"], f"нет подписи причины: {reason}"


def test_command_refusal_literals_from_api_code_are_labeled() -> None:
    literals = _refusal_literals_from_code()
    assert literals, "в Command API не найдено ни одной строки отказа — тест сломан"
    unlabeled = [
        reason
        for reason in literals
        if not labels.describe_refusal(reason)["hint"]
    ]
    assert not unlabeled, f"отказы без подписи: {unlabeled}"


def test_audit_event_labels_cover_every_audit_type() -> None:
    table = LABELS["audit_event_type"]
    uncovered = [e.value for e in AuditEventType if e.value not in table]
    assert not uncovered, f"события ленты без подписи: {uncovered}"


# ─── тексты подписей ──────────────────────────────────────────────────────

_FORBIDDEN_PATTERNS: tuple[tuple[str, str], ...] = (
    ("ссылка на раздел спецификации", r"§"),
    ("номер задачи плана", r"\bT\d+\.\d+"),
    ("код перечисления (snake_case)", r"[a-z]+_[a-z]+"),
    ("sha/uuid-подобная строка", r"\b[0-9a-fA-F]{8,}\b"),
    ("uuid с дефисами", r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}"),
)

_LIMITS = (
    ("label", MAX_LABEL_CHARS),
    ("hint", MAX_HINT_CHARS),
    ("action", MAX_ACTION_CHARS),
)


@pytest.mark.parametrize("field,limit", _LIMITS)
def test_text_limits_and_non_emptiness(field: str, limit: int) -> None:
    offenders: list[str] = []
    for category, table in LABELS.items():
        for value, entry in table.items():
            text = entry[field]
            if not text.strip():
                offenders.append(f"{category}/{value}:{field} пусто")
            elif len(text) > limit:
                offenders.append(f"{category}/{value}:{field} {len(text)}>{limit}")
    assert not offenders, "; ".join(offenders)


def test_texts_have_no_spec_refs_codes_or_hashes() -> None:
    offenders: list[str] = []
    for category, table in LABELS.items():
        for value, entry in table.items():
            for field, _limit in _LIMITS:
                for name, pattern in _FORBIDDEN_PATTERNS:
                    if re.search(pattern, entry[field]):
                        offenders.append(f"{category}/{value}:{field} — {name}")
    assert not offenders, "; ".join(offenders)


def test_labels_are_not_raw_codes() -> None:
    """Подпись не должна быть просто копией кода (иначе экран покажет enum)."""
    copied = [
        f"{category}/{value}"
        for category, table in LABELS.items()
        for value, entry in table.items()
        if entry["label"] == value
    ]
    assert not copied, f"подпись совпадает с кодом: {copied}"


# ─── запасной путь и публичный API ────────────────────────────────────────


def test_unknown_value_falls_back_to_the_code_itself() -> None:
    entry = labels.describe("question_state", "something_new_in_a_future_version")
    assert entry == {
        "label": "something_new_in_a_future_version",
        "hint": "",
        "action": "",
    }
    # неизвестная категория — тот же безопасный путь
    assert labels.describe("no_such_category", "x")["label"] == "x"


def test_describe_returns_copies_not_the_table_itself() -> None:
    entry = labels.describe("question_state", QuestionState.CANDIDATE.value)
    entry["label"] = "испорчено"
    assert LABELS["question_state"][QuestionState.CANDIDATE.value]["label"] == "ждёт очереди"


def test_glossary_exposes_every_category_and_value() -> None:
    glossary = labels.glossary()
    assert set(glossary) == set(LABELS)
    for category, table in LABELS.items():
        assert set(glossary[category]) == set(table)
        # словарь для страницы — копии: правка на экране не портит модуль
        first = next(iter(table))
        glossary[category][first]["label"] = "испорчено"
        assert table[first]["label"] != "испорчено"


def test_glossary_entries_are_human_texts() -> None:
    for category, table in labels.glossary().items():
        for value, entry in table.items():
            assert set(entry) == {"label", "hint", "action"}, f"{category}/{value}"
            assert entry["label"] and entry["hint"]


# ─── шкала из пяти этапов сессии ──────────────────────────────────────────

_ALLOWED_STAGE_NAMES = {
    "подготовка",
    "выбор вопроса и план",
    "поиск ответа",
    "проверка и оформление",
    "запись результата",
    "готово",
    "готово частично",
    "не удалось",
    "остановлено по вашей команде",
    "остановка по команде",
    "прерывание",
}


def test_every_session_state_maps_to_a_human_stage() -> None:
    for state in SessionState:
        stage = labels.session_stage(state.value)
        assert stage["of"] == labels.STAGE_COUNT == 5
        assert stage["name"] in _ALLOWED_STAGE_NAMES, f"{state.value}: {stage['name']}"
        assert stage["index"] is None or 1 <= stage["index"] <= labels.STAGE_COUNT
        assert stage["hint"]
    # порядок этапов по ходу успешной сессии не убывает
    path = [
        "created",
        "waking",
        "orienting",
        "selecting_question",
        "planning",
        "exploring",
        "verifying",
        "consolidating",
        "reporting",
        "committing",
        "succeeded",
    ]
    indexes = [int(labels.session_stage(state)["index"]) for state in path]
    assert indexes == sorted(indexes)
    assert len(set(indexes)) == labels.STAGE_COUNT


def test_unknown_session_state_is_honest_about_the_scale() -> None:
    stage = labels.session_stage("some_future_state")
    assert stage["index"] is None
    assert stage["name"] == "some_future_state"
    assert stage["of"] == labels.STAGE_COUNT


# ─── нормализация строк отказа ────────────────────────────────────────────


def test_refusal_key_normalizes_known_phrases_and_prefixes() -> None:
    assert labels.refusal_key("a session is already running") == "session_already_running"
    assert labels.refusal_key("node is paused") == "node_paused"
    assert labels.refusal_key("no active session") == "no_active_session"
    # динамическая строка с префиксом получает общую подпись
    dynamic = labels.describe_refusal("session lane unavailable: database unavailable")
    assert dynamic["label"] == "не удалось занять линию сессий"
    assert dynamic["hint"] and dynamic["action"]


def test_admission_code_as_refusal_reason_still_gets_a_hint() -> None:
    """Command API может вернуть код допуска планировщика в поле `reason`."""
    for reason in ("nonterminal_session", "unresolved_commit_attempt", "reassessment_backlog"):
        entry = labels.describe_reason(reason)
        assert entry["label"] != reason
        assert entry["hint"] and entry["action"]
