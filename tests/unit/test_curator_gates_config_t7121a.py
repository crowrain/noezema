"""Unit (T7.121a часть д5, т5): включение гейтов честности записи конфигурацией.

Гейты периода/даты прогноза/даты документа включаются необязательным булевым ключом
`session_limits.curator_record_gates` (тот же путь опционального ключа, что у T7.119):
`true` — гейты работают; отсутствие ключа (payload'ы до config-v28) или `false` — гейты
молчат, поведение прежнее. Ключ — ВНУТРИ session_limits (новая верхняя секция payload =
колонка config_snapshots = миграция БД — вне задачи), schema_version остаётся 1.
Гейт склейки дублей (T7.121) и гейт значения (T7.108) от конфигурации не зависят.
Сценарная часть (принятие/снятие одной и той же операции без ключа и с ключом) —
tests/scenario/test_curator_gates_config_session_t7121a.py.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.domain.config import optional_session_limits_problems

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
V27_PAYLOAD = REPO_ROOT / "docs/eval/config-v27-payload.json"
V28_PAYLOAD = REPO_ROOT / "docs/eval/config-v28-payload.json"


def test_bool_key_accepted_absent_accepted() -> None:
    assert optional_session_limits_problems({"curator_record_gates": True}) == []
    assert optional_session_limits_problems({"curator_record_gates": False}) == []
    # отсутствие ключа — прежнее поведение payload'ов до config-v28, не проблема
    assert optional_session_limits_problems({"max_explorer_steps": 16}) == []


def test_non_bool_key_rejected_naming_the_key() -> None:
    expected = "session_limits.curator_record_gates: должно быть булевым значением"
    for bad in ("true", 1, 0, ["yes"], json):
        problems = optional_session_limits_problems({"curator_record_gates": bad})
        assert problems == [expected], bad


def test_v28_limits_validate_v27_limits_stay_valid() -> None:
    """Payload config-v28 проходит валидацию необязательных ключей вместе с новым ключом;
    payload config-v27 (без ключа) остаётся корректным — обратной совместимости ничего не
    требует (откатом v28 служит v27, он не менялся)."""
    v27 = json.loads(V27_PAYLOAD.read_text(encoding="utf-8"))
    v28 = json.loads(V28_PAYLOAD.read_text(encoding="utf-8"))
    assert optional_session_limits_problems(v27["session_limits"]) == []
    assert optional_session_limits_problems(v28["session_limits"]) == []
    assert v28["session_limits"].get("curator_record_gates") is True
    assert "curator_record_gates" not in v27["session_limits"]
