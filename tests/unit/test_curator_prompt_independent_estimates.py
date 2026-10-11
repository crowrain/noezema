"""Unit: curator-v11 — собственная оценка актора становится отдельным claim'ом (T7.97).

Случай стенда .92 (STATUS T7.97, ADR-0035 §15): сессия bdd9ef8d прочитала страницу
SberCIB с собственным прогнозом аналитиков на 2026 год, но ни один claim его не
записал: куратор собрал только цифры официальных публикаций ЦБ (прогноз ЦБ, медиана
макроопроса ЦБ, опрос инФОМ по заказу ЦБ). Медиана чужого опроса, опубликованная
ведомством, была подана как «независимые аналитики» — это публикация ведомства, а не
независимое подтверждение его же чисел; собственная же оценка актора, дошедшая до
текста улики, отдельным утверждением не стала. curator-v11 формулирует правило 11:
прочитанная собственная оценка не-официального актора — отдельный claim с названием
актора; публикация ведомства остаётся публикацией ведомства с названными авторами;
пересказ релиза/консенсуса на чужом сайте не записывается как оценка автора сайта
(тот же показатель/значение/период — перепроверка по правилу 7). Гейтов хост не
добавляет: гейт дублей по значению (T7.83(а)) и правило 5 (нет утверждения без
текста улики) остаются как были.

curator-v10 закоммичен и не переписывается (пин config-v20). v11 обязан сохранить
правила 1–10 дословно (сравнение с байтами замороженного v10) и добавить ровно
правило 11; всё остальное — побайтно совпадает с v10.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V10 = REPO_ROOT / "prompts" / "curator" / "curator-v10.md"
CURATOR_V11 = REPO_ROOT / "prompts" / "curator" / "curator-v11.md"
V20_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v20-payload.json"
V21_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v21-payload.json"

#: curator-v10 заморожен (пин config-v20): его байты не меняются никогда
CURATOR_V10_SHA256 = "8b940bf573bcd19a1302b0a6ed48fee00ec87dbc81f71fa56063f510113c040e"
#: curator-v11 — новый файл (ADR-0019 content pinning), его идентичность закреплена
CURATOR_V11_SHA256 = "3702b7f5cb4b007ca186401251d3a6c453ac509b41a13118aa045c3a8bb71c07"

#: раздел «Правила» куратора занимает весь остаток файла после «## Правила»
_RULES_SECTION = re.compile(r"## Правила\n(.*)\Z", re.S)


def _v11() -> str:
    assert CURATOR_V11.is_file(), "curator-v11.md is missing"
    return CURATOR_V11.read_text(encoding="utf-8")


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


# ─── неизменность прежней версии и идентичность правил 1–10 ─────────────────


@pytest.mark.unit
def test_curator_v11_is_a_new_file_and_v10_stays_untouched() -> None:
    assert _v11().startswith("version: curator-v11\n")
    assert hashlib.sha256(CURATOR_V11.read_bytes()).hexdigest() == CURATOR_V11_SHA256
    assert hashlib.sha256(CURATOR_V10.read_bytes()).hexdigest() == CURATOR_V10_SHA256


@pytest.mark.unit
def test_v11_keeps_every_rule_of_v10_and_adds_exactly_one() -> None:
    """v11 = v10 + правило 11. Правила 1–10 перенесены дословно — блок правил сверяется с
    байтами замороженного v10; правка или снятие прежнего правила краснит тест."""
    v10_rules = _numbered_rules(CURATOR_V10.read_text(encoding="utf-8"))
    v11_rules = _numbered_rules(_v11())
    assert len(v10_rules) == 10, v10_rules
    assert v11_rules[:10] == v10_rules, f"прежние правила изменены: {v11_rules[:10]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v11_rules]
    assert numbers == list(range(1, 12)), numbers


@pytest.mark.unit
def test_everything_outside_the_numbered_rules_is_byte_identical_to_v10() -> None:
    """Протокол ответа, таблицы и примеры не тронуты: разница v11 — только версия в первой
    строке и добавленное правило 11 в конце раздела «Правила»."""
    v10 = CURATOR_V10.read_text(encoding="utf-8")
    v11 = _v11()

    def cut(text: str, *, rename_version: bool) -> str:
        m = _RULES_SECTION.search(text)
        assert m is not None
        head = text[: m.start(1)]
        if rename_version:
            head = head.replace("curator-v10", "curator-v11")
        return head

    assert cut(v11, rename_version=False) == cut(v10, rename_version=True)

    v10_rules_block = _RULES_SECTION.search(v10).group(1)
    v11_rules_block = _RULES_SECTION.search(v11).group(1)
    assert v11_rules_block.startswith(v10_rules_block), "правила 1–10 изменены внутри раздела"
    assert v11_rules_block[len(v10_rules_block) :].lstrip("\n").startswith("11. ")


# ─── правило 11: кто назван автором оценки, а кто — публикатором ────────────


@pytest.mark.unit
def test_rule_11_makes_the_actor_own_estimate_a_separate_claim() -> None:
    rule = _rule(_numbered_rules(_v11()), 11)
    assert "собственная оценка не-официального актора, прочитанная в тексте улики" in rule, rule
    assert "записывается отдельным claim'ом" in rule, rule
    assert "с названием актора в формулировке" in rule, rule
    assert "актор, значение, период и источник" in rule, rule
    assert "связывается evidence, где оценка прочитана" in rule, rule
    # пример формулировки ровно тот, что терялся на стенде (SberCIB, 2026, 6,5%)
    assert "Аналитики SberCIB: прогноз инфляции в России на 2026 год — 6,5%" in rule, rule


@pytest.mark.unit
def test_rule_11_keeps_official_survey_published_by_the_agency() -> None:
    """Медиана макроопроса ЦБ — запись публикации ведомства с авторами и публикатором,
    а не отдельная независимая оценка ведомства (дефект карточки 5a68172e)."""
    rule = _rule(_numbered_rules(_v11()), 11)
    assert "Оценка, собранная и опубликованная официальным ведомством" in rule, rule
    assert "медиана макроэкономического опроса аналитиков Банка России" in rule, rule
    assert "называй в формулировке и авторов, и публикатора" in rule, rule
    assert "это не отдельная независимая оценка от ведомства" in rule, rule


@pytest.mark.unit
def test_rule_11_does_not_promote_retellings_and_does_not_invent() -> None:
    """Пересказ релиза/консенсуса на чужом сайте — не собственная оценка автора сайта:
    тот же показатель/значение/период — перепроверка по правилу 7. Нет оценки в уликах —
    нет claim'а: честное отсутствие названо итогом (правило 5)."""
    rule = _rule(_numbered_rules(_v11()), 11)
    assert "не записывай как собственную оценку автора сайта" in rule, rule
    assert "перепроверка по правилу 7, а не новый claim" in rule, rule
    assert "не выдумывай их" in rule, rule
    assert "честное отсутствие уже названо итогом исследования (правило 5)" in rule, rule


