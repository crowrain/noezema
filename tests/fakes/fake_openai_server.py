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

The server is single-process and single-client by design (one session at a
time in v1, ARCHITECTURE §5.2.1).
"""

from __future__ import annotations

import argparse
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


def _record(body: dict[str, Any]) -> None:
    _state["requests"].append(
        {
            "n": next(_state["seq"]),
            "model": body.get("model"),
            "response_format": body.get("response_format"),
            "messages_count": len(body.get("messages", [])),
        }
    )


@app.post("/v1/chat/completions")
async def chat_completions(body: dict[str, Any]) -> dict[str, Any]:
    _record(body)
    queue: list[dict[str, Any]] = _state["queue"]
    if not queue:
        raise HTTPException(status_code=500, detail="no scripted responses left")
    scripted: dict[str, Any] = queue.pop(0)

    error = scripted.get("error")
    if error == "invalid_json":
        # 200 OK, but the model "produced" non-JSON content for a structured
        # request — simulates a model hiccup the gateway must recover from.
        message = "this is definitely not json"
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
        "usage": {"prompt_tokens": 10, "completion_tokens": 20, "total_tokens": 30},
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
