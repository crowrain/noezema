"""Unit: curator-v13 и config-v23 — опубликованная оценка третьей стороны есть `external_fact` (T7.105 часть а).

Решение пользователя 2026-10-12 («Обе части»): оценка/индекс/рейтинг/опрос третьей
стороны, измеренный у самого актора и прочитанный на странице, — опубликованное сообщение
этого актора, то есть `external_fact` («актор X опубликовал значение Y за период Z») с уликой,
где значение прочитано; `empirical_conjecture` — только собственная проверяемая гипотеза.
Стендовый симптом (случай e6c8959b): куратор оформил оценку сервиса вакансий как
`empirical_conjecture`, движок правил отклонил операцию (для `empirical_conjecture` допустимо
только `experiment_run`), и весь предложение погиб вместе с двумя законными записями.

curator-v13 = curator-v12 байт в байт, кроме строки `version:` и добавленного правила 12
(вымышленный пример: «Сервис вакансий „Гелиос“», Тармения, 7,8%, 2030 — выдуманные показатель,
территория, актор и год; реальных названий акторов витрины в новом тексте нет).

config-v23 = config-v22 ровно с одной правкой: пин `prompts.curator` → curator-v13.
Дефолт bootstrap'а dev-стенда — по-прежнему config-v20, и ни один скрипт dev-стенда не
ссылается на config-v23: активация v23 — решение менеджера (как v21/v22, T7.97/T7.102).
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.domain.canonical import canonical_sha256
from packages.llm_gateway.roles import PromptPinError, Role, parse_prompt_version, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

CURATOR_V11 = REPO_ROOT / "prompts" / "curator" / "curator-v11.md"
CURATOR_V12 = REPO_ROOT / "prompts" / "curator" / "curator-v12.md"
CURATOR_V13 = REPO_ROOT / "prompts" / "curator" / "curator-v13.md"
V22_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v22-payload.json"
V23_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v23-payload.json"

#: curator-v12 заморожен (пин config-v22): его байты не меняются
CURATOR_V12_SHA256 = "06d5fe1d080fc75696ed700d7acdb9cbcdfa2283bc13bc5203e99600353321ca"
CURATOR_V11_SHA256 = "1064b15e03ef924bbf393fa08188cfd141f308a22fea9ed72028006998f64935"
#: curator-v13 — новый файл (ADR-0019 content pinning), пин config-v23
CURATOR_V13_SHA256 = "1d6809407c47b70a48f34d813e25d4a6c69a18fbb08def73113be936e6154e54"

#: canonical payload'ов: v22 заморожен, v23 закреплён (эталон отчёта T7.105)
V22_CANONICAL_SHA256 = "05ada8a415fa376dc4b443b1f073057cbf7d35627035eea551a5f2a2c34513fa"
V23_CANONICAL_SHA256 = "4cc1c296e6ee5cb0036d909b05a67a2a30406fdff8cb7f8841d48164ec64778f"

#: добавленное правило 12 — ровно эти строки, приложенные в конец файла
NEW_RULE_BLOCK = (
    "12. Оценка, индекс, рейтинг, опрос или замер третьей стороны, прочитанный на странице\n"
    "   (аналитическая группа, агентство, сервис вакансий, опросная служба), — опубликованное\n"
    "   сообщение этого актора, а не твоя гипотеза: записывай его как `external_fact` в форме\n"
    "   «актор X опубликовал значение Y за период Z» и связывай с evidence, где это значение\n"
    "   прочитано (пример — вымышленный случай: «Сервис вакансий „Гелиос“: рост конкуренции\n"
    "   соискателей на вакансии в Тармении за июнь 2030 года — 7,8% к июню 2029 года»).\n"
    "   `empirical_conjecture` — только собственная гипотеза, проверяемая опытом, и никогда не\n"
    "   опирается на одно лишь утверждение источника: движок правил отклонит такую операцию\n"
    "   (для `empirical_conjecture` допустимо только `experiment_run`). Официальная публикация\n"
    "   ведомства остаётся публикацией ведомства (правило 11)."
)

#: вымышленные опоры примера — их не должно быть ни в одних реальных данных
FICTIONAL_MARKS = ("тармен", "гелиос")
#: настоящие акторы витрины — их не должно быть в НОВОМ тексте правила 12
REAL_ACTOR_MARKS = ("росстат", "hh.ru", "hr-ratings", "инфом", "сбер", "банк россии", "интерфакс")

REAL_DATA_SOURCES = [
    REPO_ROOT / "tests" / "fixtures" / "t797a" / "claims_live.tsv",
    REPO_ROOT / "tests" / "fixtures" / "t797a" / "control_question.txt",
    REPO_ROOT / "tests" / "fixtures" / "t797" / "sbercib_2026_34ecbe51.normalized.txt",
]

_RULES_SECTION = re.compile(r"## Правила\n(.*)\Z", re.S)


def _v13() -> str:
    assert CURATOR_V13.is_file(), "curator-v13.md is missing"
    return CURATOR_V13.read_text(encoding="utf-8")


def _numbered_rules(text: str) -> list[str]:
    """Правило — блок: строка «N. » и все её продолжения с отступом (тот же разбор, что в тесте v12)."""
    section = _RULES_SECTION.search(text)
    assert section is not None
    body = section.group(1)
    blocks: list[list[str]] = []
    for line in body.splitlines():
        if re.match(r"^\d+\. ", line):
            blocks.append([line])
        elif blocks and (line.startswith("   ") or line.startswith("    ") or line.startswith("\t")):
            blocks[-1].append(line)
    return ["\n".join(block).rstrip() for block in blocks]


def _real_data_texts() -> list[tuple[str, str]]:
    paths = list(REAL_DATA_SOURCES)
    paths += sorted((REPO_ROOT / "docs" / "eval").glob("question-set-*.jsonl"))
    paths += sorted((REPO_ROOT / "tests" / "fixtures" / "t7101").glob("*.txt"))
    out: list[tuple[str, str]] = []
    for path in paths:
        if path.is_file():
            out.append((path.name, path.read_text(encoding="utf-8", errors="replace").lower()))
    return out


# ─── идентичность версий: v13 новый файл, v12 и v11 не тронуты ──────────────


@pytest.mark.unit
def test_curator_v13_is_a_new_file_and_v12_v11_stay_untouched() -> None:
    assert _v13().startswith("version: curator-v13\n")
    assert hashlib.sha256(CURATOR_V13.read_bytes()).hexdigest() == CURATOR_V13_SHA256
    assert hashlib.sha256(CURATOR_V12.read_bytes()).hexdigest() == CURATOR_V12_SHA256
    assert hashlib.sha256(CURATOR_V11.read_bytes()).hexdigest() == CURATOR_V11_SHA256


@pytest.mark.unit
def test_v13_differs_from_v12_by_version_line_and_appended_rule_only() -> None:
    """Побайтный diff curator-v12 → curator-v13: ровно строка `version:` и приложенное в конец
    правило 12. Любая другая правка (в правилах 1–11, в протоколе, в примерах) краснит тест."""
    v12 = CURATOR_V12.read_text(encoding="utf-8")
    expected = v12.replace("version: curator-v12", "version: curator-v13", 1) + NEW_RULE_BLOCK + "\n"
    assert _v13() == expected, "diff v12 → v13 шире, чем версия + правило 12"


@pytest.mark.unit
def test_rules_1_to_11_are_byte_identical_and_rule_12_carries_the_decision() -> None:
    v12_rules = _numbered_rules(CURATOR_V12.read_text(encoding="utf-8"))
    v13_rules = _numbered_rules(_v13())
    assert len(v12_rules) == 11, len(v12_rules)
    assert len(v13_rules) == 12, len(v13_rules)
    assert v13_rules[:11] == v12_rules, "правила 1–11 изменены"
    assert v13_rules[11] == NEW_RULE_BLOCK
    # смысл решения назван в правиле явными опорами
    for anchor in (
        "опубликованное",
        "записывай его как `external_fact`",
        "«актор X опубликовал значение Y за период Z»",
        "evidence, где это значение\n   прочитано",
        "`empirical_conjecture` — только собственная гипотеза, проверяемая опытом",
        "допустимо только `experiment_run`",
        "ведомства остаётся публикацией ведомства",
    ):
        assert anchor in v13_rules[11], anchor


# ─── пример вымышлен, реальных названий акторов в новом тексте нет ───────────


@pytest.mark.unit
def test_new_rule_example_is_fictional_and_names_no_real_actor() -> None:
    text = _v13()
    assert "пример — вымышленный" in NEW_RULE_BLOCK
    lowered_new = NEW_RULE_BLOCK.lower()
    for mark in REAL_ACTOR_MARKS:
        assert mark not in lowered_new, f"в правиле 12 осталось реальное название {mark!r}"
    # прежний текст v12 содержит упоминания ведомств (правило 11) — они не тронуты;
    # проверка выше — именно про НОВЫЙ блок, он обязан быть вымышленным
    sources = _real_data_texts()
    assert sources, "фикстуры реальных данных отсутствуют"
    lowered = text.lower()
    for name in FICTIONAL_MARKS:
        assert name in lowered, f"вымышленное имя {name!r} не найдено в промпте"
        for source_name, content in sources:
            assert name not in content, (
                f"вымышленное имя {name!r} встречается в реальных данных {source_name}"
            )
    # связки значения примера не должны совпадать с живыми строками витрины
    for source_name, content in sources:
        for line in content.splitlines():
            assert not ("7,8" in line and "2030" in line), (
                f"связка примера 7,8 % + 2030 встречается в реальной строке {source_name}"
            )


# ─── пины config-v23 и неизменность config-v22 ──────────────────────────────


@pytest.mark.unit
def test_v23_pins_curator_v13_and_v22_still_pins_v12() -> None:
    v23 = json.loads(V23_PAYLOAD.read_text(encoding="utf-8"))
    v22 = json.loads(V22_PAYLOAD.read_text(encoding="utf-8"))
    assert v23["prompts"]["curator"]["version"] == "curator-v13"
    assert v23["prompts"]["curator"]["path"] == "prompts/curator/curator-v13.md"
    assert v23["prompts"]["curator"]["sha256"] == CURATOR_V13_SHA256
    assert v22["prompts"]["curator"]["version"] == "curator-v12"
    assert hashlib.sha256(V22_PAYLOAD.read_bytes()).hexdigest() == (
        "0edf88e3d39c880e0a63a21ec5515d37e88fb94806b19aaec103a10574660ac7"
    )

    loaded = resolve_prompts(v23["prompts"], REPO_ROOT)
    assert parse_prompt_version(loaded[Role.CURATOR].text) == "curator-v13"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V13_SHA256
    # промпт из снимка обязан нести новое правило и вымышленный пример
    assert "записывай его как `external_fact`" in loaded[Role.CURATOR].text
    assert "пример — вымышленный" in loaded[Role.CURATOR].text


@pytest.mark.unit
def test_config_v23_differs_from_v22_by_exactly_one_prompt_pin() -> None:
    v23 = json.loads(V23_PAYLOAD.read_text(encoding="utf-8"))
    v22 = json.loads(V22_PAYLOAD.read_text(encoding="utf-8"))
    assert v23.keys() == v22.keys()
    for key in v22:
        if key != "prompts":
            assert v23[key] == v22[key], f"config-v23 изменил раздел {key}"
    changed = {r for r in v23["prompts"] if v23["prompts"][r] != v22["prompts"][r]}
    assert changed == {"curator"}, changed
    # canonical-эталон закреплён (тот же порядок канонической сериализации, что у activation)
    assert canonical_sha256(v22) == V22_CANONICAL_SHA256
    assert canonical_sha256(v23) == V23_CANONICAL_SHA256


@pytest.mark.unit
def test_tampered_v13_pin_is_rejected_fail_closed() -> None:
    v23 = json.loads(V23_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v23["prompts"])
    tampered["curator"] = {**tampered["curator"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)


# ─── стенд: дефолт bootstrap'а не тронут, config-v23 нигде не активирован ───


@pytest.mark.unit
def test_dev_stand_scripts_do_not_activate_config_v23() -> None:
    """Активация config-v23 — решение менеджера: скрипты dev-стенда не ссылаются на v23,
    дефолт bootstrap'а и reset-db.sh — прежний (config-v20-payload.json)."""
    scripts = sorted((REPO_ROOT / "deploy" / "dev-stand").glob("*.sh"))
    texts = {path.name: path.read_text(encoding="utf-8") for path in scripts}
    assert texts, "скрипты dev-стенда не найдены"
    for name, text in texts.items():
        assert "config-v23" not in text, f"{name} уже ссылается на config-v23"
    assert "config-v20-payload.json" in texts["bootstrap.sh"]
    assert "config-v20-payload.json" in texts["reset-db.sh"]
