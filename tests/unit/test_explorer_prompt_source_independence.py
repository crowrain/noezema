"""Unit: explorer-v7 — независимость источника ≠ количество адресов (T7.73, ADR-0029 разбор).

Кейс подставки .92 (STATUS T7.73 §5): годовой показатель сверялся по трём адресам —
регулятор и два новостных пересказа его же релиза. Движок правил считает независимость
по registrable domain, поэтому «три источника» получились тремя группами, хотя
собственно исследование было одно. Это не дефект правил (их состав — решение ADR-0029,
оно описано, но не реализовано); здесь закрепляется вторая половина: модель обязана
искать ПЕРВОИСТОЧНИК и независимое исследование, отличать производную публикацию от
самостоятельной и показывать расхождение чисел открыто, а не выбирать одно молча.

explorer-v6 закоммичен и не переписывается: его байты закреплены хешем; v7 обязан сохранить
всякое его правило (тест заголовков) и добавить ровно одно — правило 9.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.domain.models.enums import CompleteReason
from packages.domain.schemas.decision import ModelResponse, normalize_complete_reason
from packages.llm_gateway.roles import PromptPinError, Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

EXPLORER_V6 = REPO_ROOT / "prompts" / "explorer" / "explorer-v6.md"
EXPLORER_V7 = REPO_ROOT / "prompts" / "explorer" / "explorer-v7.md"
V15_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v15-payload.json"

#: explorer-v6 заморожен (пин config-v14): его байты не меняются никогда
EXPLORER_V6_SHA256 = "5e4cffd85e2c038d92ef7bf3264183a4eda2ca09896707dcdabebaabbad09952"
#: explorer-v7 — новый файл, его идентичность закреплена (ADR-0019 content pinning)
EXPLORER_V7_SHA256 = "41d3f7a22f5704f2b9302057fc944ab8cd069c8cfb3063fc5973df28f8ea40fa"

EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)


def _v7() -> str:
    assert EXPLORER_V7.is_file(), "explorer-v7.md is missing"
    return EXPLORER_V7.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Текст промпта одной строкой: формулировка правила переносится, проверяем смысл."""
    return re.sub(r"\s+", " ", text)


def _numbered_rules(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if re.match(r"^\d+\. ", line)]


def _examples(text: str) -> list[dict]:
    blocks = EXAMPLE_JSON.findall(text)
    assert len(blocks) == 2, f"ожидались два JSON-примера, найдено {len(blocks)}"
    return [json.loads(b) for b in blocks]


# ─── идентичность и неизменность прежней версии ───────────────────────────────


@pytest.mark.unit
def test_explorer_v7_is_a_new_file_and_v6_stays_untouched() -> None:
    assert _v7().startswith("version: explorer-v7\n")
    assert hashlib.sha256(EXPLORER_V7.read_bytes()).hexdigest() == EXPLORER_V7_SHA256
    assert hashlib.sha256(EXPLORER_V6.read_bytes()).hexdigest() == EXPLORER_V6_SHA256


@pytest.mark.unit
def test_v7_keeps_every_rule_of_v6_and_adds_only_one() -> None:
    """v7 = v6 + правило 9. Правила 1–8 сохранены дословно; правка или снятие прежнего
    правила краснят этот тест."""
    v6_rules = _numbered_rules(EXPLORER_V6.read_text(encoding="utf-8"))
    v7_rules = _numbered_rules(_v7())
    assert len(v6_rules) == 8, v6_rules
    assert v7_rules[:8] == v6_rules, f"прежние правила изменены: {v7_rules[:8]}"
    numbers = [int(rule.split(".", 1)[0]) for rule in v7_rules]
    assert numbers == [1, 2, 3, 4, 5, 6, 7, 8, 9], numbers