# ─── пины config-v21 и неизменность config-v20 ──────────────────────────────


@pytest.mark.unit
def test_v21_pins_curator_v11_and_v20_still_pins_v10() -> None:
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    v20 = json.loads(V20_PAYLOAD.read_text(encoding="utf-8"))
    assert v21["prompts"]["curator"]["version"] == "curator-v11"
    assert v21["prompts"]["curator"]["path"] == "prompts/curator/curator-v11.md"
    assert v21["prompts"]["curator"]["sha256"] == CURATOR_V11_SHA256
    assert v20["prompts"]["curator"]["version"] == "curator-v10"

    loaded = resolve_prompts(v21["prompts"], REPO_ROOT)
    from packages.llm_gateway.roles import parse_prompt_version

    assert parse_prompt_version(loaded[Role.CURATOR].text) == "curator-v11"
    # промпт из снимка обязан содержать новое правило
    assert "Когда вопрос просит независимые оценки или прогнозы" in loaded[Role.CURATOR].text
    # прежняя версия остаётся на диске без изменений
    assert hashlib.sha256(CURATOR_V10.read_bytes()).hexdigest() == CURATOR_V10_SHA256


@pytest.mark.unit
def test_config_v21_differs_from_v20_by_exactly_two_prompt_pins() -> None:
    """config-v21 = config-v20 ровно с двумя правками (пины explorer→v10, curator→v11):
    пороги, `claim_type_rules`, бюджеты, окна и `model.*` — байт в байт v20."""
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    v20 = json.loads(V20_PAYLOAD.read_text(encoding="utf-8"))
    assert v21.keys() == v20.keys()
    for key in v20:
        if key != "prompts":
            assert v21[key] == v20[key], f"config-v21 изменил раздел {key}"
    for role in ("extractor", "planner", "verifier"):
        assert v21["prompts"][role] == v20["prompts"][role], role
    assert v21["prompts"]["explorer"] != v20["prompts"]["explorer"]
    assert v21["prompts"]["curator"] != v20["prompts"]["curator"]


@pytest.mark.unit
def test_tampered_v11_pin_is_rejected_fail_closed() -> None:
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v21["prompts"])
    tampered["curator"] = {**tampered["curator"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
