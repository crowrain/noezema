"""Unit: explorer-v8 — спорное число проверяется ДВУМЯ сторонами поиска (T7.76).

Замер подставки .92 (STATUS T7.76 §1): перепроверка годовой инфляции израсходовала 8 шагов из 10 на
официальную сторону — два захода на недоступный `rosstat.gov.ru`, повторное чтение уже прочитанного
`cbr.ru`, четыре адреса одной релизной орбиты — и до независимой оценки не дошла. После T7.75 хост
склеивает пересказы одного первоисточника (ADR-0029 B), поэтому такая работа больше не может получить
`E3`: она честно падает до `insufficient_independence`. Значит «Проверено» для спорной величины требует
второй половины поиска, и эта половина раньше в промпте отсутствовала: правило 9 объясняло, что такое
независимость, но не требовало отдельного запроса про независимую оценку.

explorer-v7 закоммичен и не переписывается: его байты закреплены хешем. v8 обязан сохранить всякое его
правило дословно (тест ниже сверяет правила 1–9) и добавить ровно два — правило 10 (двусторонность) и
правило 11 ( беречь шаги, потому что остаток бюджета теперь виден модели).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPLORER_V7 = REPO_ROOT / "prompts" / "explorer" / "explorer-v7.md"
EXPLORER_V8 = REPO_ROOT / "prompts" / "explorer" / "explorer-v8.md"
V15_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v15-payload.json"
V16_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v16-payload.json"

#: explorer-v7 заморожен (пин config-v15): его байты не меняются никогда
EXPLORER_V7_SHA256 = "41d3f7a22f5704f2b9302057fc944ab8cd069c8cfb3063fc5973df28f8ea40fa"
#: explorer-v8 — новый файл, его идентичность закреплена (ADR-0019 content pinning)
EXPLORER_V8_SHA256 = "eff45071d38dbc4d5eb87d4eccf26e48bbd9a2bd79e9f125e09e9a0eed33fb2e"

EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)

#: формулировки независимого запроса, которые обязан знать исследователь (постановка T7.76 (в))
INDEPENDENT_QUERY_EXAMPLES = (
    "независимая оценка инфляции 2025",
    "альтернативные оценки инфляции",
    "наблюдаемая инфляция",
    "inflation estimate independent Russia 2025",
)


def _v8() -> str:
    assert EXPLORER_V8.is_file(), "explorer-v8.md is missing"
    return EXPLORER_V8.read_text(encoding="utf-8")


def _numbered_rules(text: str) -> list[str]:
    """Правило — блок: строка «N. » и все её продолжения с отступа. Многострочное правило,
    проверенное по первой строке, проверялось бы не полностью."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if re.match(r"^\d+\. ", line):
            blocks.append([line])
        elif blocks and (line.startswith("   ") or line.startswith("\t")):
            blocks[-1].append(line)
    return ["\n".join(block).rstrip() for block in blocks]


def _rule(rules: list[str], number: int) -> str:
    """Тело правила одной строкой-смыслом (переносы не значимы)."""
    for rule in rules:
        if rule.startswith(f"{number}. "):
            return re.sub(r"\s+", " ", rule)
    raise AssertionError(f"правило {number} отсутствует в промпте")


# ─── идентичность и неизменность прежней версии ───────────────────────────────


@pytest.mark.unit
def test_explorer_v8_is_a_new_file_and_v7_stays_untouched() -> None:
    assert _v8().startswith("version: explorer-v8\n")
    assert hashlib.sha256(EXPLORER_V8.read_bytes()).hexdigest() == EXPLORER_V8_SHA256
    assert hashlib.sha256(EXPLORER_V7.read_bytes()).hexdigest() == EXPLORER_V7_SHA256


@pytest.mark.unit
def test_v8_keeps_every_rule_of_v7_and_adds_exactly_two() -> None:
    """v8 = v7 + правила 10 и 11. Правила 1–9 перенесены дословно; правка или снятие прежнего
    правила краснят этот тест (сравнение с байтами замороженного v7)."""
    v7_rules = _numbered_rules(EXPLORER_V7.read_text(encoding="utf-8"))
    v8_rules = _numbered_rules(_v8())
    assert len(v7_rules) == 9, v7_rules
    assert v8_rules[:9] == v7_rules, f"прежние правила изменены: {v8_rules[:9]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v8_rules]
    assert numbers == list(range(1, 12)), numbers


# ─── правило 10: двусторонний поиск ──────────────────────────────────────────


@pytest.mark.unit
def test_rule_10_requires_two_sides_of_the_search() -> None:
    rule = _rule(_numbered_rules(_v8()), 10)
    assert "ДВУМЯ сторонами поиска" in rule, rule
    # официальная сторона — ровно одна пара «поиск + чтение»
    assert "один `web.search` по первоисточнику" in rule, rule
    # независимая сторона — отдельный запрос, обязательный по формулировке
    assert "ОБЯЗАТЕЛЬНЫЙ отдельный `web.search`" in rule, rule
    for example in INDEPENDENT_QUERY_EXAMPLES:
        assert example in rule, f"нет примера независимого запроса: {example}"


