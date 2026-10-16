"""Unit (T7.119 часть 4): payload config-v27 = config-v26 ровно с правками session_limits.

Проверка байтовой аккуратности: все верхние секции, кроме session_limits, — байт в байт как в
config-v26 (канонические JSON-представления равны); в session_limits изменились ровно четыре
ключа (новый лимит 20, новые таймауты 2400/2700 по замеру STATUS T7.118, каталог из части 3).
`schema_version` не меняется (1), пины промптов не меняются (explorer-v11 не тронут — промпт
исследователя задачей не изменяется), канонический хэш нового payload закреплен. config-v26 не
изменён и остаётся откатом v27.

Конфиг НЕ активируется этим коммитом: активация — решение менеджера (как для v21…v26).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Final

import pytest

from apps.orchestrator.research_hints import parse_independent_hints
from packages.domain.canonical import canonical_json_bytes, canonical_sha256
from packages.domain.config import optional_session_limits_problems
from packages.llm_gateway.roles import Role, resolve_prompts

# прежнее закрепление (T7.111 часть 3) — переиспользуется, не дублируется
from tests.unit.test_explorer_v11_config_v26_t7111 import (
    V26_CANONICAL_SHA256,
    V26_FILE_SHA256,
)

pytestmark = [pytest.mark.unit]

REPO_ROOT = Path(__file__).resolve().parents[2]
V26_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v26-payload.json"
V27_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v27-payload.json"

# канонический хэш payload (canonical_sha256 из packages/domain/canonical.py) и хэш файла
V27_CANONICAL_SHA256: Final = "3f8160f8eababe5149027cdacdbe1f2710a70fc885324ae7c08f02421da77562"
V27_FILE_SHA256: Final = "edea91f0892f7b4ed6cf980188f5592048e47d0c7c6124af64eab28a9a71bbd6"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v26_untouched_and_v27_hashes_pinned() -> None:
    v26, v27 = _load(V26_PAYLOAD), _load(V27_PAYLOAD)
    assert canonical_sha256(v26) == V26_CANONICAL_SHA256, "config-v26 изменился — этого быть не должно"
    assert hashlib.sha256(V26_PAYLOAD.read_bytes()).hexdigest() == V26_FILE_SHA256
    assert canonical_sha256(v27) == V27_CANONICAL_SHA256
    assert hashlib.sha256(V27_PAYLOAD.read_bytes()).hexdigest() == V27_FILE_SHA256


def test_v27_differs_from_v26_only_in_session_limits() -> None:
    v26, v27 = _load(V26_PAYLOAD), _load(V27_PAYLOAD)
    assert set(v26) == set(v27)
    changed_sections = [name for name in v26 if v26[name] != v27[name]]
    assert changed_sections == ["session_limits"]
    # все прочие секции — байт в байт (каноническое представление каждого subtree одинаково)
    for name in v26:
        if name != "session_limits":
            assert canonical_json_bytes(v26[name]) == canonical_json_bytes(v27[name]), name


def test_v27_session_limits_changed_keys_are_exactly_listed() -> None:
    limits26 = _load(V26_PAYLOAD)["session_limits"]
    limits27 = _load(V27_PAYLOAD)["session_limits"]
    extra_keys = set(limits27) - set(limits26)
    assert extra_keys == {"max_explorer_steps_official_independent", "research_hints"}
    changed = {name for name in set(limits26) | set(limits27) if limits26.get(name) != limits27.get(name)}
    assert changed == {
        "max_explorer_steps_official_independent",
        "phase_deadline_seconds",
        "research_hints",
        "session_timeout_seconds",
    }
    assert limits27["max_explorer_steps"] == 16  # прежний обычный лимит не тронут
    assert limits27["max_explorer_steps_official_independent"] == 20
    # новые таймауты по замеру (STATUS T7.118: 16 шагов занимали 705–1459 с; 20 шагов
    # в наихудшем замере ~1824 с > 1800 — прежний дедлайн не покрывал новый потолок)
    assert limits26["phase_deadline_seconds"] == 1800 and limits27["phase_deadline_seconds"] == 2400
    assert limits26["session_timeout_seconds"] == 1800 and limits27["session_timeout_seconds"] == 2700


def test_v27_schema_and_prompt_pins_unchanged() -> None:
    v26, v27 = _load(V26_PAYLOAD), _load(V27_PAYLOAD)
    assert v26["schema_version"] == v27["schema_version"] == 1
    assert v26["prompts"] == v27["prompts"]  # промпт исследователя не менялся
    loaded = resolve_prompts(v27["prompts"], REPO_ROOT)  # пины резолвятся с диска
    assert loaded[Role.EXPLORER].version == "explorer-v11"


def test_v27_optional_keys_validate_and_catalog_parses() -> None:
    limits27 = _load(V27_PAYLOAD)["session_limits"]
    assert optional_session_limits_problems(limits27) == []
    topics, problems = parse_independent_hints(limits27)
    assert problems == []
    assert [topic.topic_id for topic in topics] == ["labor_wages", "labor_unemployment", "inflation"]


def test_dev_stand_scripts_do_not_activate_config_v27() -> None:
    """Как и для v21…v26 (T7.97, T7.111): скрипты dev-стенда не активируют новый payload —
    дефолт bootstrap'а не меняется, активация config-v27 — решение менеджера."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    assert scripts
    for script in scripts:
        text = script.read_text(encoding="utf-8")
        assert "config-v27" not in text, script.name
