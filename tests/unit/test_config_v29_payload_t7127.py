"""T7.127 (часть 2, тест 5а): config-v29 = config-v28 ровно с одной правкой лимита поиска узла.

Payload только ГОТОВИТСЯ: активация config-v29 — решение менеджера, задача её не делает (ни в
скриптах dev-стенда, ни через `activate-online`), стойку не трогает. Прежные payload'ы не
переписаны задним числом, а дефолт `ModePolicy.from_section` остаётся 20: меняется только
значение в файле config-v29 (число 60 — предложение по замеру STATUS/отчёта задачи, не факт).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.modes import ModePolicy
from packages.domain.canonical import canonical_sha256

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
V29_PAYLOAD = REPO_ROOT / "docs/eval/config-v29-payload.json"
V28_PAYLOAD = REPO_ROOT / "docs/eval/config-v28-payload.json"

# Канонический хеш (JCS) и хеш файла — РАЗНЫЕ числа (AGENTS §7 про цепочку config); пинуются
# оба: канон отвечает за содержимое, хеш файла — за то, что файл не правил задним числом.
V29_CANONICAL_SHA256 = "e0844bafb2b1ed3ce1fb2dec0da8dfbaa91d1b09db843014f89eef3e3d990ecc"
V29_FILE_SHA256 = "ea6e042a4c809dbc432805fa70fe3a566742a153352303da4ecae3162c909321"
V28_CANONICAL_SHA256 = "8db816eede41f44bde7c8cbb72791b27c1f4ad531f4034cbf9ebe964dc67e68b"
V28_FILE_SHA256 = "82ffe53a1a667270428027a42f29240c4d719a90ff932bfe3addc7c9c89da3d2"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v29_differs_from_v28_by_the_single_rate_limit_edit() -> None:
    """Единственная правка — `research_proxy.rate_limit_max` 20 -> 60; всё остальное идентично."""
    v28 = _load(V28_PAYLOAD)
    v29 = _load(V29_PAYLOAD)

    assert set(v29) == set(v28), "у payload'а появился новый раздел"
    assert {key for key in v29 if v29[key] != v28[key]} == {"research_proxy"}
    old_section, new_section = v28["research_proxy"], v29["research_proxy"]
    assert set(new_section) == set(old_section), "в секции research_proxy появился новый ключ"
    assert {key for key in new_section if new_section[key] != old_section[key]} == {"rate_limit_max"}
    assert old_section["rate_limit_max"] == 20
    assert new_section["rate_limit_max"] == 60
    # окно не менялось: 3600 с остаётся и верхней границей валидации (modes.py), и значением
    assert new_section["rate_limit_window_seconds"] == 3600 == old_section["rate_limit_window_seconds"]
    # секции, которые задача не имела права трогать, совпадают побайтово
    for section in ("session_limits", "prompts", "policy", "model", "planning"):
        assert json.dumps(v29[section], sort_keys=True) == json.dumps(v28[section], sort_keys=True)


def test_v29_text_diff_is_exactly_one_line() -> None:
    """Файл v29 — файл v28 с одной изменённой строкой: ни добавленных, ни удалённых строк."""
    old = V28_PAYLOAD.read_text(encoding="utf-8").splitlines()
    new = V29_PAYLOAD.read_text(encoding="utf-8").splitlines()
    assert len(old) == len(new), "в файле изменилось число строк"
    assert [(a, b) for a, b in zip(old, new, strict=True) if a != b] == [
        ('    "rate_limit_max": 20,', '    "rate_limit_max": 60,')
    ]


def test_pinned_hashes_of_v29_and_untouched_v28() -> None:
    """Канон и хеш файла config-v29 зафиксированы; config-v28 не переписан (откат v29 = v28)."""
    assert canonical_sha256(_load(V29_PAYLOAD)) == V29_CANONICAL_SHA256
    assert hashlib.sha256(V29_PAYLOAD.read_bytes()).hexdigest() == V29_FILE_SHA256
    assert canonical_sha256(_load(V28_PAYLOAD)) == V28_CANONICAL_SHA256
    assert hashlib.sha256(V28_PAYLOAD.read_bytes()).hexdigest() == V28_FILE_SHA256


def test_v29_research_proxy_section_passes_mode_policy() -> None:
    """Секция проходит `ModePolicy.from_section` и даёт ровно 60 запросов за окно 3600 с."""
    policy = ModePolicy.from_section(_load(V29_PAYLOAD)["research_proxy"])
    assert policy.rate_limit_max == 60
    assert policy.rate_limit_window_seconds == 3600


def test_mode_policy_default_limit_stays_twenty() -> None:
    """Дефолт движка НЕ поднят правкой payload'а: пустая секция по-прежнему 20 запросов."""
    assert ModePolicy.from_section({}).rate_limit_max == 20


def test_dev_stand_scripts_do_not_activate_config_v29() -> None:
    """Активация config-v29 — решение менеджера: скрипты dev-стенда на v29 не ссылаются,
    дефолт bootstrap'а и reset-db.sh остаётся прежним (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v29" not in text, f"{name} уже ссылается на config-v29"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