@pytest.mark.unit
def test_rule_10_does_not_let_a_second_copy_count_as_the_second_side() -> None:
    """Повторный поиск того же релиза — не вторая сторона (на подставке оба поиска искали официальный
    релиз, и независимой стороны не получилось вовсе)."""
    rule = _rule(_numbered_rules(_v8()), 10)
    assert "«того же релиза ещё раз» второй стороной не считается" in rule, rule
    assert "тот же адрес второй раз смысла нет" in rule, rule


@pytest.mark.unit
def test_rule_10_requires_comparison_and_names_the_honest_absence() -> None:
    """Сравнение обязательно; отсутствие независимых оценок — названный результат, а не молчание
    (иначе `insufficient_independence` в карточке остаётся необъяснённым)."""
    rule = _rule(_numbered_rules(_v8()), 10)
    assert "назови обе величины и расхождение" in rule, rule
    assert "расхождений не найдено" in rule, rule
    assert "независимых оценок не найдено" in rule, rule


@pytest.mark.unit
def test_rule_10_divergence_is_shown_not_picked_silently() -> None:
    """Вторая половина правила 9 сохранена в v8: расхождение показывается, одно число молча не
    выбирается. Проверка идёт по байтам замороженного v7 (та же формулировка)."""
    v7 = re.sub(r"\s+", " ", _rule(_numbered_rules(EXPLORER_V7.read_text(encoding="utf-8")), 9))
    v8 = re.sub(r"\s+", " ", _rule(_numbered_rules(_v8()), 9))
    assert v8 == v7
    assert "никогда не выбирай одно число молча" in v8


# ─── правило 11: бюджет шагов стал виден модели ──────────────────────────────


@pytest.mark.unit
def test_rule_11_names_the_visible_step_budget_and_its_cost() -> None:
    rule = _rule(_numbered_rules(_v8()), 11)
    assert "«Бюджет шага»" in rule, rule
    assert "не трать шаги на повторное чтение уже прочитанного адреса" in rule, rule
    assert "отклонённое действие стоит столько же, сколько успешное" in rule, rule


@pytest.mark.unit
def test_prompt_names_the_two_new_context_sections() -> None:
    """Промпт обязан ссылаться на то, что реально есть в контексте шага (иначе правило ссылается в
    пустоту): раздел схем аргументов и раздел бюджета шага."""
    text = _v8()
    assert "Схемы аргументов выданных инструментов" in text
    assert "Бюджет шага" in text


# ─── протокол завершения не тронут (ADR-0022) ────────────────────────────────


@pytest.mark.unit
def test_completion_protocol_examples_are_unchanged_and_valid() -> None:
    blocks = EXAMPLE_JSON.findall(_v8())
    assert len(blocks) == 2, f"ожидались два JSON-примера, найдено {len(blocks)}"
    good, bad = (json.loads(b) for b in blocks)
    assert good["decision"] == {"kind": "complete", "reason": "goal_reached"}
    assert "Веллингтон" in good["public_rationale"]
    # неправильный пример остаётся неправильным: свободный текст вместо токена
    from packages.domain.schemas.decision import normalize_complete_reason

    assert normalize_complete_reason(bad["decision"]["reason"]) != "goal_reached"


# ─── пины config-v16 ──────────────────────────────────────────────────────────


@pytest.mark.unit
def test_v16_pins_explorer_v8_and_v15_still_pins_v7() -> None:
    v16 = json.loads(V16_PAYLOAD.read_text(encoding="utf-8"))
    v15 = json.loads(V15_PAYLOAD.read_text(encoding="utf-8"))
    assert v16["prompts"]["explorer"]["version"] == "explorer-v8"
    assert v16["prompts"]["explorer"]["sha256"] == EXPLORER_V8_SHA256
    assert v15["prompts"]["explorer"]["version"] == "explorer-v7"

    loaded = resolve_prompts(v16["prompts"], REPO_ROOT)
    from packages.llm_gateway.roles import parse_prompt_version

    assert parse_prompt_version(loaded[Role.EXPLORER].text) == "explorer-v8"
    # прежняя версия остаётся на диске без изменений
    old = REPO_ROOT / "prompts" / "explorer" / "explorer-v7.md"
    assert old.is_file() and hashlib.sha256(old.read_bytes()).hexdigest() == EXPLORER_V7_SHA256


@pytest.mark.unit
def test_tampered_v8_pin_is_rejected_fail_closed() -> None:
    v16 = json.loads(V16_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v16["prompts"])
    tampered["explorer"] = {**tampered["explorer"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
