"""Unit: explorer-v10 — собственные оценки не-официальных акторов доходят до итога (T7.97).

Случай стенда .92 (STATUS T7.97, ADR-0035 §15): вопрос 5a68172e прямо просил прогнозы
Банка России и НЕЗАВИСИМЫХ аналитиков; сессия bdd9ef8d прочитала страницу SberCIB с
собственным прогнозом («…повысили прогноз инфляции на 2026 год — с 5 до 6,5%»), но
итоговая rationale назвала только цифры официальных публикаций ЦБ, а улика SberCIB не
была использована ни одним claim'ом. Причина — не в гейтах хоста: финальный сигнал
реселекции (T7.82(а)) пришпиливает окно к названной оценке, но назвать её модель не
обязывалась, а наблюдение SberCIB к финальному шагу снимается обрезкой контекста
(T7.10). Это промпт-уровень: правило 13 обязывает итоговый `public_rationale` нести
прочитанные собственные оценки акторов (с дословной цитатой — сильнейшим якорем окна)
и честно перечитывать страницу, если она выпала из контекста; хост не добавляет
гейтов, правил оценки и движений движка.

explorer-v9 закоммичен и не переписывается (пин config-v20). v10 обязан сохранить
правила 1–12 дословно (сравнение с байтами замороженного v9) и добавить ровно
правило 13; всё остальное — побайтно совпадает с v9.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPLORER_V9 = REPO_ROOT / "prompts" / "explorer" / "explorer-v9.md"
EXPLORER_V10 = REPO_ROOT / "prompts" / "explorer" / "explorer-v10.md"
V20_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v20-payload.json"
V21_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v21-payload.json"

#: explorer-v9 заморожен (пин config-v20): его байты не меняются никогда
EXPLORER_V9_SHA256 = "bf5169a06656ffce86bc3e9ea0d0c264824c11b2162a39a68b69321ce9d766af"
#: explorer-v10 — новый файл (ADR-0019 content pinning), его идентичность закреплена
#: T7.97a: пин пересчитан после замены упоминания реального ведомства в правиле 13 на
#: описание роли (файл ещё не выпущен — не запушен и нигде не активирован).
EXPLORER_V10_SHA256 = "2351e76cdd4988c0730edf066582b9850d5ef10af84171fd2eb1dbd67f074203"

_RULES_SECTION = re.compile(r"## Правила\n(.*?)\n## Примеры завершения", re.S)


def _v10() -> str:
    assert EXPLORER_V10.is_file(), "explorer-v10.md is missing"
    return EXPLORER_V10.read_text(encoding="utf-8")


def _numbered_rules(text: str) -> list[str]:
    """Правило — блок: строка «N. » и все её продолжения с отступом (тот же разбор, что в тесте v9)."""
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


# ─── неизменность прежней версии и идентичность правил 1–12 ─────────────────


@pytest.mark.unit
def test_explorer_v10_is_a_new_file_and_v9_stays_untouched() -> None:
    assert _v10().startswith("version: explorer-v10\n")
    assert hashlib.sha256(EXPLORER_V10.read_bytes()).hexdigest() == EXPLORER_V10_SHA256
    assert hashlib.sha256(EXPLORER_V9.read_bytes()).hexdigest() == EXPLORER_V9_SHA256


@pytest.mark.unit
def test_v10_keeps_every_rule_of_v9_and_adds_exactly_one() -> None:
    """v10 = v9 + правило 13. Правила 1–12 перенесены дословно — блок правил сверяется с
    байтами замороженного v9; правка или снятие прежнего правила краснит тест."""
    v9_rules = _numbered_rules(EXPLORER_V9.read_text(encoding="utf-8"))
    v10_rules = _numbered_rules(_v10())
    assert len(v9_rules) == 12, v9_rules
    assert v10_rules[:12] == v9_rules, f"прежние правила изменены: {v10_rules[:12]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v10_rules]
    assert numbers == list(range(1, 14)), numbers


@pytest.mark.unit
def test_everything_outside_the_numbered_rules_is_byte_identical_to_v9() -> None:
    """Протокол завершения, примеры JSON и разделы «Explorer»/«Контекст» не тронуты: разница
    v10 — только версия в первой строке и добавленное правило 13 внутри раздела «Правила»."""
    v9 = EXPLORER_V9.read_text(encoding="utf-8")
    v10 = _v10()

    def cut(text: str, *, rename_version: bool) -> tuple[str, str]:
        m = _RULES_SECTION.search(text)
        assert m is not None
        head = text[: m.start(1)]
        if rename_version:
            head = head.replace("explorer-v9", "explorer-v10")
        return head, text[m.end(1) :]

    v9_head, v9_tail = cut(v9, rename_version=True)
    v10_head, v10_tail = cut(v10, rename_version=False)
    assert v10_head == v9_head  # только версия в строке 1 отличается (она заменена выше)
    assert v10_tail == v9_tail

    v9_rules_block = _RULES_SECTION.search(v9).group(1)
    v10_rules_block = _RULES_SECTION.search(v10).group(1)
    assert v10_rules_block.startswith(v9_rules_block), "правила 1–12 изменены внутри раздела"
    assert v10_rules_block[len(v9_rules_block) :].lstrip("\n").startswith("13. ")


# ─── правило 13: итог обязан нести прочитанные собственные оценки акторов ───


@pytest.mark.unit
def test_rule_13_requires_own_estimates_in_the_final_rationale() -> None:
    rule = _rule(_numbered_rules(_v10()), 13)
    assert "итоговый `public_rationale` обязан назвать каждую собственную оценку" in rule, rule
    assert "актора, его значение, период и источник" in rule, rule
    assert "числа ровно как на странице" in rule, rule


@pytest.mark.unit
def test_rule_13_requires_verbatim_quote_as_window_anchor() -> None:
    """Дословная цитата — сильный якорь реселекции окна (T7.82(а)): без неё значение из
    сигнала может быть перехвачено более ранним вхождением другого числа."""
    rule = _rule(_numbered_rules(_v10()), 13)
    assert "фразу об оценке приведи дословно, в кавычках" in rule, rule
    assert "вернёт это место в окно улики куратору" in rule, rule


@pytest.mark.unit
def test_rule_13_keeps_official_surveys_official_and_retellings_non_independent() -> None:
    """Медиана опроса аналитиков, собранного ведомством, — публикация ведомства с названными
    авторами, не «независимое подтверждение»; пересказ релиза на чужом сайте — не собственная
    оценка автора сайта. Ведомство названо РОЛЬЮ, без имени реального ведомства (T7.97a):
    имя реального ведомства в правиле — половина того же дефекта «пример = ожидаемый ответ»."""
    rule = _rule(_numbered_rules(_v10()), 13)
    assert "макроэкономический опрос" in rule, rule
    assert "проводит и публикует центральный банк или статистическое ведомство страны вопроса" in rule, rule
    assert "оценка публикации ведомства" in rule, rule
    assert "не выдавай её за независимое подтверждение" in rule, rule
    assert "не делает его собственной оценкой автора сайта" in rule, rule
    assert "правила 9 и 12" in rule, rule


@pytest.mark.unit
def test_rule_13_names_the_re_read_and_keeps_honest_absence() -> None:
    """Если страница выпала из контекста (обрезка T7.10) — перечитывание, а не молчание;
    отсутствие найденного по-прежнему честный результат (правило 10)."""
    rule = _rule(_numbered_rules(_v10()), 13)
    assert "перечитай этот адрес перед завершением" in rule, rule
    assert "бюджет снял прежнее наблюдение" in rule, rule
    assert "«независимых оценок не найдено» (правило 10)" in rule, rule


# ─── пины config-v21 и неизменность config-v20 ──────────────────────────────


@pytest.mark.unit
def test_v21_pins_explorer_v10_and_v20_still_pins_v9() -> None:
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    v20 = json.loads(V20_PAYLOAD.read_text(encoding="utf-8"))
    assert v21["prompts"]["explorer"]["version"] == "explorer-v10"
    assert v21["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v10.md"
    assert v21["prompts"]["explorer"]["sha256"] == EXPLORER_V10_SHA256
    assert v20["prompts"]["explorer"]["version"] == "explorer-v9"

    loaded = resolve_prompts(v21["prompts"], REPO_ROOT)
    from packages.llm_gateway.roles import parse_prompt_version

    assert parse_prompt_version(loaded[Role.EXPLORER].text) == "explorer-v10"
    # промпт из снимка обязан содержать новое правило
    assert "Когда вопрос прямо просит независимые оценки" in loaded[Role.EXPLORER].text
    # прежняя версия остаётся на диске без изменений
    assert hashlib.sha256(EXPLORER_V9.read_bytes()).hexdigest() == EXPLORER_V9_SHA256


@pytest.mark.unit
def test_tampered_v10_pin_is_rejected_fail_closed() -> None:
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v21["prompts"])
    tampered["explorer"] = {**tampered["explorer"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
