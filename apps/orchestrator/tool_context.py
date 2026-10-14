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

import re
from collections.abc import Sequence
from typing import Any

from apps.orchestrator.assertion_window import _prefix_stem
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
    from it (T7.75) and that a second side of a check needs a DIFFERENT address.

    T7.97a (текстовая правка, логика и лимиты не тронуты): прежняя формулировка противоречила правилу
    13 explorer-v10, которое требует перечитать адрес, если страница с прочитанной оценкой уже выпала из
    контекста шага (бюджет T7.10). Отметка обязана называть законную цель повторного захода — вернуть
    страницу в контекст до подведения итога, — иначе модель получает два взаимоисключающих указания в
    одном контексте. Независимость от повтора по-прежнему не растёт (T7.75), и `TOOL_REPEAT_DENY_LIMIT`
    остаётся прежним: правка касается только текста отметки."""
    return (
        f"[{step}] повтор того же адреса: первая попытка была на шаге {first_step}. Новых знаний этот "
        "заход не обещает, второй группы независимости от него нет; он законен только чтобы вернуть в "
        "контекст страницу, которую снял бюджет контекста, — без перечитывания оценку честно не "
        "назвать. Для второй стороны проверки нужен другой адрес."
    )


# ─── T7.111 (часть 1, поправка T7.111a): почти-повтор поискового запроса ──────
#
# Замер менеджера по 8 сессиям стенда (фикстура step-budget-2026-10-14.txt): 54 поиска, из них 11
# запросов — почти повтор более раннего запроса той же сессии (step_budget.py, pass 2). Изначальное
# решение (2026-10-14) сравнивало множества слов Жаккаром с порогом 0,6 и не пускало в сравнение
# запросы короче 4 слов. На приёмке T7.111 найдено противоречие: порог отклоняет законные смены
# угла, сделанные одним содержательным словом, — сравнение с прошлым годом («…сентябрь 2026…» ->
# «…сентябрь 2025…» даёт 0,714), смену месяца («…июль 2026…» -> «…август 2026…» — 0,75), подмену
# актора («ВТБ …» -> «Газпромбанк …» — 0,714). Правило 14 промпта explorer-v11 прямо требует от
# модели этих смен («меняй угол, называя другого актора, другой показатель или другой период»).
# Решение T7.111a: почти-повтор — новый запрос, который НЕ ПРИНОСИТ НИ ОДНОГО НОВОГО СОДЕРЖАТЕЛЬНОГО
# СЛОВА относительно какого-либо выполненного запроса сессии: отклоняется запрос, чей набор основ
# содержательных слов является подмножеством основ одного из прежних запросов. Порог Жаккара и
# минимальная длина как условия отказа убраны (пересчёт по фикстуре и обоснование:
# docs/adr/0036-explorer-step-hygiene.md §1а и STATUS T7.111a).

#: Закрытый короткий список служебных слов: предлоги, союзы, частицы, указательные и личные
#  местоимения. Они не несут поискового содержательного слова: добавление или удаление служебного
#  слова не меняет существа запроса и почти-повтор не снимает. Бренд, показатель, актор, период и
#  число — содержательные слова; список не расширяется без отдельного решения.
SEARCH_QUERY_SERVICE_WORDS: frozenset[str] = frozenset(
    [
        "в", "во", "с", "со", "у", "на", "за", "под", "над", "по", "из", "от", "до", "к", "ко",
        "о", "об", "при", "для", "без", "через", "между", "и", "а", "но", "или", "да", "как",
        "что", "чтобы", "если", "тоже", "также", "не", "же", "бы", "ли", "этот", "эта", "эти",
        "его", "её", "ее", "их",
    ]
)

_WORD_RE = re.compile(r"[0-9a-zа-я]+")


def normalize_search_query(query: str) -> frozenset[str]:
    """Слова запроса для сравнения: нижний регистр, слова из букв и цифр (стоп-символы пунктуации —
    разделители), `ё` сводится к `е` (в фикстуре одно и то же слово написано двумя способами:
    «напряженность»/«напряжённость»). Домены делятся по точкам: `hh.ru` -> {hh, ru}, числа — по
    запятой: `2,1%` -> {2, 1}. Чистая функция: без БД, сети и состояния сессии."""
    text = mask_nul(str(query or "")).lower().replace("ё", "е")
    return frozenset(_WORD_RE.findall(text))


def content_word_stems(query: str) -> frozenset[str]:
    """Основы содержательных слов запроса (T7.111a): токены `normalize_search_query` без служебных
    слов, сведённые к основе уже имеющимся в кодовой базе приёмом фиксированного префикса
    (`assertion_window._prefix_stem`: длина 7 и больше — первые 6 знаков, 5–6 — пять, меньше 5 —
    целое слово; флексии русских падежей и английских форм живут в хвосте слова). Числа — годы и
    проценты — содержательные слова и берутся как есть, без усечения. Словоформы сводятся к одной
    основе: «Россия» и «России» -> `росси`, «зарплаты» и «зарплат» -> `зарпла`, «сентябрь» и
    «сентябре» -> `сентяб`. Ограничение приёма (принято сознательно): формы, различающиеся в первых
    пяти-шести знаках (например «цена»/«цены»), остаются разными основами — гейт такую перефразу
    пропустит; это безопасная сторона ошибки. Чистая функция: без БД, сети и состояния сессии."""
    stems: set[str] = set()
    for token in normalize_search_query(query):
        if token in SEARCH_QUERY_SERVICE_WORDS:
            continue
        stems.add(token if token.isdigit() else _prefix_stem(token))
    return frozenset(stems)


def search_near_duplicate(
    query: str, previous: Sequence[tuple[str, int]]
) -> tuple[str, int, float] | None:
    """Ближайший из ранее ВЫПОЛНЕННЫХ поисков этой сессии, который новый запрос повторяет.

    `previous` — записи выполненных запросов (текст, шаг). Почти-повтор по правилу T7.111a: все
    основы содержательных слов нового запроса уже содержатся в основах какого-то прежнего запроса,
    то есть новый запрос не приносит ни одного нового содержательного слова. Отклоняются
    перестановка слов, другие словоформы, убранные и добавленные служебные слова, удаление слов и
    повтор без единого нового слова — независимо от длины (короткий запрос без новых слов тоже
    повтор; прежний минимальный порог длины 4 и порог Жаккара 0,6 как условия отказа убраны: они
    отклоняли законные смены периода и актора — дефект T7.111a). Любое новое содержательное слово —
    другой месяц, год, актор, показатель — делает сравнение основ непроходящим: поиск выполняется
    (правило 14 explorer-v11). Возвращает (прежний запрос, его шаг, долю Жаккара нормализованных
    слов); доля — вспомогательная величина для журнала и заметки, она называет близость текстов и
    больше не является причиной отказа. При нескольких подходящих прежних запросах называется самый
    близкий по этой доле (первый при равенстве). Чистая функция: без БД, сети и состояния сессии."""
    stems = content_word_stems(query)
    words = normalize_search_query(query)
    best: tuple[str, int, float] | None = None
    for prior_text, prior_step in previous:
        if not stems <= content_word_stems(prior_text):
            continue
        prior_words = normalize_search_query(prior_text)
        ratio = len(words & prior_words) / max(1, len(words | prior_words))
        if best is None or ratio > best[2]:
            best = (prior_text, prior_step, ratio)
    return best


def render_search_near_duplicate_note(step: int, similar_query: str, similar_step: int, ratio: float) -> str:
    """Наблюдение модели об отклонённом почти-повторе (T7.111a): прежний запрос и его шаг названы,
    сказано, что новых содержательных слов нет, и названа законная замена — прочитать найденное
    (research.fetch) или добавить другого актора, другой показатель или другой период (правило 14)."""
    return (
        f"[{step}] web.search ОТКЛОНЕНО: этот запрос — почти повтор запроса шага {similar_step} "
        f"(совпадение слов {ratio:.2f}): «{similar_query[:120]}». Новых содержательных слов в нём нет; "
        "результаты такого поиска уже наблюдались выше — прочитай найденное (research.fetch) или "
        "добавь другого актора, другой показатель или другой период. Отклонённое действие тоже стоит "
        "один шаг."
    )
