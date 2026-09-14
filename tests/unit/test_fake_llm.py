"""Tests for the deterministic fake OpenAI-compatible server (T0.5/T0.6)."""

from __future__ import annotations

import json

import httpx
import pytest

from tests.conftest import FakeLLM


@pytest.mark.unit
def test_models_endpoint(fake_llm: FakeLLM) -> None:
    r = httpx.get(f"{fake_llm.base_url}/models", timeout=5.0)
    assert r.status_code == 200
    body = r.json()
    assert body["data"][0]["id"] == "fake-thinker"


@pytest.mark.unit
def test_scripted_text_response(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"message": "hello"}])
    r = httpx.post(
        f"{fake_llm.base_url}/chat/completions",
        json={"model": "fake-thinker", "messages": [{"role": "user", "content": "hi"}]},
        timeout=5.0,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["choices"][0]["message"]["content"] == "hello"
    assert body["choices"][0]["finish_reason"] == "stop"
    assert body["usage"]["total_tokens"] == 30


@pytest.mark.unit
def test_scripted_json_content_is_canonical(fake_llm: FakeLLM) -> None:
    payload = {"b": 1, "a": [1, 2]}
    fake_llm.script([{"content": payload}])
    r = httpx.post(
        f"{fake_llm.base_url}/chat/completions",
        json={
            "model": "fake-thinker",
            "messages": [],
            "response_format": {"type": "json_schema", "json_schema": {"name": "t", "schema": {}}},
        },
        timeout=5.0,
    )
    assert r.status_code == 200
    content = r.json()["choices"][0]["message"]["content"]
    assert content == json.dumps(payload, sort_keys=True, separators=(",", ":"))
    assert json.loads(content) == payload


@pytest.mark.unit
def test_response_queue_is_fifo(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"message": "first"}, {"message": "second"}])
    url = f"{fake_llm.base_url}/chat/completions"
    first = httpx.post(url, json={"model": "m", "messages": []}, timeout=5.0).json()
    second = httpx.post(url, json={"model": "m", "messages": []}, timeout=5.0).json()
    assert first["choices"][0]["message"]["content"] == "first"
    assert second["choices"][0]["message"]["content"] == "second"
    assert fake_llm.state()["queue_left"] == 0


@pytest.mark.unit
def test_exhausted_queue_returns_500(fake_llm: FakeLLM) -> None:
    r = httpx.post(
        f"{fake_llm.base_url}/chat/completions",
        json={"model": "m", "messages": []},
        timeout=5.0,
    )
    assert r.status_code == 500


@pytest.mark.unit
def test_injected_error(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"error": 503}])
    r = httpx.post(
        f"{fake_llm.base_url}/chat/completions",
        json={"model": "m", "messages": []},
        timeout=5.0,
    )
    assert r.status_code == 503


@pytest.mark.unit
def test_request_log_records_calls(fake_llm: FakeLLM) -> None:
    fake_llm.script([{"message": "x"}])
    messages = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
    httpx.post(
        f"{fake_llm.base_url}/chat/completions",
        json={"model": "fake-thinker", "messages": messages},
        timeout=5.0,
    ).raise_for_status()
    st = fake_llm.state()
    assert st["request_count"] == 1
    req = httpx.get(f"{fake_llm.root}/_noezema/state", timeout=5.0).json()
    assert req["queue_left"] == 0
