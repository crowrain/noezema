"""T7.133 (часть 4): config-v30 = config-v29 ровно с одной правкой лимита ответа.

Payload только ГОТОВИТСЯ: активация config-v30 — решение менеджера по решению
пользователя, задача её не делает (ни в скриптах dev-стенда, ни через
`activate-online`), стойку не трогает. Прежные payload'ы не переписаны задним
числом, дефолт `SSRFPolicy.from_section` остаётся 1 МиБ: меняется только
значение в файле config-v30.

Обоснование 16 МиБ (замер фикстур менеджера fetch-rejected-since-1016.json):
единственный переразмер за 16–22.10 — PDF Росстата (`Rab_sila_2026.pdf`, 9
отказов, и `osn-08-2026.pdf`): ответ отбраковывался на 4 МиБ ДО сохранения,
т.е. реальный размер > 4 194 304. Крупнейший сохранённый PDF стенда —
osn-06-2026.pdf, 3 417 566 байт (81 % прежнего лимита): годовые выпуски Росстата
перерастают 4 МиБ вдвое-втрое. 16 МиБ — четырёхкратный запас к старому лимиту и
запас ~4,7 к крупнейшему наблюдаемому документу; больших выпусков в журнале нет,
поэтому это запас («круглое следующее doubling»), а не вывод из правила.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from apps.research_proxy.ssrf_guard import SSRFPolicy
from packages.domain.canonical import canonical_sha256

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
V30_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v30-payload.json"
V29_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v29-payload.json"
V28_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v28-payload.json"

# Канонический хеш (JCS) и хеш файла — РАЗНЫЕ числа (AGENTS §7 про цепочку config);
# пинуются оба: канон отвечает за содержимое, хеш файла — за то, что файл не правил
# задним числом. Прежние payload'ы закреплены как откаты (не переписаны).
V30_CANONICAL_SHA256 = "46648979d6f4408ffbe888978528bb27a473eeccbdcd1139b55ebc9dd01e2ab5"
V30_FILE_SHA256 = "cddfbafcebf23001c5a8c6fc40f8c669749502a8b31f390ba4e746e739c47bc2"
V29_CANONICAL_SHA256 = "e0844bafb2b1ed3ce1fb2dec0da8dfbaa91d1b09db843014f89eef3e3d990ecc"
V29_FILE_SHA256 = "ea6e042a4c809dbc432805fa70fe3a566742a153352303da4ecae3162c909321"
V28_CANONICAL_SHA256 = "8db816eede41f44bde7c8cbb72791b27c1f4ad531f4034cbf9ebe964dc67e68b"
V28_FILE_SHA256 = "82ffe53a1a667270428027a42f29240c4d719a90ff932bfe3addc7c9c89da3d2"


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def test_v30_differs_from_v29_by_the_single_response_limit_edit() -> None:
    """Единственная правка — `research_proxy.max_response_bytes` 4194304 -> 16777216."""
    v29 = _load(V29_PAYLOAD)
    v30 = _load(V30_PAYLOAD)

    assert set(v30) == set(v29), "у payload'а появился новый раздел"
    assert {key for key in v30 if v30[key] != v29[key]} == {"research_proxy"}
    old_section, new_section = v29["research_proxy"], v30["research_proxy"]
    assert set(new_section) == set(old_section), "в секции research_proxy появился новый ключ"
    assert {key for key in new_section if new_section[key] != old_section[key]} == {
        "max_response_bytes"
    }
    assert old_section["max_response_bytes"] == 4_194_304
    assert new_section["max_response_bytes"] == 16_777_216
    # лимит поиска (правка v29) и окно не менялись
    assert new_section["rate_limit_max"] == 60
    assert new_section["rate_limit_window_seconds"] == 3600
    # секции, которые задача не имела права трогать, совпадают побайтово
    for section in ("session_limits", "prompts", "policy", "model", "planning"):
        assert json.dumps(v30[section], sort_keys=True) == json.dumps(v29[section], sort_keys=True)


def test_v30_text_diff_is_exactly_one_line() -> None:
    """Файл v30 — файл v29 с одной изменённой строкой: ни добавленных, ни удалённых строк."""
    old = V29_PAYLOAD.read_text(encoding="utf-8").splitlines()
    new = V30_PAYLOAD.read_text(encoding="utf-8").splitlines()
    assert len(old) == len(new), "в файле изменилось число строк"
    assert [(a, b) for a, b in zip(old, new, strict=True) if a != b] == [
        ('    "max_response_bytes": 4194304,', '    "max_response_bytes": 16777216,')
    ]


def test_pinned_hashes_of_v30_and_untouched_rollback_chain() -> None:
    """Канон и хеш файла config-v30 зафиксированы; v29 и v28 не переписаны (откат v30 = v29)."""
    assert canonical_sha256(_load(V30_PAYLOAD)) == V30_CANONICAL_SHA256
    assert hashlib.sha256(V30_PAYLOAD.read_bytes()).hexdigest() == V30_FILE_SHA256
    assert canonical_sha256(_load(V29_PAYLOAD)) == V29_CANONICAL_SHA256
    assert hashlib.sha256(V29_PAYLOAD.read_bytes()).hexdigest() == V29_FILE_SHA256
    assert canonical_sha256(_load(V28_PAYLOAD)) == V28_CANONICAL_SHA256
    assert hashlib.sha256(V28_PAYLOAD.read_bytes()).hexdigest() == V28_FILE_SHA256


def test_v30_research_proxy_section_passes_ssrf_policy() -> None:
    """Секция проходит `SSRFPolicy.from_section`: 16 МиБ на ответ, прочее прежнее."""
    policy = SSRFPolicy.from_section(_load(V30_PAYLOAD)["research_proxy"])
    assert policy.max_response_bytes == 16_777_216


def test_ssrf_policy_default_limit_stays_one_mebibyte() -> None:
    """Дефолт движка НЕ поднят правкой payload'а: пустая секция по-прежнему 1 МиБ."""
    assert SSRFPolicy.from_section({}).max_response_bytes == 1_048_576


def test_dev_stand_scripts_do_not_activate_config_v30() -> None:
    """Активация config-v30 — решение менеджера: скрипты dev-стенда на v30 не ссылаются,
    дефолт bootstrap'а и reset-db.sh остаётся прежним (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v30" not in text, f"{name} уже ссылается на config-v30"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
