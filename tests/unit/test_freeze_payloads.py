"""Frozen EVAL-3 payload budget validation (T7.7, §5.4.1, §22.2).

Regression: the first EVAL-3 launch (2026-09-16) failed with 0/50
sessions — ``token budgets invalid: section limits sum 26624 >
input_budget 22528`` — because max_output_tokens was raised 4096 → 8192
at freeze while the section sum (byte-identical to EVAL-2) was not
recomputed against the new budget. These tests load the actual frozen
payload files and require a valid budget before any session can start.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from packages.cognition.tokenizer import TokenBudgets
from packages.llm_gateway.roles import Role, resolve_prompts

REPO_ROOT = Path(__file__).resolve().parents[2]

# Section budgets byte-identical to EVAL-2/bootstrap (Σ = 26624) —
# A/B comparability of EVAL-3 against the EVAL-2 baseline (§22.2).
EVAL2_SECTIONS = {
    "claims_evidence": 8192,
    "contradictions": 3072,
    "identity": 2048,
    "last_session": 2048,
    "messages": 2048,
    "protocol": 4096,
    "question_plan": 3072,
    "recent_errors": 2048,
}


def _load(name: str) -> dict:
    return json.loads((REPO_ROOT / "docs" / "eval" / name).read_text())


@pytest.mark.unit
@pytest.mark.parametrize("name", ["config-v2-payload.json", "config-v3-payload.json"])
def test_frozen_payload_token_budgets_valid(name: str) -> None:
    payload = _load(name)
    budgets = TokenBudgets.from_snapshot(payload["model"], payload["token_budgets"])
    # The config must be startable: no budget problem at all.
    assert budgets.validate() == []
    # A/B comparability: the section budgets stay as in EVAL-2/bootstrap.
    assert budgets.section_limits == EVAL2_SECTIONS
    assert sum(budgets.section_limits.values()) == 26624
    assert sum(budgets.section_limits.values()) <= budgets.input_budget


@pytest.mark.unit
def test_frozen_payloads_v2_v3_budgets_identical() -> None:
    """The v2→v3 diff is exactly the two volatility lines: the model
    block and the section budgets must not drift, because the mid-run
    v2→v3 activation must not change the budget the model sees."""
    v2 = _load("config-v2-payload.json")
    v3 = _load("config-v3-payload.json")
    assert v2["model"] == v3["model"]
    assert v2["token_budgets"] == v3["token_budgets"]


@pytest.mark.unit
def test_config_v8_pins_explorer_v4_and_differs_from_v7_only_there() -> None:
    """T7.35 follow-up (ADR-0019): config-v7 copied v6's stale
    ``explorer-v2`` label, while every run since T7.21 actually used
    ``explorer-v4`` (ADR-0019 forensics). With content pinning a stale pin
    becomes real behaviour, so v8 pins ``explorer-v4`` — and changes
    nothing else: v7 stays as committed (payloads are never rewritten)."""
    v7 = _load("config-v7-payload.json")
    v8 = _load("config-v8-payload.json")
    assert {k for k in v8 if v8[k] != v7[k]} == {"prompts"}
    assert {r for r in v8["prompts"] if v8["prompts"][r] != v7["prompts"][r]} == {"explorer"}
    resolved = resolve_prompts(v8["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    assert resolved[Role.CURATOR].version == "curator-v4"
    budgets = TokenBudgets.from_snapshot(v8["model"], v8["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v9_pins_curator_v5_and_differs_from_v8_only_there() -> None:
    """T7.38 (SMOKE-V8-K2 proposals 1/2/5 — one prompt file, one new
    payload): config-v9 = v8 with the single change
    ``prompts.curator`` → curator-v5 (the claim_type↔evidence matrix,
    the reverify rule with a concrete example, ``dependencies: []``).
    v8 stays as committed (payloads are never rewritten)."""
    v8 = _load("config-v8-payload.json")
    v9 = _load("config-v9-payload.json")
    assert {k for k in v9 if v9[k] != v8[k]} == {"prompts"}
    assert {r for r in v9["prompts"] if v9["prompts"][r] != v8["prompts"][r]} == {"curator"}
    assert v9["prompts"]["curator"]["version"] == "curator-v5"
    assert v9["prompts"]["curator"]["path"] == "prompts/curator/curator-v5.md"
    resolved = resolve_prompts(v9["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v5"
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    budgets = TokenBudgets.from_snapshot(v9["model"], v9["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v10_pins_curator_v6_and_differs_from_v9_only_there() -> None:
    """T7.39 (T7.38 acceptance: the reverify example used a REAL claim
    id from SMOKE-V8-K2 — on a fresh DB the host would reject the
    copied reference and kill the whole proposal): config-v10 = v9
    with the single change ``prompts.curator`` → curator-v6 (the
    example swapped for a corpus-free fact + a fresh UUID; the matrix
    is unchanged). v9 stays as committed (payloads are never
    rewritten)."""
    v9 = _load("config-v9-payload.json")
    v10 = _load("config-v10-payload.json")
    assert {k for k in v10 if v10[k] != v9[k]} == {"prompts"}
    assert {r for r in v10["prompts"] if v10["prompts"][r] != v9["prompts"][r]} == {"curator"}
    assert v10["prompts"]["curator"]["version"] == "curator-v6"
    assert v10["prompts"]["curator"]["path"] == "prompts/curator/curator-v6.md"
    resolved = resolve_prompts(v10["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v6"
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    budgets = TokenBudgets.from_snapshot(v10["model"], v10["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v11_pins_curator_v7_and_differs_from_v10_only_there() -> None:
    """T7.43 (SMOKE-V10B-K2 proposals 1/2 — mandatory evidence binding
    + rule 7 negative example/weak-claim case): config-v11 = v10 with
    the single change ``prompts.curator`` → curator-v7. v10 stays as
    committed (payloads are never rewritten)."""
    v10 = _load("config-v10-payload.json")
    v11 = _load("config-v11-payload.json")
    assert {k for k in v11 if v11[k] != v10[k]} == {"prompts"}
    assert {r for r in v11["prompts"] if v11["prompts"][r] != v10["prompts"][r]} == {"curator"}
    assert v11["prompts"]["curator"]["version"] == "curator-v7"
    assert v11["prompts"]["curator"]["path"] == "prompts/curator/curator-v7.md"
    resolved = resolve_prompts(v11["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v7"
    assert resolved[Role.EXPLORER].version == "explorer-v4"
    budgets = TokenBudgets.from_snapshot(v11["model"], v11["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v12_pins_explorer_v5_and_differs_from_v11_only_there() -> None:
    """T7.50 (ADR-0022 option A — the class-D fix at the source: the
    model wrote free text into ``decision.reason`` in 15 of 20 partial
    smoke sessions): config-v12 = v11 with the single change
    ``prompts.explorer`` → explorer-v5 (``reason`` is exactly one token
    from the closed CompleteReason list; the explanation goes to
    ``public_rationale``; rule 5 maps stopping conditions to tokens;
    a correct/incorrect example pair). v11 stays as committed
    (payloads are never rewritten)."""
    v11 = _load("config-v11-payload.json")
    v12 = _load("config-v12-payload.json")
    assert {k for k in v12 if v12[k] != v11[k]} == {"prompts"}
    assert {r for r in v12["prompts"] if v12["prompts"][r] != v11["prompts"][r]} == {"explorer"}
    assert v12["prompts"]["explorer"]["version"] == "explorer-v5"
    assert v12["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v5.md"
    resolved = resolve_prompts(v12["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v5"
    assert resolved[Role.CURATOR].version == "curator-v7"
    budgets = TokenBudgets.from_snapshot(v12["model"], v12["token_budgets"])
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v12_bytes_and_hashes_are_untouched() -> None:
    """T7.59(в): config-v13 was ADDED; config-v12 is not rewritten and keeps the hashes it has in
    the smoke-series records (SMOKE-V14/V14B ran on this payload). The file is byte-stable under the
    serialization all frozen payloads use (indent=2, sorted keys, trailing newline)."""
    import hashlib

    from packages.domain.config import canonical_sha256

    raw = (REPO_ROOT / "docs" / "eval" / "config-v12-payload.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n" == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "493970d8ac845e3a2ca559180314592b7becdfbac404d1d530bfa85b6364057e"
    )
    assert canonical_sha256(payload) == "c23005bdec51bd9182c9000cd2d59a1a33bdbd311229cc52df58d608f3447db0"


@pytest.mark.unit
def test_config_v13_differs_from_v12_only_in_the_context_window_pair() -> None:
    """T7.59(в): the dev stand runs EXL3 (qwen38-exl3-3bpw-128k) with a 131072-token window, while
    config-v12 advertises 262144 — the node therefore plans context it cannot get from this engine.
    config-v13 = config-v12 with the single change ``model.context_window`` and its mirror
    ``model.backend_context_limit`` → 131072. Everything else (prompts and their pins, sampling,
    structured output, token_budgets Σ=26624, wake_schedule, policy, thresholds, session/activation
    limits) is byte-identical to v12: the switch changes only how much context fits the engine, so
    sessions stay comparable with the v12 smoke series.

    Budget recompute on the same rule as v12 (ContextBuilder / TokenBudgets):
    input_budget = min(context_window, backend_context_limit) − max_output_tokens − safety_margin
    = 131072 − 8192 − 2048 = 120832 ≥ Σ section limits 26624 — the payload is STARTABLE, which is
    exactly the fail-closed check `_validate_payload_budgets` applies before publishing (§5.4.1)."""
    v12 = _load("config-v12-payload.json")
    v13 = _load("config-v13-payload.json")

    assert {k for k in v13 if v13[k] != v12[k]} == {"model"}
    model13, model12 = v13["model"], v12["model"]
    assert {k for k in model13 if model13[k] != model12[k]} == {"context_window", "backend_context_limit"}
    assert model13["context_window"] == 131072 and model13["backend_context_limit"] == 131072
    # unchanged parts of the same section: output budget, sampling, structured output, safety margin
    for key in (
        "max_output_tokens",
        "safety_margin_tokens",
        "model_alias",
        "provider",
        "sampling",
        "structured_output",
    ):
        assert model13[key] == model12[key], key

    # prompts and their pins are untouched (no new prompt version was introduced)
    assert v13["prompts"] == v12["prompts"]
    resolved = resolve_prompts(v13["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v5"
    assert resolved[Role.CURATOR].version == "curator-v7"

    budgets13 = TokenBudgets.from_snapshot(model13, v13["token_budgets"])
    budgets12 = TokenBudgets.from_snapshot(model12, v12["token_budgets"])
    assert budgets13.section_limits == budgets12.section_limits == EVAL2_SECTIONS
    assert sum(budgets13.section_limits.values()) == 26624
    assert budgets13.input_budget == 131072 - model13["max_output_tokens"] - model13["safety_margin_tokens"] == 120832
    assert budgets13.validate() == []
    assert sum(budgets13.section_limits.values()) <= budgets13.input_budget

    # everything else in the payload is byte-identical to v12
    for section in ("wake_schedule", "policy", "session_limits", "activation_limits", "claim_type_rules",
                    "curiosity", "embeddings", "extraction", "planning", "reassessment_admission",
                    "repair_admission", "repetition", "research_proxy", "schema_version", "verification",
                    "token_budgets"):
        assert v13[section] == v12[section], section


@pytest.mark.unit
def test_config_v13_file_is_byte_stable_and_pinned() -> None:
    """The new payload keeps the frozen-payload file form (indent=2, sorted keys, trailing newline)
    and its identity is pinned so a later edit cannot pass as the same config."""
    import hashlib

    from packages.domain.config import canonical_sha256

    raw = (REPO_ROOT / "docs" / "eval" / "config-v13-payload.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n" == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "fe931c15a34fe71e670c29a2aacc6e63ba3defd54b5db88923201f6342e1a0ed"
    )
    assert canonical_sha256(payload) == "0260fcd2f79035e634d49fe8304e44a3784b63dc0a81566687cbe52aa7f94ce0"

@pytest.mark.unit
def test_config_v14_differs_from_v13_only_by_the_search_grant_and_the_explorer_pin() -> None:
    """T7.71 (ADR-0027 §4): config-v14 = config-v13 с двумя осознанными правками —
    ``policy.capabilities.tools`` (+``web.search``, −несуществующего в реестре ``artifact.create``,
    из-за которого шаги падали «unknown tool: artifact.create») и пин ``prompts.explorer`` →
    explorer-v6 (v5 утверждает «сети нет», что на curated-стенде неправда). Всё остальное — байт в
    байт v13: пороги, бюджеты, sampling, wake_schedule, research_proxy. v13 остаётся как закоммичен
    (payload'ы не переписываются никогда)."""
    v13 = _load("config-v13-payload.json")
    v14 = _load("config-v14-payload.json")

    assert {k for k in v14 if v14[k] != v13[k]} == {"policy", "prompts"}

    tools13 = v13["policy"]["capabilities"]["tools"]
    tools14 = v14["policy"]["capabilities"]["tools"]
    assert set(tools14) - set(tools13) == {"web.search"}
    assert set(tools13) - set(tools14) == {"artifact.create"}
    # порядок внутри списка значим только как данные снапшота; проверяем содержимое и длину
    # режим и сеть не меняются: изменён только список выданных инструментов
    caps13, caps14 = v13["policy"]["capabilities"], v14["policy"]["capabilities"]
    assert {k for k in caps14 if caps14[k] != caps13[k]} == {"tools"}
    assert v14["policy"]["access_profile"] == v13["policy"]["access_profile"] == "curated"
    assert {k for k in v14["policy"] if v14["policy"][k] != v13["policy"][k]} == {"capabilities"}

    assert v14["prompts"]["explorer"] != v13["prompts"]["explorer"]
    assert {r for r in v14["prompts"] if v14["prompts"][r] != v13["prompts"][r]} == {"explorer"}
    assert v14["prompts"]["explorer"]["version"] == "explorer-v6"
    assert v14["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v6.md"
    resolved = resolve_prompts(v14["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v6"
    assert resolved[Role.CURATOR].version == "curator-v7"

    # контекст и бюджеты — те же, что у v13: поиск не меняет окно модели
    assert v14["model"] == v13["model"]
    budgets14 = TokenBudgets.from_snapshot(v14["model"], v14["token_budgets"])
    assert budgets14.section_limits == EVAL2_SECTIONS
    assert sum(budgets14.section_limits.values()) == 26624 <= budgets14.input_budget == 120832
    assert budgets14.validate() == []

    for section in ("wake_schedule", "session_limits", "activation_limits", "claim_type_rules",
                    "curiosity", "embeddings", "extraction", "planning", "reassessment_admission",
                    "repair_admission", "repetition", "research_proxy", "schema_version",
                    "verification", "token_budgets"):
        assert v14[section] == v13[section], section


@pytest.mark.unit
def test_config_v14_file_is_byte_stable_and_pinned() -> None:
    """Новый payload сохраняет форму frozen-файла (indent=2, sorted keys, trailing newline), а его
    идентичность закреплена: хеш файла и canonical-хеш (именно он попадает в
    ``config_snapshots.payload_sha256`` — AGENTS §8). Правка файла позже не пройдёт под тем же
    именем конфигурации."""
    import hashlib

    from packages.domain.config import canonical_sha256

    raw = (REPO_ROOT / "docs" / "eval" / "config-v14-payload.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n" == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "6d361dae4454a56472de58df4c4c34577d6b1ff4cc870d9b418d72943256ed89"
    )
    assert canonical_sha256(payload) == "22903be78602cf7897f0de57b99514b66c58eca83960fa05458fd341e0104df4"



@pytest.mark.unit
def test_config_v15_differs_from_v14_only_by_the_two_prompt_pins() -> None:
    """T7.73 (уточнение ADR-0018): config-v15 = config-v14 ровно с двумя правками — пин
    ``prompts.curator`` → curator-v8 (перепроверка сохраняет опорную дату и scope якоря,
    каждый использованный источник привязан) и пин ``prompts.explorer`` → explorer-v7
    (первоисточник и независимое исследование; производный пересказ — не второй источник).
    Всё остальное — байт в байт v14: пороги, правила типов, бюджеты, окна, research_proxy.
    Состав правил движка (независимость, пороги, шкала grade) при этом не менялся: ADR-0029
    описан, но не реализован. v14 остаётся как закоммичен (payload'ы не переписываются никогда)."""
    v14 = _load("config-v14-payload.json")
    v15 = _load("config-v15-payload.json")

    assert {k for k in v15 if v15[k] != v14[k]} == {"prompts"}
    assert {r for r in v15["prompts"] if v15["prompts"][r] != v14["prompts"][r]} == {"curator", "explorer"}
    changed = {
        key
        for role in ("curator", "explorer")
        for key in v15["prompts"][role]
        if v15["prompts"][role][key] != v14["prompts"][role][key]
    }
    assert changed == {"path", "sha256", "version"}, changed

    assert v15["prompts"]["curator"]["version"] == "curator-v8"
    assert v15["prompts"]["curator"]["path"] == "prompts/curator/curator-v8.md"
    assert v15["prompts"]["explorer"]["version"] == "explorer-v7"
    assert v15["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v7.md"
    # не тронутые роли закреплены прежними пинами
    for role in ("extractor", "planner", "verifier"):
        assert v15["prompts"][role] == v14["prompts"][role], role

    resolved = resolve_prompts(v15["prompts"], REPO_ROOT)
    assert resolved[Role.CURATOR].version == "curator-v8"
    assert resolved[Role.EXPLORER].version == "explorer-v7"

    # правила оценки, независимости и пороги — те же, что у v14 (стопап задачи: не менять)
    assert set(v15) == set(v14), "у payload'а появился новый раздел"
    untouched = sorted(k for k in v14 if k != "prompts")
    for section in untouched:
        assert v15[section] == v14[section], section

    budgets = TokenBudgets.from_snapshot(v15["model"], v15["token_budgets"])
    assert budgets.section_limits == EVAL2_SECTIONS
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v15_file_is_byte_stable_and_pinned() -> None:
    """Идентичность v15 закреплена: хеш файла и canonical-хеш (последний попадает в
    ``config_snapshots.payload_sha256`` — AGENTS §8, путать их нельзя)."""
    import hashlib

    from packages.domain.config import canonical_sha256

    raw = (REPO_ROOT / "docs" / "eval" / "config-v15-payload.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n" == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "c65b69db5a1f50d44e6dc9e9c4b6399381c191b1df62506a9ef275a6d3b5f722"
    )
    assert canonical_sha256(payload) == "b380181298310e6d1e1904ac5f062b0d1c80543fafe11c6482b7ce9ba05d6a73"


@pytest.mark.unit
def test_config_v14_file_is_untouched_by_the_v15_activation() -> None:
    """Новый номер конфигурации = новый файл (AGENTS §8): v14 не переписан задним числом и
    остаётся штатным откатом стенда."""
    import hashlib

    raw = (REPO_ROOT / "docs" / "eval" / "config-v14-payload.json").read_text(encoding="utf-8")
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "6d361dae4454a56472de58df4c4c34577d6b1ff4cc870d9b418d72943256ed89"
    )


@pytest.mark.unit
def test_config_v16_differs_from_v15_only_by_the_explorer_pin_and_the_step_limit() -> None:
    """T7.76: config-v16 = config-v15 ровно с двумя правками — пин ``prompts.explorer`` → explorer-v8
    (спорное число проверяется двумя сторонами поиска: первоисточник и отдельный запрос про независимую
    оценку; расхождение называется открыто, а его отсутствие — честно) и
    ``session_limits.max_explorer_steps`` 10 → 16. Пороги, ``claim_type_rules``, token-бюджеты, окна
    модели, research_proxy и список инструментов остаются байт в байтом v15: двусторонний поиск меняет
    то, что модель ищет, и длину сессии, но не то, как хост считает независимость. Композиция правил
    движка (ADR-0029 реализована в T7.75) здесь не меняется. v15 остаётся закоммичен и является откатом."""
    v15 = _load("config-v15-payload.json")
    v16 = _load("config-v16-payload.json")

    assert set(v16) == set(v15), "у payload'а появился новый раздел"
    assert {k for k in v16 if v16[k] != v15[k]} == {"prompts", "session_limits"}

    # промпты: изменился ровно один пин, ровно своими тремя полями
    assert {r for r in v16["prompts"] if v16["prompts"][r] != v15["prompts"][r]} == {"explorer"}
    changed = {k for k in v16["prompts"]["explorer"] if v16["prompts"]["explorer"][k] != v15["prompts"]["explorer"][k]}
    assert changed == {"path", "sha256", "version"}, changed
    assert v16["prompts"]["explorer"]["version"] == "explorer-v8"
    assert v16["prompts"]["explorer"]["path"] == "prompts/explorer/explorer-v8.md"
    for role in ("curator", "extractor", "planner", "verifier"):
        assert v16["prompts"][role] == v15["prompts"][role], role

    # лимиты: изменился ровно один, и только в большую сторону
    assert {k for k in v16["session_limits"] if v16["session_limits"][k] != v15["session_limits"][k]} == {
        "max_explorer_steps"
    }
    assert v16["session_limits"]["max_explorer_steps"] == 16
    assert v16["session_limits"]["max_explorer_steps"] > v15["session_limits"]["max_explorer_steps"]
    for key in ("session_timeout_seconds", "phase_deadline_seconds"):
        assert v16["session_limits"][key] == v15["session_limits"][key], key

    untouched = sorted(k for k in v15 if k not in {"prompts", "session_limits"})
    for section in untouched:
        assert v16[section] == v15[section], section

    resolved = resolve_prompts(v16["prompts"], REPO_ROOT)
    assert resolved[Role.EXPLORER].version == "explorer-v8"
    assert resolved[Role.CURATOR].version == "curator-v8"

    budgets = TokenBudgets.from_snapshot(v16["model"], v16["token_budgets"])
    assert budgets.section_limits == EVAL2_SECTIONS
    assert budgets.validate() == []


@pytest.mark.unit
def test_config_v16_step_limit_fits_the_session_and_search_ceilings() -> None:
    """Арифметика нового лимита (без замеров в тесте, только по числам снапшота и замерам STATUS T7.76):
    16 шагов ≈ 16×57 с = 912 с исследования + куратор ≤180 с укладываются в `session_timeout_seconds` и
    `phase_deadline_seconds` (1800 с, не изменены); максимум upstream-запросов поиска за сессию — меньше
    лимита шагов, а `rate_limit_max` = 20 на окно 3600 с не менялся. Если эти числа разъедутся, тест
    краснеет до того, как сессия упрётся в дедлайн посреди независимой стороны проверки."""
    v16 = _load("config-v16-payload.json")
    steps = v16["session_limits"]["max_explorer_steps"]
    assert steps * 60 <= v16["session_limits"]["phase_deadline_seconds"] - 180
    assert steps <= v16["research_proxy"]["rate_limit_max"], "поисковых шагов больше, чем потолок upstream"


@pytest.mark.unit
def test_config_v16_file_is_byte_stable_and_pinned() -> None:
    """Идентичность v16 закреплена: хеш файла и canonical-хеш (последний попадает в
    ``config_snapshots.payload_sha256`` — AGENTS §8, путать их нельзя)."""
    import hashlib

    from packages.domain.config import canonical_sha256

    raw = (REPO_ROOT / "docs" / "eval" / "config-v16-payload.json").read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n" == raw
    assert hashlib.sha256(raw.encode()).hexdigest() == (
        "79d63b2d380775621495ad2445ce3610484c8ce5fd3c5f857f65f26a7817d131"
    )
    assert canonical_sha256(payload) == "740ae9a1022b00ef98b4eb09563ac4645b0047ebd419aba2fe853ee1a31f31b3"


@pytest.mark.unit
def test_earlier_payloads_are_untouched_by_the_v16_activation() -> None:
    """Новый номер конфигурации = новый файл (AGENTS §8): v15 и v14 не переписаны задним числом, v15
    остаётся штатным откатом стенда."""
    import hashlib

    for name, digest in {
        "config-v15-payload.json": "c65b69db5a1f50d44e6dc9e9c4b6399381c191b1df62506a9ef275a6d3b5f722",
        "config-v14-payload.json": "6d361dae4454a56472de58df4c4c34577d6b1ff4cc870d9b418d72943256ed89",
    }.items():
        raw = (REPO_ROOT / "docs" / "eval" / name).read_text(encoding="utf-8")
        assert hashlib.sha256(raw.encode()).hexdigest() == digest, name
