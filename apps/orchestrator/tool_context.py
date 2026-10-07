"""Explorer step-context addenda (T7.76): the exact argument contract of each offered tool, the
host-computed step budget, and the honest note about re-reading an address already read this session.

Why this module exists (§5.4.1 reserves «протокол, схемы инструментов, правила» but only the protocol
was ever rendered): the step context used to name the tools and nothing more (`orchestrator._explorer_context`),
while the structured envelope carries `decision.arguments` as a free object
(`packages/domain/schemas/decision.py`). So the model had no host-authored contract for
`question.create` (which takes only `{text, origin}`) and filled it with fields borrowed from the
curator envelope — the host then rejected them via `extra="forbid"`, and every rejection costs a step.
Showing the registry's own schemas is additive: enforcement (`packages/policy/tools.py`, `_Args`) is
unchanged, the offered list and therefore `tool_schema_hash` are unchanged.

Pure functions on purpose (AGENTS §4: separately testable logic): no DB, no session, no network. The
schemas come from the tool registry itself, so they cannot drift from what the policy engine enforces.
"""

from __future__ import annotations

from typing import Any

from packages.domain.sanitization import mask_nul
from packages.policy.tools import get_tool

#: Ceiling of the argument-contract block. It is host overhead inside the existing
#: `EXPLORER_CONTEXT_BUDGET` (which already budgets +8000 chars over one full fetch); the curated
#: profile's ten tools fit here with room to spare, and an offered tool is never described partially:
#: whole lines are dropped from the tail rather than cut inside a schema.
TOOL_SCHEMAS_CHAR_LIMIT = 4_000

#: Ceiling of the step-accounting line (one short paragraph, host numbers only).
STEP_BUDGET_CHAR_LIMIT = 400

#: What the two addenda cost the step prompt in total. `orchestrator.EXPLORER_CONTEXT_BUDGET` grows by
#: exactly this amount (T7.76), so the newest fetch and every observation that fit before still fit.
TOOL_CONTEXT_ADDENDA_CHARS = TOOL_SCHEMAS_CHAR_LIMIT + STEP_BUDGET_CHAR_LIMIT

_SCHEMAS_HEADER = (
    "# Схемы аргументов выданных инструментов\n"
    "Аргументы каждого инструмента — ровно эти поля, другого поля в его схеме нет: лишнее поле "
    "отклоняется хостом и стоит один шаг. Поля предложения куратора (`claim_type`, `as_of`, "
    "`scope`, `dependencies`, `search_statements`, `evidence_links`) аргументом инструмента не являются."
)

_BUDGET_HEADER = "# Бюджет шага"


def _type_phrase(prop: dict[str, Any]) -> str:
    """One human-readable argument description out of the registry's own JSON schema."""
    kind = prop.get("type") or (prop.get("anyOf") or [{}])[0].get("type") or "значение"
    base = {"string": "строка", "integer": "целое число", "number": "число", "boolean": "да/нет"}.get(
        str(kind), str(kind)
    )
    limits: list[str] = []
    minimum, maximum = prop.get("minLength"), prop.get("maxLength")
    if isinstance(minimum, int) and isinstance(maximum, int):
        limits.append(f"{minimum}–{maximum} знаков")
    elif isinstance(maximum, int):
        limits.append(f"не длиннее {maximum} знаков")
    elif isinstance(minimum, int):
        limits.append(f"не короче {minimum} знаков")
    enum = prop.get("enum")
    if isinstance(enum, list):
        limits.append("одно из: " + ", ".join(repr(str(v)) for v in enum))
    return f"{base} ({'; '.join(limits)})" if limits else base


def _argument_line(name: str, prop: dict[str, Any], required: bool) -> str:
    """`text: строка (1–2000 знаков)` plus optionality/default, all read off the registry model."""
    line = f"{name}: {_type_phrase(prop)}"
    if not required:
        default = prop.get("default")
        line += ", необязательно" if default in (None, "") else f", необязательно (по умолчанию {default!r})"
    return line


def render_tool_argument_schemas(allowed_tools: list[str]) -> str:
    """The per-step contract block for the EXACT tools offered on this step.

    Filtered by `allowed_tools` (not by the profile) so it cannot contradict the per-step filtering of
    the tool list (T7.13: `message.reply` is dropped while the inbox is empty). A name that has no
    registry entry gets no line — this block never invents a contract for a tool the engine does not know.
    """
    lines: list[str] = []
    for name in allowed_tools:
        spec = get_tool(name)
        if spec is None:
            continue
        schema = spec.args_model.model_json_schema()
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        args = ", ".join(
            _argument_line(key, prop, key in required) for key, prop in properties.items()
        )
        lines.append(f"- {name}({args}) — {spec.description}")

    kept: list[str] = []
    for line in lines:
        candidate = "\n".join([*kept, line])
        if len(candidate.encode("utf-8")) > TOOL_SCHEMAS_CHAR_LIMIT and kept:
            break
        kept.append(line)
    return _SCHEMAS_HEADER + "\n" + "\n".join(kept)


def render_step_budget(step: int | None, step_limit: int | None) -> str:
    """The host-computed step accounting. Host numbers only: the model never reported them before T7.76,
    so it had no feedback about its own spending and burned the budget until `budget_exhausted`.

    Both ends are stated (this is step N of M), because 'осталось 0' alone hides what the session was
    given. A refused action costs a step too — said plainly, because the repeat guard and argument
    denials look like free failures from the model's side."""
    if step is None or step_limit is None:
        return ""
    remaining = max(step_limit - step, 0)
    return (
        f"{_BUDGET_HEADER}\n"
        f"Шаг {step} из {step_limit}; осталось действий: {remaining}. "
        "Отклонённое действие (лишние аргументы, повтор того же вызова, отвергнутый complete) тоже "
        "стоит один шаг."
    )


def fetch_url_key(url: str) -> str:
    """A deliberately coarse comparison key for the re-read note (never for a denial): scheme,
    trailing slash and fragment are dropped, host lowercased. Imprecision is harmless here — the worst
    case is a note about an address the model already sees in its own observations. The repeat DENIAL
    keeps using the exact (tool, arguments) hash (`TOOL_REPEAT_DENY_LIMIT`), unchanged."""
    text = mask_nul(str(url or "")).strip()
    for scheme in ("https://", "http://"):
        if text.lower().startswith(scheme):
            text = text[len(scheme) :]
            break
    text = text.split("#", 1)[0]
    return text.rstrip("/")


def render_repeat_fetch_note(step: int, first_step: int) -> str:
    """Honesty about a second read of the same address in one session (the stand burned two steps this
    way on `rosstat.gov.ru` and one on the already-read `cbr.ru`). The call is still executed — a repeat
    fetch legitimately checks freshness — but the model is told that the group independence does not grow
    from it (T7.75) and that a second side of a check needs a DIFFERENT address."""
    return (
        f"[{step}] повтор того же адреса: первая попытка была на шаге {first_step}. Новых знаний этот "
        "заход не обещает, второй группы независимости от него нет. Для второй стороны проверки нужен "
        "другой адрес."
    )
