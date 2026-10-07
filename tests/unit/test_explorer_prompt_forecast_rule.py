"""Unit: explorer-v9 — прогноз до события не выдаётся за независимую оценку (T7.77).

Замер стенда (STATUS T7.77): утверждение об итоговой инфляции 2025 получало вторую «независимую»
группу от публикации, пересказывающей официальную цифру со ссылкой на Росстат. Детектор производности
(T7.75 → v2 в этой же задаче) такие склейки закрывает на стороне хоста; правило 10 (T7.76) требует
вторую сторону поиска — но модель может выполнить его буквально и принять в качестве «независимой
оценки» ПРОГНОЗ (банк/СМИ ожидает X), который про состоявшееся событие ничего не измеряет. Это
промпт-уровень: хост не добавляет гейтов, правил и движений движка — правило 12 формулирует различие
«ожидания vs уже произошедшее» так, чтобы explorer честно назвал вторую сторону или её отсутствие.

explorer-v8 закоммичен и не переписывается (пин config-v16). v9 обязан сохранить правила 1–11
дословно (сравнение с байтами замороженного v8) и добавить ровно правило 12; всё остальное — включая
протокол завершения — побайтно совпадает с v8.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPLORER_V8 = REPO_ROOT / "prompts" / "explorer" / "explorer-v8.md"
EXPLORER_V9 = REPO_ROOT / "prompts" / "explorer" / "explorer-v9.md"
V16_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v16-payload.json"
V17_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v17-payload.json"

#: explorer-v8 заморожен (пин config-v16): его байты не меняются никогда
EXPLORER_V8_SHA256 = "eff45071d38dbc4d5eb87d4eccf26e48bbd9a2bd79e9f125e09e9a0eed33fb2e"
#: explorer-v9 — новый файл (ADR-0019 content pinning), его идентичность закреплена
EXPLORER_V9_SHA256 = "bf5169a06656ffce86bc3e9ea0d0c264824c11b2162a39a68b69321ce9d766af"

_RULES_SECTION = re.compile(r"## Правила\n(.*?)\n## Примеры завершения", re.S)


def _v9() -> str:
    assert EXPLORER_V9.is_file(), "explorer-v9.md is missing"
    return EXPLORER_V9.read_text(encoding="utf-8")


def _numbered_rules(text: str) -> list[str]:
    """Правило — блок: строка «N. » и все её продолжения с отступом (тот же разбор, что в тесте v8)."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if re.match(r"^\d+\. ", line):
            blocks.append([line])
        elif blocks and (line.startswith("   ") or line.startswith("\t")):
            blocks[-1].append(line)
    return ["\n".join(block).rstrip() for block in blocks]


def _rule(rules: list[str], number: int) -> str:
    for rule in rules:
        if rule.startswith(f"{number}. "):
            return re.sub(r"\s+", " ", rule)
    raise AssertionError(f"правило {number} отсутствует в промпте")


# ─── неизменность прежней версии и идентичность правил 1–11 ─────────────────


@pytest.mark.unit
def test_explorer_v9_is_a_new_file_and_v8_stays_untouched() -> None:
    assert _v9().startswith("version: explorer-v9\n")
    assert hashlib.sha256(EXPLORER_V9.read_bytes()).hexdigest() == EXPLORER_V9_SHA256
    assert hashlib.sha256(EXPLORER_V8.read_bytes()).hexdigest() == EXPLORER_V8_SHA256


@pytest.mark.unit
def test_v9_keeps_every_rule_of_v8_and_adds_exactly_one() -> None:
    """v9 = v8 + правило 12. Правила 1–11 перенесены дословно — блок правил сверяется с байтами
    замороженного v8; правка или снятие прежнего правила краснит тест."""
    v8_rules = _numbered_rules(EXPLORER_V8.read_text(encoding="utf-8"))
    v9_rules = _numbered_rules(_v9())
    assert len(v8_rules) == 11, v8_rules
    assert v9_rules[:11] == v8_rules, f"прежние правила изменены: {v9_rules[:11]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v9_rules]
    assert numbers == list(range(1, 13)), numbers


