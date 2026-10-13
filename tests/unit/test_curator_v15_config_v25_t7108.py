"""Unit: curator-v15 и config-v25 — гейт «значение на связи» в промпте (T7.108 часть в).

Хостовый гейт (часть а, ADR-0034 §10) снимает `supports`-связь к улике `source_assertion`,
если измеренного значения утверждения нет ни во фрагменте, ни во всём сохранённом тексте
страницы: связь уходит с названной причиной (`rejected_links`), а утверждение без единой
оставшейся связи снимается как операция с причиной `no linked evidence contains the claim
value`. Промпт обязан объяснять моделью эту границу заранее: привязывать только страницу,
где число названо, и не тянуть к claim'у список файлов или описание раздела, где значения нет.

Решение пользователя от 2026-10-13 («да, делай»): curator-v15 = curator-v14 байт в байт,
кроме строки `version:` и одного дополнения к правилу 9 (после его последней строки):
правило про UNUSED-evidence остаётся прежним, к нему добавлен абзац гейта «значение на
связи» с вымышленным примером той же формы (сервис „Гелиос“, Тармения, 7,8 %; страницы с
размерами файлов «52.22 Кб»/«12,2 Мб» значения не несут).

config-v25 = config-v24 ровно с одной правкой: пин `prompts.curator` → curator-v15
(путь, sha256, version). config-v24 и прежние payload'ы не тронуты; дефолт bootstrap'а
dev-стенда (config-v20) не изменён; активация config-v25 — решение менеджера.
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
    INHERITED_REAL_DATA,
    _actor_hits,
    _inherited_lines,
    _new_lines,
    _pair_hits,
    _real_value_year_pairs,
)

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V14 = REPO_ROOT / "prompts" / "curator" / "curator-v14.md"
CURATOR_V15 = REPO_ROOT / "prompts" / "curator" / "curator-v15.md"
V24_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v24-payload.json"
V25_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v25-payload.json"

#: curator-v14 заморожен (пин config-v24): его байты не меняются (ADR-0019)
CURATOR_V14_SHA256 = "c582d6fd1cad9eebf51d44241586e108e88721a34cec2f115649d996c88d6eeb"
#: curator-v15 — новый файл (пин config-v25)
CURATOR_V15_SHA256 = "d229c3235424e98ac423aa400575fd646c84a9cea8f605e0b8e7f0513464920b"

#: canonical payload'ов: v24 заморожен (эталон отчёта T7.106), v25 закреплён (эталон T7.108)
V24_CANONICAL_SHA256 = "9f0edb1436602ec0dcd90b3aba3da1c07677a47ee2ef08ee11c44a8912a44530"
V25_CANONICAL_SHA256 = "b86bd7b09517db4581a4f544759dd46ccd5efb4705fe362378b7915e76ad7a9a"
#: хеш файла config-v25 (с каноническим сравнением не путать: байты файла — свой формат)
V25_FILE_SHA256 = "e3aa508e3ba2b45729419a3304c4d9c71827c2588ccb448cc8cd46612852e932"

#: ровно эти две правки applied to v14 -> v15 (байт в байт, больше — никаких)
EDITS: list[tuple[str, str]] = [
    (
        "version: curator-v14",
        "version: curator-v15",
    ),
    (
        "   же, что в правилу 1: неиспользованное evidence к claim'у не тяни.\n",
        "   же, что в правилу 1: неиспользованное evidence к claim'у не тяни.\n"
        "   Хост проверяет связь по значению (гейт «значение на связи»): связь `supports` к улике\n"
        "   `source_assertion` остаётся только если измеренное значение утверждения — целое число\n"
        "   ровно как названо — встречается либо в фрагменте этой улики, либо во всём сохранённом\n"
        "   тексте страницы. Если числа там нет вовсе (размеры файлов, таблица с другими величинами,\n"
        "   описание раздела), связь снимается с названной причиной (`rejected_links`) ещё до проверки\n"
        "   правил: утверждение больше не считает этот источник независимым. Если у утверждения не\n"
        "   остаётся ни одной связи со значением, оно снимается целиком с причиной\n"
        "   `no linked evidence contains the claim value`: привязывай только ту страницу, где число\n"
        "   названо, а такой страницы нет — отказывайся от утверждения. (Вымышленный случай: claim\n"
        "   «Сервис „Гелиос“ (Тармения): доля вакансий с закрытыми откликами выросла до 7,8%» —\n"
        "   связи к списку файлов («52.22 Кб», «12,2 Мб») и к описанию раздела числа 7,8 не несут,\n"
        "   значит они не доказательство этого claim'а.) Связь опровержения (`counters`) значения не\n"
        "   требует: опровержение живёт и без него. Утверждение, у которого хост не измерил значение,\n"
        "   этим правилом не проверяется вовсе.\n",
    ),
]


def _apply_edits(base: str) -> str:
    text = base
    for old, new in EDITS:
        assert text.count(old) == 1, f"якорь правки встречается не ровно один раз: {old[:60]!r}"
        text = text.replace(old, new)
    return text


def _numbered_rules(text: str) -> list[str]:
    """Тело каждого нумерованного правила — от его строки до следующей строки `N.` или EOF."""
    starts = [
        (int(m.group(1)), m.start())
        for m in re.finditer(r"(?m)^(\d+)\. ", text)
    ]
    rules: list[str] = []
    for pos, (_number, start) in enumerate(starts):
        end = starts[pos + 1][1] if pos + 1 < len(starts) else len(text)
        rules.append(text[start:end].rstrip())
    return rules


# ─── байтовая правка v14 → v15 ────────────────────────────────────────────────


@pytest.mark.unit
def test_curator_v15_is_exact_bytes_of_v14_with_the_two_edits() -> None:
    v14 = CURATOR_V14.read_text(encoding="utf-8")
    assert hashlib.sha256(v14.encode()).hexdigest() == CURATOR_V14_SHA256, "curator-v14 переписан"
    v15 = CURATOR_V15.read_text(encoding="utf-8")
    assert hashlib.sha256(v15.encode()).hexdigest() == CURATOR_V15_SHA256
    assert v15 == _apply_edits(v14), "curator-v15 отличается от v14 не ровно двумя закреплёнными правками"


@pytest.mark.unit
def test_curator_v15_rules_1_to_8_and_10_to_14_are_bytes_of_v14_rule_9_only_grows() -> None:
    old = _numbered_rules(CURATOR_V14.read_text(encoding="utf-8"))
    new = _numbered_rules(CURATOR_V15.read_text(encoding="utf-8"))
    assert len(old) == 14, old[:3]
    assert len(new) == 14, "curator-v15 изменил число правил (дополнение живёт внутри правила 9)"
    assert new[:8] == old[:8], "правила 1–8 curator-v15 не байт в байт curator-v14 (ADR-0019)"
    assert new[9:] == old[9:], "правила 10–14 curator-v15 не байт в байт curator-v14 (ADR-0019)"
    # правило 9 = прежнее правило 9 + закреплённое дополнение; прежний текст — префикс
    assert new[8].startswith(old[8]), "старый текст правила 9 переписан"
    assert new[8][len(old[8]) :].strip() == EDITS[1][1][len(EDITS[1][0]) :].strip(), (
        "дополнение правила 9 отличается от закреплённого"
    )


@pytest.mark.unit
def test_rule_9_addition_states_the_gate_and_the_named_reasons() -> None:
    rule = _numbered_rules(CURATOR_V15.read_text(encoding="utf-8"))[8]
    assert "гейт «значение на связи»" in rule
    assert "`rejected_links`" in rule and "no linked evidence contains the claim value" in rule
    # границы гейта названы честно: полная страница тоже носит значение; counters и
    # утверждение без измеренного значения — вне проверки
    assert "во всём сохранённом" in rule and "фрагменте" in rule
    assert "Связь опровержения (`counters`) значения не" in rule
    assert "не измерил значение" in rule
    # вымышленный пример с % и без реальных акторов
    examples = [example for example in re.findall(r"«([^»]+)»", rule) if "%" in example]
    assert examples and "Гелиос" in examples[0] and "вымышлен" in rule.lower()
    assert not _actor_hits(rule)


# ─── отсутствие реальных данных в новом тексте (страж T7.97a/T7.102, без исключений) ──


@pytest.mark.unit
def test_curator_v15_new_text_carries_no_real_case_data() -> None:
    """Прямое закрепление того, что общий страж `test_prompt_example_no_real_data.py`
    применяет к v15 без исключений: новый текст не называет акторов витрины, не
    воспроизводит связку «значение + период» и не противоречит примерам корпуса."""
    new_text = "\n".join(_new_lines(CURATOR_V15, CURATOR_V14))
    assert new_text, "правого сравнения v15↔v14 не получилось"
    hits = _actor_hits(new_text)
    assert not hits, f"новый текст curator-v15 называет реальных акторов {sorted(hits)}"
    pairs = _pair_hits(new_text, _real_value_year_pairs())
    assert not pairs, f"новый текст curator-v15 воспроизводит «значение + период» реального случая {sorted(pairs)}"
    for corpus in sorted((REPO_ROOT / "docs" / "eval").glob("question-set-*.jsonl")):
        content = corpus.read_text(encoding="utf-8")
        for block in re.findall(r"```json\n(.*?)```", CURATOR_V15.read_text(encoding="utf-8"), re.S):
            example = json.loads(block)
            for claim in example.get("claims", []):
                assert claim["statement"] not in content, (
                    f"пример v15 {claim['statement']!r} встречается в корпусе {corpus.name}"
                )


@pytest.mark.unit
def test_curator_v15_inherits_no_real_case_data() -> None:
    """Унаследованный текст v15 (= весь корпус v14) обязан быть чистым: запись
    `INHERITED_REAL_DATA` для нынешнего curator-промпта пустая, и скан унаследованных
    строк ничего не находит (T7.102)."""
    assert INHERITED_REAL_DATA.get(CURATOR_V15.name, set()) == set()
    found: set[tuple[str, str]] = set()
    for line in _inherited_lines(CURATOR_V15, CURATOR_V14):
        found |= {("актор", name) for name, _snippet in _actor_hits(line)}
        found |= {("пара", f"{value}+{year}") for value, year in _pair_hits(line, _real_value_year_pairs())}
    assert not found, f"унаследованные реальные данные в curator-v15: {sorted(found)}"


# ─── пины config-v25 и неизменность config-v24 ───────────────────────────────


@pytest.mark.unit
def test_config_v25_is_v24_with_only_the_curator_pin() -> None:
    v24 = json.loads(V24_PAYLOAD.read_text(encoding="utf-8"))
    v25 = json.loads(V25_PAYLOAD.read_text(encoding="utf-8"))
    assert canonical_sha256(v24) == V24_CANONICAL_SHA256, "canonical config-v24 изменился"
    assert canonical_sha256(v25) == V25_CANONICAL_SHA256
    assert (
        hashlib.sha256(V25_PAYLOAD.read_bytes()).hexdigest() == V25_FILE_SHA256
    ), "байты файла config-v25-payload.json изменены не закреплённой правкой"
    assert v25["schema_version"] == v24["schema_version"]
    untouched = {key for key in v24 if key != "prompts"}
    for key in untouched:
        assert v25[key] == v24[key], f"config-v25 изменил раздел {key}"
    assert set(v25) == set(v24), "config-v25 изменил набор разделов"
    for role, pin in v24["prompts"].items():
        if role == "curator":
            continue
        assert v25["prompts"][role] == pin, f"config-v25 изменил пин {role}"
    assert v25["prompts"]["curator"]["path"] == "prompts/curator/curator-v15.md"
    assert v25["prompts"]["curator"]["version"] == "curator-v15"
    assert v25["prompts"]["curator"]["sha256"] == CURATOR_V15_SHA256
    # на диске пин совпадает с байтами файла (content pinning, ADR-0019)
    disk = CURATOR_V15.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == v25["prompts"]["curator"]["sha256"]


@pytest.mark.unit
def test_config_v24_and_previous_payloads_stay_frozen_on_disk() -> None:
    """config-v24 остаётся пином curator-v14 побайтно; прежние payload'ы не переписаны
    (эталон canonical закреплён тестом T7.106)."""
    v24 = json.loads(V24_PAYLOAD.read_text(encoding="utf-8"))
    assert v24["prompts"]["curator"]["version"] == "curator-v14"
    assert v24["prompts"]["curator"]["sha256"] == CURATOR_V14_SHA256
    disk = CURATOR_V14.read_bytes()
    assert hashlib.sha256(disk).hexdigest() == CURATOR_V14_SHA256
    v23 = json.loads((REPO_ROOT / "docs" / "eval" / "config-v23-payload.json").read_text(encoding="utf-8"))
    assert canonical_sha256(v23) == "4cc1c296e6ee5cb0036d909b05a67a2a30406fdff8cb7f8841d48164ec64778f"


@pytest.mark.unit
def test_pinned_prompts_of_config_v25_resolve_and_tamper_fails_closed() -> None:
    payload = json.loads(V25_PAYLOAD.read_text(encoding="utf-8"))
    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.CURATOR].version == "curator-v15"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V15_SHA256
    assert "гейт «значение на связи»" in loaded[Role.CURATOR].text
    tampered = json.loads(json.dumps(payload))
    tampered["prompts"]["curator"] = dict(tampered["prompts"]["curator"], sha256="0" * 64)
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered["prompts"], REPO_ROOT)


# ─── стенд: дефолт bootstrap'а не тронут, config-v25 нигде не активирован ────


@pytest.mark.unit
def test_dev_stand_scripts_do_not_activate_config_v25() -> None:
    """Активация config-v25 — решение менеджера: скрипты dev-стенда не ссылаются на v25,
    дефолт bootstrap'а и reset-db.sh — прежний (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v25" not in text, f"{name} уже ссылается на config-v25"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
