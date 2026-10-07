"""Unit: контекст шага исследователя показывает модели её бюджет и схемы аргументов (T7.76).

Разобрано в STATUS.md T7.76 (§1.2–§1.3): до этой правки контекст шага называл инструменты списком имён
(`# Доступные инструменты`), но не их аргументы; раздел «Протокол действий» при этом требовал
`dependencies` и `search_statements` — полей предложения куратора, которых не принимает ни один
инструмент. Модель заполняла их в `arguments` вызова `question.create`, хост отвечал
`argument ('search_statements',): Extra inputs are not permitted`, и каждая такая попытка стоила один
шаг из десяти. Оставаться слепой к собственному бюджету модель могла потому, что число шагов нигде в
контексте не было написано: сессия `da2abfc1` на подставке израсходовала 8 шагов на официальную сторону
и до независимой оценки не дошла.

Правка аддитивна (`apps/orchestrator/tool_context.py`): реестр инструментов — единственный источник
описаний, принуждение (`extra="forbid"` в `packages/policy/tools.py`) не ослаблено, offered-список и
`tool_schema_hash` не изменились. Тесты ниже проверяют именно это: схемы совпадают с реестром, блок
ограничен по размеру, бюджет шага виден до наблюдений (усечение старых наблюдений не может его спрятать),
и хост честно говорит о повторном чтении того же адреса.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from apps.orchestrator.orchestrator import (
    EXPLORER_CONTEXT_BUDGET,
    RESEARCH_CONTEXT_BUDGET,
    Orchestrator,
    SessionContext,
)
from apps.orchestrator.tool_context import (
    STEP_BUDGET_CHAR_LIMIT,
    TOOL_CONTEXT_ADDENDA_CHARS,
    TOOL_SCHEMAS_CHAR_LIMIT,
    fetch_url_key,
    render_repeat_fetch_note,
    render_step_budget,
    render_tool_argument_schemas,
)
from packages.cognition.tokenizer import estimate_tokens
from packages.policy.profiles import effective_profile
from packages.policy.tools import all_tools

REPO_ROOT = Path(__file__).resolve().parents[2]
V16_PAYLOAD = json.loads((REPO_ROOT / "docs" / "eval" / "config-v16-payload.json").read_text(encoding="utf-8"))

#: Инструменты curated-профиля ровно так, как их выдаёт config-v16.
CURATED_TOOLS = list(V16_PAYLOAD["policy"]["capabilities"]["tools"])

pytestmark = [pytest.mark.unit]


def _orch() -> Orchestrator:
    # _explorer_context and _protocol_text are pure builders (no self attributes are read)
    return Orchestrator.__new__(Orchestrator)


def _ctx(observations: list[str] | None = None) -> SessionContext:
    return SessionContext(question_text="вопрос", plan="план", observations=observations or [])


def _tool_lines(block: str) -> list[str]:
    """Только строки контрактов инструментов (заголовок и пояснение в потолок схем не входят)."""
    assert block.startswith("# Схемы аргументов выданных инструментов"), block.splitlines()[0]
    return [line for line in block.splitlines() if line.startswith("- ")]


# ─── схемы аргументов: взяты из реестра, а не сочинены ────────────────────────


def test_schemas_cover_exactly_the_offered_tools() -> None:
    block = render_tool_argument_schemas(CURATED_TOOLS)
    for name in CURATED_TOOLS:
        assert f"- {name}(" in block, f"инструмент {name} выдан профилю, но его схемы нет в контексте"


def test_schemas_do_not_describe_tools_that_are_not_offered() -> None:
    """Блок фильтруется по offered-списку шага (T7.13: `message.reply` снят, пока входящих нет), иначе
    контекст обещал бы инструмент, которого хост отвергнет."""
    block = render_tool_argument_schemas(["research.fetch", "web.search"])
    assert "- research.fetch(" in block and "- web.search(" in block
    for name in ("message.reply", "question.create", "python.execute"):
        assert f"- {name}(" not in block, f"{name} не выдан этому шагу, но описан в контексте"


def test_question_create_contract_names_only_its_real_arguments() -> None:
    """Прямой репродюсер стендовой ошибки: модель писала `search_statements` и `dependencies` в arguments
    `question.create`. Теперь в контракте названы ровно те поля, которые принимает `_Args` реестра."""
    block = render_tool_argument_schemas(["question.create"])
    line = next(line for line in block.splitlines() if line.startswith("- question.create("))
    assert "text: строка" in line and "origin:" in line, line
    for curator_field in ("search_statements", "dependencies", "claim_type", "as_of", "scope"):
        assert f"{curator_field}:" not in line, f"{curator_field} — не аргумент question.create"
    # и то же самое сказано словами: поле предложения куратора аргументом не является
    assert "аргументом инструмента не являются" in block


def test_schema_lines_are_the_registry_own_descriptions_and_limits() -> None:
    """Проверка против первоисточника — моделей `_Args`: длина, необязательность и описание берутся из
    реестра, а не из текста теста (иначе блок разошёлся бы с тем, что проверяет policy engine)."""
    block = render_tool_argument_schemas(CURATED_TOOLS)
    for spec in all_tools():
        if spec.name not in CURATED_TOOLS:
            continue
        schema = spec.args_model.model_json_schema()
        line = next((row for row in block.splitlines() if row.startswith(f"- {spec.name}(")), "")
        assert spec.description in line, f"описание реестра пропало: {spec.name}"
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        for key, prop in properties.items():
            assert f"{key}:" in line, f"{spec.name}: аргумент {key} не описан"
            maximum = prop.get("maxLength")
            if isinstance(maximum, int):
                assert f"{maximum} знаков" in line, f"{spec.name}.{key}: потолок длины не назван"
            default = prop.get("default")
            if key not in required and default not in (None, ""):
                assert "необязательно (по умолчанию" in line, line
                assert repr(default) in line, line


def test_unknown_tool_gets_no_line() -> None:
    """`artifact.create` есть в прежних снапшотах, но его нет в реестре (исполнитель отвечал «unknown
    tool»). Блок не имеет права сочинять контракт для инструмента, которого движок не знает."""
    block = render_tool_argument_schemas(["artifact.create", "research.fetch"])
    assert "- artifact.create" not in block
    assert "- research.fetch(" in block


def test_schema_block_is_bounded_and_never_splits_a_line() -> None:
    """Потолок блока — потолок накладных расходов контекста (он вычтен из EXPLORER_CONTEXT_BUDGET).
    Переполнение срезает ЦЕЛЫЕ строки с хвоста, а не разрезает схему пополам."""
    full = {name: render_tool_argument_schemas([name]) for name in CURATED_TOOLS}
    oversized = CURATED_TOOLS * 6  # заведомо больше TOOL_SCHEMAS_CHAR_LIMIT
    block = render_tool_argument_schemas(oversized)
    lines = _tool_lines(block)
    assert len("\n".join(lines).encode("utf-8")) <= TOOL_SCHEMAS_CHAR_LIMIT, len(lines)
    assert len(lines) < len(oversized), "хвостовые строки не были сняты целиком"
    # каждая оставленная строка — полная строка малого вызова, без разреза внутри схемы
    whole = {line for rendered in full.values() for line in _tool_lines(rendered)}
    assert set(lines) <= whole, f"в блоке есть резаная строка: {set(lines) - whole}"


def test_curated_schemas_fit_the_declared_ceiling_with_room() -> None:
    """Набор инструментов curated-профиля обязан умещаться в заявленный потолок с запасом: иначе блок
    молча терял бы хвостовые инструменты, а половинчатый контракт хуже отсутствия контракта."""
    lines = _tool_lines(render_tool_argument_schemas(CURATED_TOOLS))
    assert len("\n".join(lines).encode("utf-8")) <= TOOL_SCHEMAS_CHAR_LIMIT - 500, len(lines)
    assert len(lines) == len(CURATED_TOOLS), "какой-то инструмент выдан, но не описан"


# ─── бюджет шага: хостовые числа, которых раньше не было ─────────────────────


def test_step_budget_states_both_ends_and_the_price_of_a_refusal() -> None:
    text = render_step_budget(3, 16)
    assert text.startswith("# Бюджет шага")
    assert "Шаг 3 из 16" in text
    assert "осталось действий: 13" in text
    # отказ стоит столько же: модель должна знать цену отклонённого вызова, а не считать его бесплатным
    assert "Отклонённое действие" in text and "тоже стоит один шаг" in text
    assert len(text.encode("utf-8")) <= STEP_BUDGET_CHAR_LIMIT


def test_step_budget_on_the_last_step_and_without_arguments() -> None:
    assert "осталось действий: 0" in render_step_budget(16, 16)
    # прежний вызов без шага не получает раздела: выдуманных чисел в контексте быть не может
    assert render_step_budget(None, 16) == "" and render_step_budget(3, None) == ""


def test_context_budget_grew_by_exactly_the_addenda() -> None:
    """Потолок шага вырос РОВНО на размер двух новых блоков (T7.76): старые наблюдения и самый свежий
    fetch не могли быть вытеснены этой накладной правкой."""
    assert EXPLORER_CONTEXT_BUDGET == RESEARCH_CONTEXT_BUDGET + 8_000 + TOOL_CONTEXT_ADDENDA_CHARS


# ─── повторное чтение адреса: видно модели, без нового отказа ────────────────


def test_fetch_url_key_ignores_scheme_trailing_slash_and_fragment() -> None:
    assert fetch_url_key("https://cbr.ru/analytics/") == fetch_url_key("http://cbr.ru/analytics")
    assert fetch_url_key("https://cbr.ru/a/#section") == fetch_url_key("https://cbr.ru/a")
    assert fetch_url_key("https://cbr.ru/a") != fetch_url_key("https://cbr.ru/b")
    assert fetch_url_key("") == ""


def test_repeat_note_names_the_first_attempt_and_asks_for_another_address() -> None:
    note = render_repeat_fetch_note(5, 2)
    assert "повтор того же адреса" in note
    assert "первая попытка была на шаге 2" in note
    # независимость даёт другой источник (ADR-0029), и заметка обязана это говорить
    assert "второй группы независимости от него нет" in note
    assert "нужен другой адрес" in note


# ─── контекст шага: блоки действительно доходят до модели ────────────────────


def test_explorer_context_carries_schemas_and_step_budget() -> None:
    text = _orch()._explorer_context(_ctx(), CURATED_TOOLS, None, step=4, step_limit=16)
    assert "# Доступные инструменты" in text
    assert "# Схемы аргументов выданных инструментов" in text
    assert "# Бюджет шага" in text and "Шаг 4 из 16" in text
    assert "- question.create(" in text and "- web.search(" in text


def test_step_budget_is_rendered_before_observations_so_trimming_cannot_hide_it() -> None:
    """Усечение старых наблюдений (T7.10) не имеет права прятать бюджет и схемы: они стоят до пакета и
    наблюдений, поэтому снимаются только наблюдения."""
    orch = _orch()
    observations = [f"[{i}] " + ("y" * 4_000) for i in range(15)]
    text = orch._explorer_context(_ctx(observations), CURATED_TOOLS, None, step=9, step_limit=16)
    assert text.index("# Схемы аргументов") < text.index("# Бюджет шага") < text.index("# Наблюдения")
    assert "[14] " in text, "самое свежее наблюдение вытеснено"
    assert "[0] " not in text, "старые наблюдения обязаны сниматься первыми"
    assert len(text) <= EXPLORER_CONTEXT_BUDGET


def test_legacy_call_signature_still_works() -> None:
    """Вызовы без шага (единственные прежние) должны работать как раньше — без выдуманного бюджета."""
    text = _orch()._explorer_context(_ctx(), ["research.fetch"], None)
    assert "# Схемы аргументов выданных инструментов" in text
    assert "# Бюджет шага" not in text


# ─── хостовый протокол: назван владелец кураторских полей ─────────────────────


def test_protocol_section_names_the_owner_of_the_curator_fields() -> None:
    """Ловушка, которая стоила сессии трёх шагов: раздел «Протокол действий» показывают ИССЛЕДОВАТЕЛЮ,
    а поля в нём — из предложения куратора. Теперь владелец назван прямо."""
    profile = effective_profile(V16_PAYLOAD["policy"])
    text = _orch()._protocol_text(profile)
    assert "а не аргументы инструмента" in text
    for field in ("dependencies", "search_statements", "claim_type", "as_of", "scope", "evidence_links"):
        assert f"`{field}`" in text, field
    assert "поля предложения куратора" in text


def test_protocol_section_stays_inside_its_reserved_token_budget() -> None:
    """Раздел протокола — HARD_SECTIONS с зарезервированным бюджетом снапшота (T3.8): разросшийся текст
    вытеснил бы остальной пакет, а не себя."""
    profile = effective_profile(V16_PAYLOAD["policy"])
    used = estimate_tokens(_orch()._protocol_text(profile))
    budget = int(V16_PAYLOAD["token_budgets"]["protocol"])
    assert used < budget, f"протокол занимает {used} токенов из {budget}"
