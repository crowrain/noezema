"""Web API entry point (M1).

Usage: python -m apps.web.main   (or: uvicorn apps.web.main:app)

T7.59(b): bind comes from `NOEZEMA_WEB_HOST` / `NOEZEMA_WEB_PORT`; the defaults are the
historical ones (127.0.0.1:8321), so behaviour without those variables is unchanged.
The fail-closed rule lives in `apps.web.bind` (pure, unit-tested): a non-loopback bind
without `NOEZEMA_ADMIN_TOKEN` is refused at startup — the query API is open (§13.1) but
the command API (T3.18) and the operator question intake (T7.59) are guarded by exactly
that token. The check runs on import, so it also covers `uvicorn apps.web.main:app`.
"""

from __future__ import annotations

import os
import sys

import uvicorn

from apps.web.api import build_standalone_app
from apps.web.bind import (
    ADMIN_TOKEN_ENV,
    EXIT_CONFIG_ERROR,
    WEB_HOST_ENV,
    WEB_PORT_ENV,
    WebBindError,
    resolve_web_bind,
)

try:
    WEB_HOST, WEB_PORT = resolve_web_bind(
        os.environ.get(WEB_HOST_ENV),
        os.environ.get(WEB_PORT_ENV),
        os.environ.get(ADMIN_TOKEN_ENV, ""),
    )
except WebBindError as exc:
    sys.stderr.write(f"NOEZEMA web startup refused: {exc}\n")
    raise SystemExit(EXIT_CONFIG_ERROR) from exc

app = build_standalone_app()

if __name__ == "__main__":
    uvicorn.run(app, host=WEB_HOST, port=WEB_PORT)
