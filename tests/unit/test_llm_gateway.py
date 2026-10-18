"""Tests for the LLM Gateway (T1.7-T1.9) against the fake LLM.

T7.23 (ADR-0012) additions: the engine compatibility profile (default
= byte-for-byte the current schema; "halogen" strips the keywords that
engine refuses), host-side validation staying FULL even when the sent
schema is stripped, and the engine's refusal of the request itself
(HTTP 4xx) being a distinct, non-transient, non-retried error.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID

import pytest

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import DecisionKind
from packages.domain.schemas.decision import ModelResponse
from packages.domain.schemas.staging import CuratorProposal
from packages.llm_gateway.client import (
    LLMError,
    LLMMiddleware,
    LLMRequestRejectedError,
    LLMSchemaError,
    LLMTransientError,
)
from packages.llm_gateway.config import LLMGatewayConfig, ModelProfile
from packages.llm_gateway.fingerprint import build_model_fingerprint
from packages.llm_gateway.schema_compat import HALOGEN_UNSUPPORTED_KEYWORDS, strip_schema_keywords
from tests.conftest import FakeLLM

TOOL_RESPONSE: JsonDict = {
    "public_rationale": "Проверить источник",
    "expected_information": "Первичный документ",
    "decision": {"kind": "tool", "tool": "web.search", "arguments": {"query": "spec"}},
}
COMPLETE_RESPONSE: JsonDict = {
    "public_rationale": "Цель достигнута",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}


def _gateway(fake: FakeLLM, **overrides: object) -> LLMMiddleware:
    params: dict[str, object] = {
        "base_url": fake.base_url,
        "model": "fake-thinker",
        "max_retries": 3,
        "retry_base_delay": 0.01,
    }
    params.update(overrides)  # type: ignore[arg-type]
    return LLMMiddleware(LLMGatewayConfig(**params))  # type: ignore[arg-type]


@pytest.mark.unit
async def test_chat_returns_validated_model(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        model, record = await gateway.chat(
            system="sys", user="user", response_schema=ModelResponse
        )
    finally:
        await gateway.close()

    assert isinstance(model, ModelResponse)
    assert model.decision.kind is DecisionKind.TOOL
    assert model.decision.tool == "web.search"
    assert record.output_schema_valid is True
    assert record.attempts == 1
    assert record.input_tokens == 10
    assert record.output_tokens == 20
    assert record.latency_ms >= 0
    assert record.finish_reason == "stop"


@pytest.mark.unit
async def test_transient_error_is_retried(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 503}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        _model, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert record.attempts == 2
    assert record.output_schema_valid is True


@pytest.mark.unit
async def test_transient_exhausted_raises(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 500}, {"error": 502}, {"error": 503}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMTransientError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 3


@pytest.mark.unit
async def test_permanent_error_not_retried(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 400}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert "400" in str(excinfo.value)
    assert fake_llm.state()["request_count"] == 1  # no retry


@pytest.mark.unit
async def test_schema_error_recovered_on_retry(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": "invalid_json"}, {"content": TOOL_RESPONSE}])
    gateway = _gateway(fake_llm)
    try:
        model, record = await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()
    assert record.attempts == 2
    assert record.output_schema_valid is True
    assert isinstance(model, ModelResponse)


@pytest.mark.unit
async def test_schema_error_exhausted_raises(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": "invalid_json"}] * 3)
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMSchemaError):
            await gateway.chat(system="s", user="u", response_schema=ModelResponse)
    finally:
        await gateway.close()


@pytest.mark.unit
def test_fingerprint_is_deterministic_and_versioned() -> None:
    profile = ModelProfile(model_alias="thinker-local", artifact_sha256="a" * 64)
    fp1 = build_model_fingerprint(profile, prompt_version="explorer-v1", tool_schema_hash="t")
    fp2 = build_model_fingerprint(profile, prompt_version="explorer-v1", tool_schema_hash="t")
    fp3 = build_model_fingerprint(profile, prompt_version="explorer-v2", tool_schema_hash="t")
    assert fp1 == fp2
    assert fp1["fingerprint_sha256"] == fp2["fingerprint_sha256"]
    assert fp1["fingerprint_sha256"] != fp3["fingerprint_sha256"]
    assert fp1["model"]["model_alias"] == "thinker-local"


# ── T7.23 (ADR-0012): engine JSON Schema compatibility profile ────────


def _canon(obj: object) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _kw_count(node: object, key: str) -> int:
    if isinstance(node, dict):
        return sum(1 for k in node if k == key) + sum(_kw_count(v, key) for v in node.values())
    if isinstance(node, list):
        return sum(_kw_count(v, key) for v in node)
    return 0


def _sent_response_format(fake: FakeLLM) -> JsonDict:
    requests = fake.requests()
    assert requests, "no request was recorded"
    return requests[-1]["response_format"]


VALID_CURATOR: JsonDict = {
    "summary": "Итог сессии",
    "claims": [
        {
            "statement": "Ключевая ставка ЦБ РФ 14,00% с 27.07.2026",
            "claim_type": "temporal_fact",
            "scope": {"объект": "ЦБ РФ"},
            "as_of": "2026-07-27T00:00:00Z",
            "dependencies": [
                {"claim_id": "3f2c6f4d-8b1e-4c7a-9d2f-1a2b3c4d5e6f", "kind": "evidential"}
            ],
        }
    ],
    "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
    "new_questions": [],
}


@pytest.mark.unit
async def test_default_profile_sends_schema_byte_identical(fake_llm: FakeLLM) -> None:
    """Default (schema_profile unset) = the CURRENT behavior: the schema
    the gateway sends is byte-for-byte what pydantic produces (the two
    `format`s included) — qwen36 / EVAL-3d comparability is untouched."""
    fake_llm.script([{"content": VALID_CURATOR}])
    gateway = _gateway(fake_llm)  # no schema_profile override
    try:
        assert gateway.config.schema_profile == "none"
        await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    sent = _sent_response_format(fake_llm)
    expected = {
        "type": "json_schema",
        "json_schema": {
            "name": "CuratorProposal",
            "strict": True,
            "schema": CuratorProposal.model_json_schema(),
        },
    }
    assert _canon(sent) == _canon(expected)
    # sanity: the un-stripped schema really does carry the formats
    assert _kw_count(sent["json_schema"]["schema"], "format") == 2


@pytest.mark.unit
async def test_halogen_profile_strips_only_refused_keywords(fake_llm: FakeLLM) -> None:
    """Enabled profile: exactly the keywords the engine refuses are gone
    from the sent schema; every other key is preserved."""
    fake_llm.script([{"content": VALID_CURATOR}])
    gateway = _gateway(fake_llm, schema_profile="halogen")
    try:
        await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    sent = _sent_response_format(fake_llm)["json_schema"]["schema"]
    assert _kw_count(sent, "format") == 0
    assert _kw_count(sent, "pattern") == 0
    # the sent schema is exactly the pydantic schema minus the refused
    # keywords — nothing else moved
    full = CuratorProposal.model_json_schema()
    assert _canon(sent) == _canon(strip_schema_keywords(full, HALOGEN_UNSUPPORTED_KEYWORDS))
    # the $defs structure, enums, anyOf, budgets all survive
    assert set(sent["$defs"]) == set(full["$defs"])
    assert sent["required"] == full["required"]
    assert _kw_count(sent, "anyOf") == _kw_count(full, "anyOf")


@pytest.mark.unit
async def test_halogen_profile_host_validation_stays_full(fake_llm: FakeLLM) -> None:
    """The sent schema has no `format`, yet the host still validates the
    answer against the FULL model: a well-formed uuid + date-time parse
    into the typed fields (host validation is not weakened)."""
    fake_llm.script([{"content": VALID_CURATOR}])
    gateway = _gateway(fake_llm, schema_profile="halogen")
    try:
        model, record = await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    assert record.output_schema_valid is True
    claim = model.claims[0]
    assert claim.as_of == datetime(2026, 7, 27, tzinfo=UTC)
    assert claim.dependencies[0].claim_id == UUID("3f2c6f4d-8b1e-4c7a-9d2f-1a2b3c4d5e6f")


@pytest.mark.unit
async def test_halogen_profile_rejects_bad_uuid_despite_stripped_schema(fake_llm: FakeLLM) -> None:
    """The engine no longer enforces `format: uuid`, so a bad uuid can
    reach the host — the host must still reject it (full validation)."""
    bad = {**VALID_CURATOR, "claims": [
        {**VALID_CURATOR["claims"][0],
         "dependencies": [{"claim_id": "not-a-uuid", "kind": "evidential"}]}
    ]}
    fake_llm.script([{"content": bad}] * 3)
    gateway = _gateway(fake_llm, schema_profile="halogen")
    try:
        with pytest.raises(LLMSchemaError):
            await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 3  # retried, then surfaced


@pytest.mark.unit
async def test_engine_refusal_is_request_rejected_not_transient(fake_llm: FakeLLM) -> None:
    """HTTP 400 = the engine refused the request itself: a distinct
    non-transient error, NOT retried, NOT an 'unavailable' error."""
    fake_llm.script([{"error": 400}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMRequestRejectedError) as excinfo:
            await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    assert "400" in str(excinfo.value)
    assert not isinstance(excinfo.value, LLMTransientError)
    assert isinstance(excinfo.value, LLMError)  # still an LLMError
    assert fake_llm.state()["request_count"] == 1  # never retried


@pytest.mark.unit
async def test_404_is_request_rejected_like_400(fake_llm: FakeLLM) -> None:
    """Any non-transient 4xx (unknown model, bad auth) is a request
    refusal, distinct from a down engine."""
    fake_llm.script([{"error": 404}])
    gateway = _gateway(fake_llm)
    try:
        with pytest.raises(LLMRequestRejectedError):
            await gateway.chat(system="s", user="u", response_schema=CuratorProposal)
    finally:
        await gateway.close()
    assert fake_llm.state()["request_count"] == 1
