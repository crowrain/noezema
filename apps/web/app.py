"""FastAPI app — status page, timeline API, message inbox, operator commands."""

from __future__ import annotations

import uuid

from fastapi import FastAPI

app = FastAPI(title="NOEZEMA")


@app.get("/")
async def status_page() -> dict:
    """MVP status — returns JSON (HTML page later)."""
    return {
        "name": "noezema",
        "version": "0.1.0-dev",
        "status": "running",
        "claims_count": 0,
        "pending_questions": 0,
        "unresolved_commits": 0,
    }


@app.get("/api/timeline")
async def timeline(limit: int = 50) -> list[dict]:
    """SSE-compatible audit timeline."""
    return []  # MVP: no DB yet


@app.post("/api/message")
async def send_message(sender: str, body: str, priority: str = "normal") -> dict:
    """Send a message to the thinker (stored in inbox)."""
    mid = uuid.uuid4()
    return {"id": str(mid), "state": "queued"}


@app.post("/api/command")
async def operator_command(actor: str, type: str, arguments: dict | None = None) -> dict:
    """Typed operator command with idempotency."""
    idem = uuid.uuid4()
    allowed = {"wake_now", "pause", "resume", "stop_gracefully", "abort_session"}
    if type not in allowed:
        return {"error": f"Unknown command: {type}"}
    return {"id": str(idem), "state": "accepted"}