@pytest.mark.unit
def test_completion_protocol_of_v6_is_preserved() -> None:
    """Протокол завершения (ADR-0022) на v7 проверен заново: токен — ровно один из enum
    минус операторская остановка; пример с текстом в `reason` хост не считает завершением."""
    v7 = _v7()
    named = {tok for tok in (c.value for c in CompleteReason) if re.search(rf"\b{re.escape(tok)}\b", v7)}
    assert named <= {c.value for c in CompleteReason} - {CompleteReason.OPERATOR_STOP.value}, named
    for token in ("goal_reached", "budget_exhausted", "no_progress", "blocked"):
        assert token in v7, token
    assert "operator_stop" not in v7

    good, bad = _examples(v7)
    assert normalize_complete_reason(ModelResponse.model_validate(good).decision.reason) is (
        CompleteReason.GOAL_REACHED
    )
    assert normalize_complete_reason(ModelResponse.model_validate(bad).decision.reason) is None


# ─── правило 9: первоисточник против производной публикации ───────────────────


@pytest.mark.unit
def test_independence_is_named_as_different_primary_sources() -> None:
    """Наблюдённая ошибка подставки названа прямо: три адреса, пересказывающие один релиз."""
    v7 = _flat(_v7())
    assert "Независимость источника — не количество адресов" in v7
    assert "три сайта, пересказывающие один релиз" in v7
    assert "Два независимых источника — это два разных первоисточника, а не два адреса" in v7


@pytest.mark.unit
def test_primary_source_and_independent_research_are_both_required() -> None:
    """Требование пользователя 2026-10-07: опираться не только на официальные источники."""
    v7 = _flat(_v7())
    assert "ПЕРВОИСТОЧНИК — того, кто данные получил или посчитал" in v7
    assert "НЕЗАВИСИМОЕ исследование" in v7
    assert "другой расчёт, другая методика, другой автор" in v7


@pytest.mark.unit
def test_derivative_publication_is_named_and_not_substituted() -> None:
    v7 = _flat(_v7())
    assert "публикация, пересказывающая релиз первоисточника" in v7.lower()
    assert "это продолжение того же источника" in v7
    assert "но не подставляй вторым источником вместо самостоятельного исследования" in v7


@pytest.mark.unit
def test_divergence_must_be_shown_not_silently_picked() -> None:
    v7 = _flat(_v7())
    assert "покажи расхождение открыто в `public_rationale`" in v7
    assert "никогда не выбирай одно число молча" in v7
    assert "принеси оба наблюдения" in v7


# ─── пин в config-v15 ─────────────────────────────────────────────────────────


@pytest.mark.unit
def test_config_v15_pins_explorer_v7_and_corrupted_pin_fails_closed() -> None:
    payload = json.loads(V15_PAYLOAD.read_text(encoding="utf-8"))
    pin = payload["prompts"]["explorer"]
    assert pin["version"] == "explorer-v7"
    assert pin["path"] == "prompts/explorer/explorer-v7.md"
    assert pin["sha256"] == EXPLORER_V7_SHA256

    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v7"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V7_SHA256

    tampered = json.loads(json.dumps(payload["prompts"]))
    tampered["explorer"]["sha256"] = "0" * 64
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)


@pytest.mark.unit
def test_activation_of_v15_loads_both_pinned_prompts_from_disk() -> None:
    """Резолвер работает по пути payload'а, а не по хардкоду имени (AGENTS §7): v15 отдаёт
    модели тексты v8/v7, а прежние версии остаются на диске без изменений."""
    from packages.llm_gateway.roles import parse_prompt_version

    payload = json.loads(V15_PAYLOAD.read_text(encoding="utf-8"))
    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert parse_prompt_version(loaded[Role.CURATOR].text) == "curator-v8"
    assert parse_prompt_version(loaded[Role.EXPLORER].text) == "explorer-v7"
    for role, version in ((Role.CURATOR, "curator-v7"), (Role.EXPLORER, "explorer-v6")):
        old = REPO_ROOT / "prompts" / role.value / f"{version}.md"
        assert old.is_file(), f"{version} удалена из репо"
