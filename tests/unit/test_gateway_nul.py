"""T7.47a — the model-text entry boundary: NUL in the LLM response is
masked at the gateway, right after parsing, BEFORE schema validation.

ADR-0020 open question (T7.46a): model-generated text (claim statement,
question text, complete reason, plan/verifier/extraction fields) could
carry \\x00 into JSONB columns (session_staging, sessions.plan, ...) and
roll back the whole phase-1 transaction — bypassing the three T7.46a
boundaries, which cover tool observations and the audit write only.

The fix masks at the single point where model text enters the host
(``LLMMiddleware.chat``), so every downstream consumer (staging, plan,
verification, extraction, complete reason, audit) sees the masked value
and any hash over it is computed over the same value.
"""

from __future__ import annotations

from typing import Any

import pytest

from packages.domain.sanitization import NUL_MARKER
from packages.domain.schemas.decision import ModelResponse
from packages.domain.schemas.staging import CuratorProposal
from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.config import LLMGatewayConfig
from tests.conftest import FakeLLM


def _gateway(fake: FakeLLM) -> LLMMiddleware:
    return LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=1, retry_base_delay=0.01)
    )


def _curator_content(**overrides: Any) -> dict[str, Any]:
    content: dict[str, Any] = {
        "summary": "Одно утверждение",
        "claims": [
            {
                "statement": "6*7 равно 42",
                "claim_type": "computed_result",
                "scope": {"expr": "6*7"},
            }
        ],
        "evidence_links": [{"evidence_index": 0, "claim_index": 0, "relation": "supports"}],
        "new_questions": [],
    }
    content.update(overrides)
    return content


@pytest.mark.unit
async def test_nul_in_claim_statement_is_masked_before_validation(fake_llm: FakeLLM) -> None:
    fake_llm.script(
        [
            {
                "content": _curator_content(
                    claims=[
                        {
                            "statement": "6*7 рав\x00но 42",
                            "claim_type": "computed_result",
                            "scope": {"expr": "6\x00*7"},
                        }
                    ]
                )
            }
        ]
    )
    gateway = _gateway(fake_llm)
    try:
        model, record = await gateway.chat(
            system="s", user="u", response_schema=CuratorProposal
        )
    finally:
        await gateway.close()

    assert record.output_schema_valid is True
    claim = model.claims[0]
    # the visible marker replaces the NUL byte — the model text never
    # reaches the host with a raw \x00
    assert claim.statement == f"6*7 рав{NUL_MARKER}но 42"
    assert "\x00" not in claim.statement
    assert claim.scope == {"expr": f"6{NUL_MARKER}*7"}
    assert model.summary == "Одно утверждение"  # clean fields untouched


@pytest.mark.unit
async def test_nul_in_complete_reason_and_rationale_is_masked(fake_llm: FakeLLM) -> None:
    fake_llm.script(
        [
            {
                "content": {
                    "public_rationale": "Вопрос отвечен\x00",
                    "expected_information": "Результат 6*7",
                    "decision": {"kind": "complete", "reason": "goal_reached\x00"},
                }
            }
        ]
    )
    gateway = _gateway(fake_llm)
    try:
        model, _record = await gateway.chat(
            system="s", user="u", response_schema=ModelResponse
        )
    finally:
        await gateway.close()

    assert model.public_rationale == f"Вопрос отвечен{NUL_MARKER}"
    assert model.decision.reason == f"goal_reached{NUL_MARKER}"
    assert "\x00" not in model.public_rationale
    assert model.decision.reason is not None and "\x00" not in model.decision.reason


@pytest.mark.unit
async def test_clean_response_passes_byte_identical(fake_llm: FakeLLM) -> None:
    """A NUL-free response must come out unchanged: masking is the
    identity on clean input (T7.46a invariant — clean input gives the
    byte-identical value, hashes and dedup keys unchanged)."""
    content = _curator_content(
        new_questions=[{"text": "Почему 42?", "origin": "previous_result"}]
    )
    fake_llm.script([{"content": content}])
    gateway = _gateway(fake_llm)
    try:
        model, _record = await gateway.chat(
            system="s", user="u", response_schema=CuratorProposal
        )
    finally:
        await gateway.close()

    # every provided value passes through unchanged (mask = identity on
    # clean input) — the stored value and any dedup/hash key over it is
    # byte-identical to the model's own text
    assert model.summary == content["summary"]
    assert model.claims[0].statement == content["claims"][0]["statement"]
    assert model.claims[0].claim_type == content["claims"][0]["claim_type"]
    assert model.claims[0].scope == content["claims"][0]["scope"]
    assert model.new_questions[0].text == content["new_questions"][0]["text"]
    assert model.evidence_links[0].evidence_index == 0
    assert model.evidence_links[0].relation.value == content["evidence_links"][0]["relation"]