@pytest.mark.unit
def test_everything_outside_the_numbered_rules_is_byte_identical_to_v8() -> None:
    """Протокол завершения, примеры JSON и разделы «Explorer»/«Контекст» не тронуты: разница v9 —
    только версия в первой строке и добавленное правило 12 внутри раздела «Правила»."""
    v8 = EXPLORER_V8.read_text(encoding="utf-8")
    v9 = _v9()

    def cut(text: str, *, rename_version: bool) -> tuple[str, str]:
        m = _RULES_SECTION.search(text)
        assert m is not None
        head = text[: m.start(1)]
        if rename_version:
            head = head.replace("explorer-v8", "explorer-v9")
        return head, text[m.end(1) :]

    # ожидаемое совпадение: head v9 = head v8 ровно в части токена версии
    v8_head, v8_tail = cut(v8, rename_version=True)
    v9_head, v9_tail = cut(v9, rename_version=False)
    assert v9_head == v8_head  # только версия в строке 1 отличается (она заменена выше)
    assert v9_tail == v8_tail

    v8_rules_block = _RULES_SECTION.search(v8).group(1)
    v9_rules_block = _RULES_SECTION.search(v9).group(1)
    assert v9_rules_block.startswith(v8_rules_block), "правила 1–11 изменены внутри раздела"
    assert v9_rules_block[len(v8_rules_block) :].lstrip("\n").startswith("12. ")


# ─── правило 12: ожидания ≠ измерение состоявшегося ─────────────────────────


@pytest.mark.unit
def test_rule_12_names_the_forecast_boundary() -> None:
    rule = _rule(_numbered_rules(_v9()), 12)
    assert "Прогноз до события — не независимая оценка реализованного значения" in rule, rule
    assert "сопоставимо оно только с тем, что ожидалось" in rule, rule
    assert "никогда — как независимое подтверждение уже состоявшейся величины" in rule, rule


@pytest.mark.unit
def test_rule_12_names_what_a_real_independent_measurement_is() -> None:
    """Правило обязано не только запретить, но и показать вторую сторону измерением, иначе модель
    упрётся в молчание там, где честная независимая оценка существует."""
    rule = _rule(_numbered_rules(_v9()), 12)
    assert "измерение того, что уже произошло" in rule, rule
    for example in ("наблюдаемая и воспринимаемая инфляция по опросам домохозяйств", "альтернативные индексы цен"):
        assert example in rule, f"нет примера независимого измерения: {example}"


@pytest.mark.unit
def test_rule_12_ties_retold_official_numbers_back_to_rules_9_and_10() -> None:
    """Связка с прежними правилами: пересказ официальной цифры со ссылкой на первоисточник — не
    вторая независимая сторона (ловушка стенда, закрытая на хосте детектором v2)."""
    rule = _rule(_numbered_rules(_v9()), 12)
    assert "пересказывающая официальную цифру со ссылкой на первоисточник" in rule, rule
    assert "правила 9 и 10" in rule, rule


# ─── пины config-v17 и неизменность v16 ─────────────────────────────────────


@pytest.mark.unit
def test_v17_pins_explorer_v9_and_v16_still_pins_v8() -> None:
    v17 = json.loads(V17_PAYLOAD.read_text(encoding="utf-8"))
    v16 = json.loads(V16_PAYLOAD.read_text(encoding="utf-8"))
    assert v17["prompts"]["explorer"]["version"] == "explorer-v9"
    assert v17["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v9.md"
    assert v17["prompts"]["explorer"]["sha256"] == EXPLORER_V9_SHA256
    assert v16["prompts"]["explorer"]["version"] == "explorer-v8"

    loaded = resolve_prompts(v17["prompts"], REPO_ROOT)
    from packages.llm_gateway.roles import parse_prompt_version

    assert parse_prompt_version(loaded[Role.EXPLORER].text) == "explorer-v9"
    # промпт из снимка обязан содержать новое правило
    assert "Прогноз до события" in loaded[Role.EXPLORER].text
    # прежняя версия остаётся на диске без изменений
    assert hashlib.sha256(EXPLORER_V8.read_bytes()).hexdigest() == EXPLORER_V8_SHA256


@pytest.mark.unit
def test_tampered_v9_pin_is_rejected_fail_closed() -> None:
    v17 = json.loads(V17_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v17["prompts"])
    tampered["explorer"] = {**tampered["explorer"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
