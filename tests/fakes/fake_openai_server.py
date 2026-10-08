"""Deterministic OpenAI-compatible fake LLM server (T0.5).

Purpose: run the entire CI without a GPU. Scenarios are scripted from tests:

    POST /_noezema/scenario  {"responses": [...]}   — set the response queue
    GET  /_noezema/state                              — queue length + request log
    GET  /v1/models
    POST /v1/chat/completions                         — pops the next scripted reply

A scripted response entry is a JSON object:
    {"message": "..."}                      plain text reply
    {"content": {...}}                      reply serialized as canonical JSON
                                            (for json_schema structured output)
    {"error": 500}  /  {"error": "timeout"} inject a transient HTTP failure
    {"error": "invalid_json"}               200 OK, but the content is not
                                            valid JSON (model hiccup)
    {"raw_message": "..."}                  200 OK with this VERBATIM content —
                                            no JSON round-trip, so an entry may
                                            carry a deliberately CUT-OFF JSON
                                            document (T7.80: finish_reason=length)
    {"usage": {...}}                        scripted usage block; lets a test fix
                                            completion_tokens exactly at the
                                            gateway's max_output_tokens cap
    {"finish_reason": "length"}             reported stop reason (default "stop")
    {"delay_seconds": 2.5}                  (any entry) the server sleeps
                                            before replying — a slow model

The per-request log records every body parameter except `messages` and
`response_format` (logged separately) under `params` — so a test can assert
what the gateway actually sent on each attempt, e.g. that a reasoning-off
retry really carries the engine's "stop reasoning" parameter (T7.80, ADR-0030).

The server is single-process and single-client by design (one session at a
time in v1, ARCHITECTURE §5.2.1).
"""

from __future__ import annotations

import argparse
import asyncio
import itertools
import json
import time
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()


class ScriptedResponse(BaseModel):
    message: str | None = None
    content: dict[str, Any] | None = None
    error: int | str | None = None
    finish_reason: str = "stop"
    delay_seconds: float = 0.0  # T3.30: simulate a slow model (real local LLMs: 15–90 s)
    # T7.80: verbatim assistant content — lets a script carry a CUT-OFF JSON document
    # (what an engine really returns when it stops at max_tokens), no JSON round-trip
    raw_message: str | None = None
    # T7.80: scripted usage block; None = the fixed small usage below
    usage: dict[str, Any] | None = None


class Scenario(BaseModel):
    responses: list[ScriptedResponse]


_state: dict[str, Any] = {
    "queue": [],
    "requests": [],
    "seq": itertools.count(1),
}


@app.get("/v1/models")
def models() -> dict[str, Any]:
    return {"object": "list", "data": [{"id": "fake-thinker", "object": "model"}]}


@app.post("/_noezema/scenario")
def set_scenario(body: Scenario) -> dict[str, Any]:
    _state["queue"] = [r.model_dump() for r in body.responses]
    return {"queued": len(body.responses)}


@app.get("/_noezema/state")
def state() -> dict[str, Any]:
    return {"queue_left": len(_state["queue"]), "request_count": len(_state["requests"])}


@app.get("/_noezema/requests")
def requests_log() -> dict[str, Any]:
    # T5.5: scenario tests assert what actually reached the model
    return {"requests": _state["requests"]}


def _record(body: dict[str, Any]) -> None:
    messages = body.get("messages") or []
    last_user = ""
    for message in reversed(messages):
        if message.get("role") == "user":
            content = message.get("content")
            last_user = content if isinstance(content, str) else str(content)
            break
    _state["requests"].append(
        {
            "n": next(_state["seq"]),
            "model": body.get("model"),
            "response_format": body.get("response_format"),
            "messages_count": len(messages),
            # T7.80: every other request parameter as sent (reasoning_effort,
            # chat_template_kwargs, temperature, max_tokens …) — a test asserts
            # what the engine was actually asked to do on EACH attempt
            "params": {k: v for k, v in body.items() if k not in {"messages", "response_format"}},
            # T5.5: scenario tests assert what actually reached the model
            # (e.g. that raw untrusted content never enters the context)
            "last_user": last_user[:20_000],
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(body: dict[str, Any]) -> dict[str, Any]:
    _record(body)
    queue: list[dict[str, Any]] = _state["queue"]
    if not queue:
        raise HTTPException(status_code=500, detail="no scripted responses left")
    scripted: dict[str, Any] = queue.pop(0)

    if scripted.get("delay_seconds"):
        await asyncio.sleep(float(scripted["delay_seconds"]))

    error = scripted.get("error")
    raw_message = scripted.get("raw_message")
    if error == "invalid_json":
        # 200 OK, but the model "produced" non-JSON content for a structured
        # request — simulates a model hiccup the gateway must recover from.
        message = "this is definitely not json"
    elif isinstance(raw_message, str):
        # T7.80: a verbatim answer, possibly a CUT-OFF JSON document. The fake
        # server does not repair it: classifying it is the gateway's job.
        message = raw_message
    else:
        if error is not None:
            code = error if isinstance(error, int) else 500
            raise HTTPException(status_code=code, detail="injected failure")

        message: str | None = scripted.get("message")
        content = scripted.get("content")
        if content is not None:
            message = json.dumps(content, sort_keys=True, separators=(",", ":"))
        if message is None:
            raise HTTPException(status_code=500, detail="scripted response has no message/content")

        response_format = body.get("response_format") or {}
        if response_format.get("type") == "json_schema":
            json.loads(message)  # must be valid JSON for structured-output scenarios

    scripted_usage = scripted.get("usage")
    if scripted_usage is None and scripted.get("finish_reason") == "length":
        # the measured halogen behavior (T7.80): an answer stopped by the output
        # limit reports completion_tokens EXACTLY at the requested max_tokens
        capped = body.get("max_tokens")
        completion_tokens = capped if isinstance(capped, int) else 20
        scripted_usage = {
            "prompt_tokens": 10,
            "completion_tokens": completion_tokens,
            "total_tokens": 10 + completion_tokens,
        }

    return {
        "id": f"fake-cmplt-{next(_state['seq'])}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": body.get("model", "fake-thinker"),
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": message},
                "finish_reason": scripted.get("finish_reason", "stop"),
            }
        ],
        "usage": scripted_usage or {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Fake OpenAI-compatible LLM server")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8089)
    args = parser.parse_args()
    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
