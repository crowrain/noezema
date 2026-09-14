"""Tests for the compat suite (T1.11) and prompt loading (T1.10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.compat import run_compat_suite
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.roles import Role, load_prompt, tool_schema_hash
from tests.conftest import FakeLLM

VALID_TOOL = {
    "public_rationale": "r",
    "decision": {"kind": "tool", "tool": "web.search", "arguments": {"q": 1}},
}
VALID_COMPLETE = {
    "public_rationale": "r",
    "decision": {"kind": "complete", "reason": "goal_reached"},
}


def _gateway(fake: FakeLLM) -> LLMMiddleware:
    return LLMMiddleware(
        LLMGatewayConfig(base_url=fake.base_url, model="fake-thinker", max_retries=2, retry_base_delay=0.01)
    )


@pytest.mark.unit
async def test_compat_suite_passes_on_compliant_endpoint(fake_llm: FakeLLM) -> None:
    fake_llm.script(
        [
            {"content": VALID_TOOL},
            {"content": VALID_TOOL},
            {"content": VALID_COMPLETE},
            {"content": VALID_TOOL},  # long_context
        ]
    )
    gateway = _gateway(fake_llm)
    try:
        report = await run_compat_suite(gateway)
    finally:
        await gateway.close()

    assert report.passed is True
    assert len(report.checks) == 4
    assert [c.name for c in report.checks] == [
        "schema_basic",
        "tool_choice",
        "complete_decision",
        "long_context",
    ]
    report_dict = report.to_dict()
    assert report_dict["passed"] is True
    assert report_dict["model"] == "fake-thinker"


@pytest.mark.unit
async def test_compat_suite_reports_schema_failure(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": "invalid_json"}] * 4)
    gateway = _gateway(fake_llm)
    try:
        report = await run_compat_suite(gateway)
    finally:
        await gateway.close()

    assert report.passed is False
    assert all(not c.passed for c in report.checks)


@pytest.mark.unit
def test_load_prompt_extracts_version(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "explorer.md").write_text(
        "version: explorer-v1\n\n# Explorer\nDo one thing per turn.\n", encoding="utf-8"
    )
    loaded = load_prompt(Role.EXPLORER, prompts_dir=prompts)
    assert loaded.version == "explorer-v1"
    assert loaded.role is Role.EXPLORER
    assert loaded.sha256 and len(loaded.sha256) == 64
    assert "Do one thing per turn" in loaded.text


@pytest.mark.unit
def test_load_prompt_without_version(tmp_path: Path) -> None:
    prompts = tmp_path / "prompts"
    prompts.mkdir()
    (prompts / "curator.md").write_text("just text", encoding="utf-8")
    loaded = load_prompt(Role.CURATOR, prompts_dir=prompts)
    assert loaded.version == "unversioned"


@pytest.mark.unit
def test_tool_schema_hash_order_insensitive() -> None:
    assert tool_schema_hash(["a.b", "c.d"]) == tool_schema_hash(["c.d", "a.b"])
    assert tool_schema_hash(["a.b"]) != tool_schema_hash(["a.c"])
