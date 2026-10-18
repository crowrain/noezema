"""Unit: the output limit is a separate outcome class, and reasoning can be
switched off per call phase (T7.80, ADR-0030).

The stand fact this file reproduces (session 1d0886fa on .92 with halogen on
.141): three curator attempts in a row returned EXACTLY max_output_tokens
completion tokens, the JSON document ended mid-string, and the gateway repeated
the SAME request — reasoning included — until the retry budget ran out. Here the
same input must produce: one retry WITHOUT reasoning (only when the engine
profile can actually disable it), then an honest "truncated by the output limit"
error that carries every failed attempt.
"""

from __future__ import annotations

import pytest

from packages.domain.models.base import JsonDict
from packages.domain.schemas.decision import ModelResponse
from packages.llm_gateway.client import (
    LLMMiddleware,
    LLMSchemaError,
    LLMTruncatedResponseError,
)
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.reasoning_compat import MODE_OFF, MODE_ON, PHASE_CONSOLIDATION
from tests.conftest import FakeLLM

TOOL_RESPONSE: JsonDict = {
    "public_rationale": "Проверить источник",
    "expected_information": "Первичный документ",
    "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": "spec"}},
}

#: a cut-off answer: exactly what an engine returns when it stops at max_tokens
TRUNCATED_ANSWER = '{"public_rationale": "Итог по ставке: ключевая ставка ЦБ РФ остаётся 14,00'


def _gateway(fake: FakeLLM, **overrides: object) -> LLMMiddleware:
    params: dict[str, object] = {
        "base_url": fake.base_url,
        "model": "fake-thinker",
        "max_retries": 3,
        "retry_base_delay": 0.01,
    }
    params.update(overrides)  # type: ignore[arg-type]
    return LLMMiddleware(LLMGatewayConfig(**params))  # type: ignore[arg-type]


def _params(fake: FakeLLM, index: int = -1) -> JsonDict:
    requests = fake.requests()
    needed = -index if index < 0 else index + 1
    assert len(requests) >= needed, f"expected at least {needed} request(s), got {len(requests)}"
    return dict(requests[index]["params"])


def _truncated_script(count: int, *, then_valid: bool = False) -> list[dict[str, object]]:
    """finish_reason=length with completion tokens exactly at the requested cap —
    the measured halogen signature (max 8192 -> completion_tokens 8192).
    `then_valid` appends one well-formed answer: what a reasoning-off retry gets."""
    script: list[dict[str, object]] = [
        {"raw_message": TRUNCATED_ANSWER, "finish_reason": "length"} for _ in range(count)
    ]
    if then_valid:
        script.append({"content": TOOL_RESPONSE})
    return script


# ── request body per (profile, phase mode) ─────────────────────────────


