"""Unit: curator-v12 — реальный пример абзаца перефразы правила 7 заменён вымышленным (T7.102).

Решение пользователя 2026-10-12 («да, готовь»): абзац перефразы правила 7 curator-v11 содержал
реальные данные витрины — «годовая инфляция 5,59% по итогам 2025 года» и «по данным Росстата»
(реальные утверждения витрины 9266248e и e189c80d). В T7.97a этот текст править было нельзя:
правила 1–10 curator-v11 обязаны быть байт в байт curator-v10, замороженным пином config-v20
(ADR-0019), и попадание было закреплено в `INHERITED_REAL_DATA` как остаточный дефект.

curator-v12 = curator-v11 байт в байт, кроме строки `version:` и абзаца перефразы правила 7:
реальный пример заменён ВЫМЫШЛЕННЫМ того же устройства — тот же показатель-значение-период уже
записан как `[c:…]`, а предложение приносит перефразу «со ссылкой на первоисточник» с
`existing_claim_id: null`; выдуманные показатель (уровень безработицы), территория (Тармения),
год (2029), число (6,1%) и ведомство (Тарменская служба статистики). Смысл правила 7 и всех
остальных правил не изменён.

curator-v11, explorer-v10 и прежние payload'ы не переписываются: v11 — выпущенный пин config-v21,
история. Страж test_prompt_example_no_real_data.py проверяет чистоту нового текста v12 и требует
пустого `INHERITED_REAL_DATA` для нынешнего curator-промпта.
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
CURATOR_V12 = REPO_ROOT / "prompts" / "curator" / "curator-v12.md"
V20_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v20-payload.json"
V21_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v21-payload.json"
V22_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v22-payload.json"

#: curator-v10 и curator-v11 заморожены (пины config-v20 и config-v21): их байты не меняются
CURATOR_V10_SHA256 = "8b940bf573bcd19a1302b0a6ed48fee00ec87dbc81f71fa56063f510113c040e"
CURATOR_V11_SHA256 = "1064b15e03ef924bbf393fa08188cfd141f308a22fea9ed72028006998f64935"
#: curator-v12 — новый файл (ADR-0019 content pinning), пин config-v22
CURATOR_V12_SHA256 = "06d5fe1d080fc75696ed700d7acdb9cbcdfa2283bc13bc5203e99600353321ca"

#: абзац перефразы правила 7 в curator-v11 (реальные данные витрины) — закреплён как история
OLD_PARAPHRASE_BLOCK = (
    "   Та же ошибка в форме перефразы (наблюдена на стенде): в разделе «Знание»\n"
    "   строка `[c:…]` с годовой инфляцией 5,59% по итогам 2025 года уже записана\n"
    "   (supported, E4), а предложение приносит «Официальная годовая инфляция в\n"
    "   России по итогам 2025 года … составила 5,59% (по данным Росстата)» — то же\n"
    "   число того же показателя за тот же период, только переформулированное со\n"
    "   ссылкой на первоисточник, и `existing_claim_id: null`. Это тоже новый"
)
#: тот же абзац в curator-v12: вымышленный случай той же формы (T7.102)
NEW_PARAPHRASE_BLOCK = (
    "   Та же ошибка в форме перефразы (наблюдена на стенде; пример — вымышленный\n"
    "   случай той же формы): в разделе «Знание» строка `[c:…]` с уровнем\n"
    "   безработицы в Тармении 6,1% по итогам 2029 года уже записана (supported,\n"
    "   E4), а предложение приносит «Официальный уровень безработицы в Тармении\n"
    "   по итогам 2029 года … составил 6,1% (по данным Тарменской службы\n"
    "   статистики)» — то же число того же показателя за тот же период, только\n"
    "   переформулированное со ссылкой на первоисточник, и `existing_claim_id: null`.\n"
    "   Это тоже новый"
)

#: реальные данные витрины, которых в curator-v12 не должно остаться вовсе
REAL_LEAK_MARKS = ("5,59", "Росстат", "годовая инфляция в России")

#: вымышленные имена примера — их не должно быть ни в одних реальных данных
FICTIONAL_NAMES = ("тармен",)

REAL_DATA_SOURCES = [
    REPO_ROOT / "tests" / "fixtures" / "t797a" / "claims_live.tsv",
    REPO_ROOT / "tests" / "fixtures" / "t797a" / "control_question.txt",
    REPO_ROOT / "tests" / "fixtures" / "t797" / "sbercib_2026_34ecbe51.normalized.txt",
]

_RULES_SECTION = re.compile(r"## Правила\n(.*)\Z", re.S)


def _v12() -> str:
    assert CURATOR_V12.is_file(), "curator-v12.md is missing"
    return CURATOR_V12.read_text(encoding="utf-8")


def _numbered_rules(text: str) -> list[str]:
    """Правило — блок: строка «N. » и все её продолжения с отступом (тот же разбор, что в тесте v11)."""
    blocks: list[list[str]] = []
    for line in text.splitlines():
        if re.match(r"^\d+\. ", line):
            blocks.append([line])
        elif blocks and (line.startswith("   ") or line.startswith("\t")):
            blocks[-1].append(line)
    return ["\n".join(block).rstrip() for block in blocks]


def _real_data_texts() -> list[tuple[str, str]]:
    """Реальные данные (фикстуры витрины + корпуса вопросов + страницы стендового случая T7.101),
    прочитанные побайтно с побайтовым же декодированием (ошибки не глушатся: replacement-символ
    не должен спрятать совпадение)."""
    paths = list(REAL_DATA_SOURCES)
    paths += sorted((REPO_ROOT / "docs" / "eval").glob("question-set-*.jsonl"))
    paths += sorted((REPO_ROOT / "tests" / "fixtures" / "t7101").glob("*.txt"))
    out: list[tuple[str, str]] = []
    for path in paths:
        if path.is_file():
            out.append((path.name, path.read_text(encoding="utf-8", errors="replace").lower()))
    return out


# ─── идентичность версий: v12 новый файл, v11 и v10 не тронуты ──────────────


@pytest.mark.unit
def test_curator_v12_is_a_new_file_and_v11_v10_stay_untouched() -> None:
    assert _v12().startswith("version: curator-v12\n")
    assert hashlib.sha256(CURATOR_V12.read_bytes()).hexdigest() == CURATOR_V12_SHA256
    assert hashlib.sha256(CURATOR_V11.read_bytes()).hexdigest() == CURATOR_V11_SHA256
    assert hashlib.sha256(CURATOR_V10.read_bytes()).hexdigest() == CURATOR_V10_SHA256


@pytest.mark.unit
def test_v12_differs_from_v11_by_version_line_and_paraphrase_paragraph_only() -> None:
    """Побайтный diff curator-v11 → curator-v12: ровно строка `version:` и абзац перефразы
    правила 7. Любая другая правка (в правиле, в протоколе, в примере) краснит тест."""
    v11 = CURATOR_V11.read_text(encoding="utf-8")
    expected = v11.replace("version: curator-v11", "version: curator-v12", 1)
    assert OLD_PARAPHRASE_BLOCK in expected, "закреплённый абзац v11 не найден в самом v11"
    expected = expected.replace(OLD_PARAPHRASE_BLOCK, NEW_PARAPHRASE_BLOCK, 1)
    assert _v12() == expected, "diff v11 → v12 шире, чем версия + абзац перефразы"


# ─── правило 7: смысл сохранён, пример вымышлен ─────────────────────────────


@pytest.mark.unit
def test_rule_7_keeps_its_meaning_and_only_the_example_changed() -> None:
    """Правила 1–6 и 8–11 байт в байт прежние; правило 7 отличается ровно закреплённой заменой
    абзаца. Смысловые опоры правила 7 обязаны остаться в обоих текстах."""
    v11_rules = _numbered_rules(CURATOR_V11.read_text(encoding="utf-8"))
    v12_rules = _numbered_rules(_v12())
    assert len(v11_rules) == 11 and len(v12_rules) == 11, (len(v11_rules), len(v12_rules))
    assert v12_rules[:6] == v11_rules[:6], "правила 1–6 изменены"
    assert v12_rules[7:] == v11_rules[7:], "правила 8–11 изменены"
    assert v12_rules[6] == v11_rules[6].replace(OLD_PARAPHRASE_BLOCK, NEW_PARAPHRASE_BLOCK, 1), (
        "правило 7 изменено шире абзаца перефразы"
    )
    for anchor in (
        "Перепроверка: если факт уже установлен",
        "то же число того же показателя за тот же период, только",
        "Это тоже новый",
        "claim-дубль. Идентичность факта определяется показателем, его значением и",
        "периодом, а не пословным совпадением формулировок",
        "Сверенную по источникам перефразу оформляй перепроверкой",
        "Хост заметит дубль",
        "заполнить ты сам.",
    ):
        assert anchor in v12_rules[6], anchor


@pytest.mark.unit
def test_paraphrase_example_is_fictional_and_carries_no_showcase_data() -> None:
    """Выдуманные показатель, территория, год, число и ведомство; пометка «вымышленный случай»;
    реальных данных витрины (Росстат, 5,59 %, «годовая инфляция в России») во всём файле v12 не
    осталось; вымышленные имена не встречаются ни в фикстурах витрины, ни в корпусах вопросов,
    ни в страницах случая T7.101; связка «6,1 % + 2029» не встречается ни в одной строке
    реальных данных (иначе пример был бы опознаваем как живой случай)."""
    text = _v12()
    assert "пример — вымышленный" in text
    assert "уровень безработицы в Тармении" in NEW_PARAPHRASE_BLOCK
    assert "6,1%" in NEW_PARAPHRASE_BLOCK and "2029" in NEW_PARAPHRASE_BLOCK
    assert "Тарменской службы" in NEW_PARAPHRASE_BLOCK
    for mark in REAL_LEAK_MARKS:
        assert mark not in text, f"в curator-v12 остался реальный фрагмент {mark!r}"
    sources = _real_data_texts()
    assert sources, "фикстуры реальных данных отсутствуют"
    lowered = text.lower()
    for name in FICTIONAL_NAMES:
        assert name in lowered, f"вымышленное имя {name!r} не найдено в промпте"
        for source_name, content in sources:
            assert name not in content, (
                f"вымышленное имя {name!r} встречается в реальных данных {source_name}"
            )
    for source_name, content in sources:
        for line in content.splitlines():
            assert not ("6,1" in line and "2029" in line), (
                f"связка примера 6,1 % + 2029 встречается в реальной строке {source_name}"
            )


# ─── пины config-v22 и неизменность config-v21 / config-v20 ─────────────────


@pytest.mark.unit
def test_v22_pins_curator_v12_and_v21_still_pins_v11() -> None:
    v22 = json.loads(V22_PAYLOAD.read_text(encoding="utf-8"))
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    assert v22["prompts"]["curator"]["version"] == "curator-v12"
    assert v22["prompts"]["curator"]["path"] == "prompts/curator/curator-v12.md"
    assert v22["prompts"]["curator"]["sha256"] == CURATOR_V12_SHA256
    assert v21["prompts"]["curator"]["version"] == "curator-v11"

    loaded = resolve_prompts(v22["prompts"], REPO_ROOT)
    from packages.llm_gateway.roles import parse_prompt_version

    assert parse_prompt_version(loaded[Role.CURATOR].text) == "curator-v12"
    assert loaded[Role.CURATOR].sha256 == CURATOR_V12_SHA256
    # промпт из снимка обязан нести вымышленный пример правила 7
    assert "пример — вымышленный" in loaded[Role.CURATOR].text
    # прежние версии остаются на диске без изменений
    assert hashlib.sha256(CURATOR_V11.read_bytes()).hexdigest() == CURATOR_V11_SHA256
    assert hashlib.sha256(CURATOR_V10.read_bytes()).hexdigest() == CURATOR_V10_SHA256


@pytest.mark.unit
def test_config_v22_differs_from_v21_by_exactly_one_prompt_pin() -> None:
    """config-v22 = config-v21 ровно с одной правкой (пин curator→v12): пороги,
    `claim_type_rules`, бюджеты, окна и `model.*` — байт в байт v21; explorer-v10 и остальные
    пины не тронуты."""
    v22 = json.loads(V22_PAYLOAD.read_text(encoding="utf-8"))
    v21 = json.loads(V21_PAYLOAD.read_text(encoding="utf-8"))
    assert v22.keys() == v21.keys()
    for key in v21:
        if key != "prompts":
            assert v22[key] == v21[key], f"config-v22 изменил раздел {key}"
    for role in ("explorer", "extractor", "planner", "verifier"):
        assert v22["prompts"][role] == v21["prompts"][role], role
    assert v22["prompts"]["curator"] != v21["prompts"]["curator"]
    changed = {r for r in v22["prompts"] if v22["prompts"][r] != v21["prompts"][r]}
    assert changed == {"curator"}, changed


@pytest.mark.unit
def test_tampered_v12_pin_is_rejected_fail_closed() -> None:
    v22 = json.loads(V22_PAYLOAD.read_text(encoding="utf-8"))
    tampered = dict(v22["prompts"])
    tampered["curator"] = {**tampered["curator"], "sha256": "0" * 64}
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)
