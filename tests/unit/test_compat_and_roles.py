"""Tests for the compat suite (T1.11) and prompt loading (T1.10)."""

from __future__ import annotations

from pathlib import Path

import pytest

from packages.llm_gateway.client import LLMMiddleware
from packages.llm_gateway.compat import run_compat_suite
from packages.llm_gateway.config import LLMGatewayConfig
from packages.llm_gateway.roles import (
    PromptPinError,
    Role,
    parse_prompt_version,
    resolve_prompts,
    tool_schema_hash,
)
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


def _full_tree(tmp_path: Path, override: dict[str, str] | None = None) -> dict:
    """A complete 5-role prompt tree under tmp_path + its honest pins."""
    import hashlib

    texts: dict[str, str] = {
        role.value: f"version: {role.value}-v1\n\n# {role.value}\n"
        for role in Role
    }
    texts["explorer"] = "version: explorer-v1\n\n# Explorer\nDo one thing per turn.\n"
    texts.update(override or {})
    section = {}
    for role, text in texts.items():
        rel = f"prompts/{role}/{role}-v1.md"
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        section[role] = {
            "version": parse_prompt_version(text),
            "path": rel,
            "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        }
    return section


@pytest.mark.unit
def test_resolve_prompt_extracts_version_and_content_hash(tmp_path: Path) -> None:
    import hashlib

    section = _full_tree(tmp_path)
    loaded = resolve_prompts(section, prompts_root=tmp_path)
    assert set(loaded) == set(Role)
    explorer = loaded[Role.EXPLORER]
    assert explorer.version == "explorer-v1"
    assert explorer.role is Role.EXPLORER
    assert explorer.sha256 and len(explorer.sha256) == 64
    assert explorer.sha256 == hashlib.sha256(explorer.text.encode("utf-8")).hexdigest()
    assert "Do one thing per turn" in explorer.text


@pytest.mark.unit
def test_unversioned_file_fails_the_version_pin(tmp_path: Path) -> None:
    import hashlib

    text = "just text"
    section = _full_tree(tmp_path, override={"curator": text})
    # the honest pin for the unversioned file: the header check must
    # still fail closed (declared label vs absent header)
    assert parse_prompt_version(text) == "unversioned"
    section["curator"] = {
        "version": "curator-v1",
        "path": "prompts/curator/curator-v1.md",
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }
    with pytest.raises(PromptPinError, match="version header"):
        resolve_prompts(section, prompts_root=tmp_path)


@pytest.mark.unit
def test_tool_schema_hash_order_insensitive() -> None:
    assert tool_schema_hash(["a.b", "c.d"]) == tool_schema_hash(["c.d", "a.b"])
    assert tool_schema_hash(["a.b"]) != tool_schema_hash(["a.c"])