@pytest.mark.unit
async def test_default_profile_and_no_phase_send_the_pre_t780_body(fake_llm: FakeLLM) -> None:
    """No profile, no phase: the body is what the gateway sent before this
    feature — no reasoning parameter appears anywhere."""
    fake_llm.script([{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    params = _params(fake_llm)
    # the fake server logs every body parameter except messages/response_format
    assert set(params) == {"model", "max_tokens"}
    assert "reasoning_effort" not in params
    assert "chat_template_kwargs" not in params
    assert params["max_tokens"] == 4096


@pytest.mark.unit
async def test_mode_on_adds_nothing_and_body_matches_baseline(fake_llm: FakeLLM) -> None:
    """phase + mode "on" = the engine reasons as configured: identical body."""
    fake_llm.script([{"content": TOOL_RESPONSE}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm, reasoning_profile="halogen")
    try:
        await gateway.chat(system="s", user="u", response_schema=ModelResponse)
        await gateway.chat(
            system="s", user="u", response_schema=ModelResponse,
            phase=PHASE_CONSOLIDATION, reasoning_mode=MODE_ON,
        )
    finally:
        await gateway.close()
    assert _params(fake_llm, 0) == _params(fake_llm, 1)


@pytest.mark.unit
async def test_halogen_off_adds_reasoning_effort_none_only(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"content": TOOL_RESPONSE}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm, reasoning_profile="halogen")
    try:
        await gateway.chat(system="s", user="u", response_schema=ModelResponse)
        await gateway.chat(
            system="s", user="u", response_schema=ModelResponse,
            phase=PHASE_CONSOLIDATION, reasoning_mode=MODE_OFF,
        )
    finally:
        await gateway.close()
    baseline = _params(fake_llm, 0)
    off = _params(fake_llm, 1)
    assert off["reasoning_effort"] == "none"
    # nothing else moved: same model, same cap, same structured-output request
    assert {k: v for k, v in off.items() if k != "reasoning_effort"} == baseline


@pytest.mark.unit
async def test_chat_template_off_adds_enable_thinking_false(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm, reasoning_profile="chat-template")
    try:
        await gateway.chat(
            system="s", user="u", response_schema=ModelResponse,
            phase=PHASE_CONSOLIDATION, reasoning_mode=MODE_OFF,
        )
    finally:
        await gateway.close()
    assert _params(fake_llm)["chat_template_kwargs"] == {"enable_thinking": False}
    assert "reasoning_effort" not in _params(fake_llm)


@pytest.mark.unit
async def test_profile_none_cannot_invent_a_switch(fake_llm: FakeLLM) -> None:
    """mode "off" on a deployment without a profile: nothing is added (the
    engine would ignore it), the request stays as it is today."""
    fake_llm.script([{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)  # reasoning_profile defaults to "none"
    try:
        await gateway.chat(
            system="s", user="u", response_schema=ModelResponse,
            phase=PHASE_CONSOLIDATION, reasoning_mode=MODE_OFF,
        )
    finally:
        await gateway.close()
    assert "reasoning_effort" not in _params(fake_llm)
    assert "chat_template_kwargs" not in _params(fake_llm)


# ── truncation: a separate outcome class ───────────────────────────────


@pytest.mark.unit
async def test_length_stop_is_not_a_schema_hiccup_without_a_profile(fake_llm: FakeLLM) -> None:
    """Profile "none" (no way to disable reasoning): ONE request, then an honest
    truncation error — the same request is not repeated hoping for luck."""
    fake_llm.script(_truncated_script(3))
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMTruncatedResponseError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 1
    attempts = excinfo.value.attempts
    assert len(attempts) == 1
    assert attempts[0].finish_reason == "length"
    assert attempts[0].output_tokens == 4096  # the cap, as measured on .141
    assert attempts[0].output_schema_valid is False
    assert attempts[0].truncated is True
    assert "truncat" in str(excinfo.value).lower()


@pytest.mark.unit
async def test_capped_answer_reported_as_stop_is_truncation_too(fake_llm: FakeLLM) -> None:
    """The measured halogen case cannot be caught by finish_reason alone: an
    answer at the cap is a cut answer even when the engine calls it "stop"."""
    fake_llm.script(
        [
            {
                "raw_message": TRUNCATED_ANSWER,
                "finish_reason": "stop",
                "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
            }
        ]
    )
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMTruncatedResponseError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 1


@pytest.mark.unit
async def test_complete_json_cut_by_the_limit_is_still_refused(fake_llm: FakeLLM) -> None:
    """Fail-closed honesty: a JSON document that happens to PARSE but was
    stopped by the output limit may have lost a value mid-string — it is not
    accepted as an answer."""
    fake_llm.script([{"content": TOOL_RESPONSE, "finish_reason": "length"}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMTruncatedResponseError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 1


@pytest.mark.unit
async def test_truncation_retry_disables_reasoning_once(fake_llm: FakeLLM) -> None:
    """The fix itself: the second request is a DIFFERENT request — reasoning off —
    and it recovers the answer."""
    fake_llm.script(_truncated_script(1, then_valid=True))
    gateway = _gateway(fake_llm, reasoning_profile="halogen")
    try:
        model, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert isinstance(model, ModelResponse)
    requests = fake_llm.requests()
    assert len(requests) == 2
    assert "reasoning_effort" not in requests[0]["params"]
    assert requests[1]["params"]["reasoning_effort"] == "none"
    assert record.truncated is True
    assert record.output_schema_valid is True
    detail = record.attempts_detail
    assert [a.output_schema_valid for a in detail] == [False, True]
    assert [a.truncated for a in detail] == [True, False]
    assert detail[1].reasoning_mode == MODE_OFF


@pytest.mark.unit
async def test_truncation_is_retried_once_not_three_times(fake_llm: FakeLLM) -> None:
    """Budget max_retries=3 does not mean "keep repeating the same doomed
    request": after ONE reasoning-off attempt the truncation is reported."""
    fake_llm.script(_truncated_script(5))
    gateway = _gateway(fake_llm, reasoning_profile="halogen")
    try:
        with pytest.raises(LLMTruncatedResponseError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    requests = fake_llm.requests()
    assert len(requests) == 2, "one ordinary attempt + exactly one reasoning-off attempt"
    assert "reasoning_effort" not in requests[0]["params"]
    assert requests[1]["params"]["reasoning_effort"] == "none"
    attempts = excinfo.value.attempts
    assert len(attempts) == 2
    assert all(a.truncated and not a.output_schema_valid for a in attempts)
    assert [a.reasoning_mode for a in attempts] == [None, MODE_OFF]


@pytest.mark.unit
async def test_phase_already_off_does_not_retry_the_same_request(fake_llm: FakeLLM) -> None:
    """If the policy already disabled reasoning, a "disable reasoning" retry
    would be the SAME request — report truncation immediately instead."""
    fake_llm.script(_truncated_script(3))
    gateway = _gateway(fake_llm, reasoning_profile="halogen")
    try:
        with pytest.raises(LLMTruncatedResponseError):
            await gateway.chat(
                system="s", user="u", response_schema=ModelResponse,
                phase=PHASE_CONSOLIDATION, reasoning_mode=MODE_OFF,
            )
    finally:
        await gateway.close()
    requests = fake_llm.requests()
    assert len(requests) == 1
    assert requests[0]["params"]["reasoning_effort"] == "none"


@pytest.mark.unit
async def test_chat_template_truncation_retry_uses_the_template_switch(fake_llm: FakeLLM) -> None:
    fake_llm.script(_truncated_script(1, then_valid=True))
    gateway = _gateway(fake_llm, reasoning_profile="chat-template")
    try:
        await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    requests = fake_llm.requests()
    assert len(requests) == 2
    assert "chat_template_kwargs" not in requests[0]["params"]
    assert requests[1]["params"]["chat_template_kwargs"] == {"enable_thinking": False}


@pytest.mark.unit
async def test_ordinary_schema_hiccup_keeps_the_old_retry_policy(fake_llm: FakeLLM) -> None:
    """Not a regression: an answer that is merely invalid JSON (well below the
    cap) is still retried with the same body up to max_retries."""
    fake_llm.script([{"error": "invalid_json"}] * 2 + [{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        _, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert record.attempts == 3
    assert record.truncated is False
    assert len(record.attempts_detail) == 3


@pytest.mark.unit
async def test_exhausted_schema_retries_carry_every_failed_attempt(fake_llm: FakeLLM) -> None:
    """The orchestrator must be able to write the failed attempts into
    model_runs: the error carries them."""
    fake_llm.script([{"error": "invalid_json"}] * 3)
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMSchemaError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    attempts = excinfo.value.attempts
    assert len(attempts) == 3
    assert all(a.output_schema_valid is False for a in attempts)
    assert all(a.truncated is False for a in attempts)
