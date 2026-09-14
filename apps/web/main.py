"""Web API entry point (M1).

Usage: python -m apps.web.main   (or: uvicorn apps.web.main:app)
"""

from __future__ import annotations

import uvicorn

from apps.web.api import build_standalone_app

app = build_standalone_app()

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8321)
