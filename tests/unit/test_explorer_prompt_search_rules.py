"""Unit: explorer-v6 — режим правдив, поиск отделён от чтения (T7.71, ADR-0027 §4).

config-v13 на curated-стенде выдал модели `research.fetch`, а prompt explorer-v5 утверждал
«Ты работаешь в режиме **Sealed**: сети нет» — модель сообщала хосту о недоступности сети, которой у
неё был доступ (отчёт менеджера, разбор STATUS.md T7.71). Второй дефект той же сессии: модель
переносила в аргументы инструменты поля из чужих схем (`dependencies`, `search_statements`) и получала
«Extra inputs are not permitted». Третий: найденные заголовки выдавались за факт.

explorer-v6 правит все три, ничего из закреплённого в v5 не ослабляя (ADR-0022 протокол завершения
проверяется здесь на v6 повторно). v5 остаётся как закоммичен: payload'ы и промпты не переписываются
никогда, его байты закреплены хешем.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import pytest

from packages.domain.models.enums import CompleteReason
from packages.domain.schemas.decision import ModelResponse, normalize_complete_reason
from packages.llm_gateway.roles import Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]
EXPLORER_V5 = REPO_ROOT / "prompts" / "explorer" / "explorer-v5.md"
EXPLORER_V6 = REPO_ROOT / "prompts" / "explorer" / "explorer-v6.md"
V14_PAYLOAD = REPO_ROOT / "docs" / "eval" / "config-v14-payload.json"
EXAMPLE_JSON = re.compile(r"```json\n(.*?)```", re.S)

#: explorer-v5 заморожен (пін config-v12/v13): его байты не меняются никогда
EXPLORER_V5_SHA256 = "3b1fd49d687a39ab88809ac208cc9dfc4f0390b0da3a9ea848f888cf22a69c3c"
#: explorer-v6 — новый файл, его идентичность закреплена (ADR-0019 content pinning)
EXPLORER_V6_SHA256 = "5e4cffd85e2c038d92ef7bf3264183a4eda2ca09896707dcdabebaabbad09952"

# операторская остановка — хостовое расширение: prompt его не предлагает модели
HOST_ONLY_TOKENS = frozenset({CompleteReason.OPERATOR_STOP.value})


def _v6() -> str:
    assert EXPLORER_V6.is_file(), "explorer-v6.md is missing"
    return EXPLORER_V6.read_text(encoding="utf-8")


def _flat(text: str) -> str:
    """Текст промпта одной строкой: формулировка правила может переноситься, а проверяем мы смысл."""
    return re.sub(r"\s+", " ", text)


def _examples(text: str) -> list[dict]:
    blocks = EXAMPLE_JSON.findall(text)
    assert len(blocks) == 2, f"ожидались два JSON-примера, найдено {len(blocks)}"
    return [json.loads(b) for b in blocks]


# ─── идентичность и неизменность прежних версий ──────────────────────────────


@pytest.mark.unit
def test_explorer_v6_is_a_new_file_and_v5_stays_untouched() -> None:
    assert EXPLORER_V6.read_text(encoding="utf-8").startswith("version: explorer-v6\n")
    assert hashlib.sha256(EXPLORER_V6.read_bytes()).hexdigest() == EXPLORER_V6_SHA256
    # прежняя версия не переписана под новую (payload'ы и промпты не правятся задним числом)
    assert hashlib.sha256(EXPLORER_V5.read_bytes()).hexdigest() == EXPLORER_V5_SHA256


@pytest.mark.unit
def test_config_v14_pins_explorer_v6_and_corrupted_pin_fails_closed() -> None:
    """Пин — содержательный (ADR-0019): путь и sha256 файла. Подсованный sha не «проходит»:
    резолвер поднимает PromptPinError, и сессия не стартует."""
    from packages.llm_gateway.roles import PromptPinError

    payload = json.loads(V14_PAYLOAD.read_text(encoding="utf-8"))
    pin = payload["prompts"]["explorer"]
    assert pin["version"] == "explorer-v6"
    assert pin["path"] == "prompts/explorer/explorer-v6.md"
    assert pin["sha256"] == EXPLORER_V6_SHA256

    loaded = resolve_prompts(payload["prompts"], REPO_ROOT)
    assert loaded[Role.EXPLORER].version == "explorer-v6"
    assert loaded[Role.EXPLORER].sha256 == EXPLORER_V6_SHA256

    tampered = json.loads(json.dumps(payload["prompts"]))
    tampered["explorer"]["sha256"] = "0" * 64
    with pytest.raises(PromptPinError):
        resolve_prompts(tampered, REPO_ROOT)


# ─── правдивость режима (дефект №1 отчёта менеджера) ─────────────────────────


@pytest.mark.unit
def test_v5_claims_a_closed_network_and_v6_does_not() -> None:
    """Что именно исправлено: v5 объявляет «режим Sealed: сети нет». В v6 этого утверждения нет —
    режим берётся из контекста шага, и модель не имеет права выдумывать его."""
    v5 = EXPLORER_V5.read_text(encoding="utf-8")
    assert "сети нет" in v5  # прежняя формулировка закреплена: она и была дефектом

    v6 = _v6()
    assert "сети нет, доступны только локальные инструменты" not in v6
    assert "режиме **Sealed**" not in v6
    # вместо выдумывания режима — указание на источник истины и запрет обратного утверждения
    assert "Доступные инструменты" in v6
    assert "не утверждай, что сети нет" in v6
    assert "research.fetch" in v6 and "web.search" in v6


# ─── поиск не равен чтению и не равен доказательству (дефект №3) ──────────────


@pytest.mark.unit
def test_search_is_navigation_and_only_fetch_confirms() -> None:
    v6 = _v6()
    rule = next(
        (line for line in v6.splitlines() if "навигация" in line and "web.search" in line),
        "",
    )
    assert rule, "правило «сначала навигация, потом чтение» отсутствует"

    flat = _flat(v6)
    # заголовки выдачи не являются фактом и не становятся доказательствами
    assert "не становятся доказательствами" in flat
    # единственная дорога к подтверждению — чтение выбранной страницы через research.fetch
    assert "research.fetch" in v6
    assert "только содержимое страницы становится подтверждением" in flat
    # внешний факт требует двух независимых источников (порог rules engine, не выдумка prompt'а)
    assert "два независимых источника" in flat

    # и observations поиска описывают найденное, а не истину
    tail = _flat(v6.split("## Контекст")[-1])
    assert "описывают только то, что нашлось" in tail


@pytest.mark.unit
def test_search_arguments_are_only_the_query() -> None:
    """Аргументы `web.search` — ровно `query`: ни URL, ни текст вопроса целиком. Аргументы описывает
    реестр (extra="forbid"), prompt не должен подталкивать к лишнему."""
    v6 = _v6()
    assert "В `web.search` передавай только" in v6
    assert "не URL" in v6

    from packages.policy.tools import get_tool  # аргументы описывает реестр, не prompt

    spec = get_tool("web.search")
    assert spec is not None
    assert {"query"} == set(spec.args_model.model_fields)


@pytest.mark.unit
def test_foreign_schema_fields_are_forbidden_in_arguments() -> None:
    """Дефект №2 отчёта менеджера: модель переносила в аргументы поля из чужих схем — реестр
    отклоняет их (`extra="forbid"`), prompt обязан этого не поддерживать."""
    v6 = _v6()
    assert "dependencies" in v6 and "search_statements" in v6
    assert "ровно те поля, которые требует схема" in v6

    from packages.policy.tools import get_tool

    spec = get_tool("question.create")
    assert spec is not None
    from pydantic import ValidationError

    with pytest.raises(ValidationError):  # extra="forbid": чужие поля отклоняются схемой
        spec.args_model.model_validate({"text": "вопрос", "dependencies": []})


# ─── протокол завершения ADR-0022 сохранён и на v6 ────────────────────────────


@pytest.mark.unit
def test_v6_completion_tokens_match_enum_minus_operator_stop() -> None:
    v6 = _v6()
    named = {tok for tok in (c.value for c in CompleteReason) if re.search(rf"\b{re.escape(tok)}\b", v6)}
    assert named <= {c.value for c in CompleteReason} - HOST_ONLY_TOKENS, named
    for token in ("goal_reached", "budget_exhausted", "no_progress", "blocked"):
        assert token in v6, token
    assert "operator_stop" not in v6


@pytest.mark.unit
def test_v6_examples_are_envelope_valid_and_only_one_normalizes() -> None:
    good, bad = _examples(_v6())
    # оба примера — валидные конверт-ответы (схема хоста их принимает: дефект был в тексте reason)
    good_response = ModelResponse.model_validate(good)
    bad_response = ModelResponse.model_validate(bad)

    assert normalize_complete_reason(good_response.decision.reason) is CompleteReason.GOAL_REACHED
    # свободный текст в reason хост не считает завершением (ADR-0022/T7.49)
    assert normalize_complete_reason(bad_response.decision.reason) is None
