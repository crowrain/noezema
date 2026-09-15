"""The research proxy HTTP API (T6.1, stage 5, §5.12).

A deliberately small surface — the proxy is infrastructure, not a
service for humans:

- ``POST /fetch`` — the controlled egress (the only way out to the
  network); refused in sealed mode (the bootstrap default) and on any
  guard/limit violation;
- ``GET /healthz`` — liveness.

The response envelope never contains page content: only the
content-addressed hashes, the source/chunk ids and the untrusted-
external-content marking. Content is read back through the artifact
store (the same content-addressed paths as any other artifact).
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from apps.research_proxy.service import (
    ResearchProxyError,
    ResearchProxyService,
)


class FetchRequest(BaseModel):
    url: str


def create_proxy_app(service: ResearchProxyService) -> FastAPI:
    app = FastAPI(title="noezema-research-proxy", version="1.0")
    app.state.service = service

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/fetch")
    async def fetch(body: FetchRequest) -> dict[str, Any]:
        try:
            return await service.fetch(body.url)
        except ResearchProxyError as exc:
            status = 403 if exc.status == "rejected" else 502
            raise HTTPException(
                status_code=status,
                detail={"reason": exc.reason},
            ) from exc
        except ValueError as exc:
            # ConfigError (no effective config) and similar
            raise HTTPException(status_code=503, detail={"reason": str(exc)}) from exc

    return app
