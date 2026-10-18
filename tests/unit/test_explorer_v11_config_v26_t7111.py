"""Unit: explorer-v11 и config-v26 — гигиена шагов исследователя в промпте (T7.111 часть 3).

Решение пользователя от 2026-10-14: explorer-v11 = explorer-v10 байт в байт, кроме строки
`version:` и четырёх новых правил (14–17) после правила 13 — перед разделом «Примеры завершения»,
который остаётся неизменным побайтно:

(а) 14 — после удачного поиска следующим шагом читай найденное, повтор выполненного запроса —
    смена угла, а не перефраз (хостовый гейт части 1 отклоняет почти-повторы);
(б) 15 — хосты из контекстного раздела «Недавно недоступны» (часть 2б) не обходят запрет, но
    строить сессию вокруг них не надо;
(в) 16 — к статистическому ведомству или центральному банку идти сразу к выпуску/документу с
    числами, а не читать разделы сайта (страница без чисел не даёт окна улики);
(г) 17 — вопрос про независимые оценки: с середины бюджета вести вторую линию не-официальных
    акторов. Примеров с реальными данными в новом тексте нет (страж T7.97a).

config-v26 = config-v25 ровно с одной правкой: пин `prompts.explorer` → explorer-v11 (путь,
sha256, version; байты файла payload правка не меняет — замена равной длины). config-v25 и
прежние payload'ы не тронуты; дефолт bootstrap'а dev-стенда (config-v20) не изменён; активация
config-v26 — решение менеджера.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.domain.canonical import canonical_sha256
from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts
from tests.unit.test_prompt_example_no_real_data import (
    _actor_hits,
    _new_lines,
    _pair_hits,
    _real_value_year_pairs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPLORER_V10 = REPO_ROOT / "prompts" / "explorer" / "explorer-v10.md"
EXPLORER_V11 = REPO_ROOT / "prompts" / "explorer" / "explorer-v11.md"
V25_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v25-payload.json"
V26_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v26-payload.json"

#: explorer-v10 заморожен (пин config-v25): его байты не меняются (ADR-0019)
EXPLORER_V10_SHA256 = "2351e76cdd4988c0730edf066582b9850d5ef10af84171fd2eb1dbd67f074203"
#: explorer-v11 — новый файл (пин config-v26)
EXPLORER_V11_SHA256 = "4bebf12985699e50681d811be0f7c1d372f63159c4b8c5f56778a630422da654"

#: canonical payload'ов: v25 заморожен (эталон отчёта T7.108), v26 закреплён (эталон T7.111)
V25_CANONICAL_SHA256 = "b86bd7b09517db4581a4f544759dd46ccd5efb4705fe362378b7915e76ad7a9a"
V26_CANONICAL_SHA256 = "3d93df81d40012dc640689982983fe1c4018982b317833f94acce89a1db56d6f"
#: хеш файла config-v26 (с каноническим сравнением не путать: байты файла — свой формат)
V26_FILE_SHA256 = "029f3d86d5406996ab82d091aca7a23637939e9b7adfa35c901b54d9766b7c4b"

#: ровно эти две правки applied to v10 -> v11 (байт в байт, больше — никаких)
EDITS: list[tuple[str, str]] = [
    (
        "version: explorer-v10",
        "version: explorer-v11",
    ),
    (
        "(правило 10).\n\n\n## Примеры завершения\n",
        "(правило 10).\n\n"
        "14. Поиск — это навигация, а не знание (правило 6): полезного в результатах поиска ровно"
        " столько, сколько в адресах и заголовках — нужный текст лежит на странице. После удачного"
        " поиска следующим шагом читай подходящий найденный адрес, а не повторяй поиск. Не повторяй"
        " выполненный запрос: перефразировка тех же слов нового результата не даёт — меняй угол,"
        " называя другого актора, другой показатель или другой период (T7.111).\n\n"
        "15. В контексте шага может быть раздел «Недавно недоступны»: хосты, которые недавно"
        " многократно отказывали чтению. Не строй сессию вокруг таких хостов: ищи те же данные на"
        " других сайтах — в тематических каталогах, аналитических сервисах, в статьях со ссылкой на"
        " первоисточник. Список не является запретом — хост мог починиться, а отказ случаен, — но"
        " повторный заход к нему редко даёт новое, а каждый неудачный шаг стоит шаг.\n\n"
        "16. Когда идёшь за числами в статистическое ведомство или центральный банк, не читай"
        " подряд разделы сайта: главную страницу, указатель раздела, каталог. Иди сразу к конкретному"
        " выпуску или документу с числами: аналитический выпуск с таблицей, бюллетень, страница"
        " отдельных данных. Страница, в тексте которой нет чисел, для тебя не источник улики"
        " (правило 5): окно улики, вырезанное из неё, значения не принесёт — такой шаг потерян.\n\n"
        "17. Если вопрос просит независимые оценки или прогнозы (правило 13), не трать всю сессию"
        " только на официальные источники: с середины бюджета веди вторую линию — читай оценки"
        " не-официальных акторов (базы частных аналитических служб, отраслевые ассоциации,"
        " академические публикации), а потом возвращайся к официальным числам и сравнивай линии."
        " Это верно для любого официального источника и любой страны вопроса (правила 9 и 10).\n"
        "\n\n## Примеры завершения\n",
    ),
]


def _apply_edits(base: str) -> str:
    text = base
    for old, new in EDITS:
        assert text.count(old) == 1, f"якорь правки встречается не ровно один раз: {old[:60]!r}"
        text = text.replace(old, new)
    return text


def _rules_before_examples(text: str) -> list[str]:
    """Тело каждого нумерованного правила — от его строки до следующей строки `N.` или раздела
    «Примеры завершения» (в v10 оно тоже кончается этим разделом, так что сравнение честное)."""
    body = text.split("## Примеры завершения", 1)[0]
    starts = [(int(m.group(1)), m.start()) for m in re.finditer(r"(?m)^(\d+)\. ", body)]
    rules: list[str] = []
    for pos, (_number, start) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(body)
        rules.append(body[start:end].rstrip())
    return rules


# ─── байтовая правка v10 → v11 ────────────────────────────────────────────────


@pytest.mark.unit
def test_explorer_v11_is_exact_bytes_of_v10_with_the_two_edits() -> None:
    v10 = EXPLORER_V10.read_text(encoding="utf-8")
    assert hashlib.sha256(v10.encode()).hexdigest() == EXPLORER_V10_SHA256, "explorer-v10 переписан"
    v11 = EXPLORER_V11.read_text(encoding="utf-8")
    assert hashlib.sha256(v11.encode()).hexdigest() == EXPLORER_V11_SHA256
    assert v11 == _apply_edits(v10), "explorer-v11 отличается от v10 не ровно двумя закреплёнными правками"


@pytest.mark.unit
def test_explorer_v11_rules_1_to_13_are_bytes_of_v10_and_examples_are_untouched() -> None:
    v10 = EXPLORER_V10.read_text(encoding="utf-8")
    v11 = EXPLORER_V11.read_text(encoding="utf-8")
    old = _rules_before_examples(v10)
    new = _rules_before_examples(v11)
    assert len(old) == 13, old[:3]
    assert len(new) == 17, "explorer-v11 изменил число правил не на четыре (14–17 добавлены, 13 прежних целы)"
    assert new[:13] == old, "правила 1–13 explorer-v11 не байт в байт explorer-v10 (ADR-0019)"
    assert new[13:] == _rules_before_examples(EDITS[1][1]), "правила 14–17 отличаются от закреплённых"
    # раздел примеров завершения — неизменен побайтно (пример про Веллингтон закреплён стражем)
    assert v11.split("## Примеры завершения", 1)[1] == v10.split("## Примеры завершения", 1)[1], (
        "explorer-v11 изменил раздел примеров завершения"
    )
    assert "Веллингтон" in v11 and "Веллингтон" in v10


@pytest.mark.unit
def test_new_rules_name_the_four_decisions_without_real_examples() -> None:
    rules = _rules_before_examples(EXPLORER_V11.read_text(encoding="utf-8"))
    r14, r15, r16, r17 = rules[13], rules[14], rules[15], rules[16]
    # (а) после поиска — чтение найденного; повтор запроса назван потерей шага, смена угла — замена
    assert "следующим шагом читай" in r14 and "меняй угол" in r14 and "перефразировка" in r14
    # (б) раздел «Недавно недоступны» назван и не объявлен запретом
    assert "«Недавно недоступны»" in r15 and "Список не является запретом" in r15
    # (в) статистическое ведомство/центральный банк — выпуск или документ с числами, не разделы
    assert "статистическое ведомство или центральный банк" in r16 and "выпуску или документу" in r16
    assert "нет чисел" in r16 and "шаг потерян" in r16
    # (г) независимые оценки — вторая линия с середины бюджета
    assert "с середины бюджета" in r17 and "вторую линию" in r17 and "не-официальных акторов" in r17
    # новых fenced-блоков и выдуманных примеров с реальными данными в новом тексте нет
    new_text = "\n".join(_new_lines(EXPLORER_V11, EXPLORER_V10))
    assert "```" not in new_text
    assert not _actor_hits(new_text), sorted(_actor_hits(new_text))
    assert not _pair_hits(new_text, _real_value_year_pairs()), sorted(_pair_hits(new_text, _real_value_year_pairs()))


# ─── пины config-v26 и неизменность config-v25 ────────────────────────────────


@pytest.mark.unit
def test_config_v26_is_v25_with_only_the_explorer_pin() -> None:
    v25 = json.loads(V25_PAYLOAD.read_text(encoding="utf-8"))
    v26 = json.loads(V26_PAYLOAD.read_text(encoding="utf-8"))
    assert canonical_sha256(v25) == V25_CANONICAL_SHA256, "canonical config-v25 изменился"
    assert canonical_sha256(v26) == V26_CANONICAL_SHA256
    assert (
        hashlib.sha256(V26_PAYLOAD.read_bytes()).hexdigest() == V26_FILE_SHA256
    ), "байты файла config-v26-payload.json изменены не закреплённой правкой"
    assert v26["schema_version"] == v25["schema_version"]
    untouched = {key for key in v25 if key != "prompts"}
    for key in untouched:
        assert v26[key] == v25[key], f"config-v26 изменил раздел {key}"
    assert set(v26) == set(v25), "config-v26 изменил набор разделов"
    for role, pin in v25["prompts"].items():
        if role == "explorer":
            continue
        assert v26["prompts"][role] == pin, f"config-v26 изменил пин {role}"
    assert v26["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v11.md"
    assert v26["prompts"]["explorer"]["version"] == "explorer-v11"
    assert v26["prompts"]["explorer"]["sha256"] == EXPLORER_V11_SHA256
    # на диске пин совпадает с байтами файла (content pinning, ADR-0019)
    disk = EXPLORER_V11.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == v26["prompts"]["explorer"]["sha256"]


@pytest.mark.unit
def test_config_v25_and_previous_payloads_stay_frozen_on_disk() -> None:
    """config-v25 остаётся пином explorer-v10 побайтно; прежние payload'ы не переписаны
    (эталон canonical закреплён тестом T7.108)."""
    v25 = json.loads(V25_PAYLOAD.read_text(encoding="utf-8"))
    assert v25["prompts"]["explorer"]["version"] == "explorer-v10"
    assert v25["prompts"]["explorer"]["sha256"] == EXPLORER_V10_SHA256
    disk = EXPLORER_V10.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == EXPLORER_V10_SHA256
    v24 = json.loads((REPO_ROOT / "docs" / "eval" / "config-v24-payload.json").read_text(encoding="utf-8"))
    assert canonical_sha256(v24) == "9f0edb1436602ec0dcd90b3aba3da1c07677a47ee2ef08ee11c44a8912a44530"


@pytest.mark.unit
def test_pinned_prompts_of_config_v26_resolve_and_tamper_fails_closed() -> None:
    payload = json.loads(V26_PAYLOAD.read_text(encoding="utf-8"))
    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v11"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V11_SHA256
    assert "Недавно недоступны" in loaded[Role.EXPLORER].text
    tampered = json.loads(json.dumps(payload))
    tampered["prompts"]["explorer"] = dict(tampered["prompts"]["explorer"], sha256="0" * 64)
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered["prompts"], REPO_ROOT)


# ─── стенд: дефолт bootstrap'а не тронут, config-v26 нигде не активирован ────


@pytest.mark.unit
def test_dev_stand_scripts_do_not_activate_config_v26() -> None:
    """Активация config-v26 — решение менеджера: скрипты dev-стенда не ссылаются на v26,
    дефолт bootstrap'а и reset-db.sh — прежний (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v26" not in text, f"{name} уже ссылается на config-v26"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
