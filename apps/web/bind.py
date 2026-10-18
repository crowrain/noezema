"""Web standalone configuration (T7.59(b)): bind from env + the fail-closed rule (§13.1–§13.2).

Pure module on purpose (AGENTS §4): `apps/web/main.py` is an entry point with side
effects (it builds the app), so the bind decision lives here and is unit-tested here.

Defaults are the historical ones — 127.0.0.1:8321, loopback only, behaviour unchanged.
The fail-closed rule: a non-loopback bind requires `NOEZEMA_ADMIN_TOKEN`. GET endpoints
are open by design (§13.1), but the mutating ones are guarded by exactly that token —
`POST /api/v1/commands` (T3.18) and `POST /api/v1/questions` (T7.59). Publishing the UI to
a network without a token would publish a write API nobody can authenticate against.

The standalone workspace path is resolved here for the same reason: it used to be hardcoded
to `/var/lib/noezema/workspace`, which a dev stand (non-root unit user, own data root) cannot
create. It now follows `NOEZEMA_DATA_ROOT` — the exact env and default the wake tick uses
(`apps/orchestrator/scheduler.py`), so web and tick see one data root. Unset env → same path
as before, behaviour unchanged.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

from apps.orchestrator.scheduler import DATA_ROOT_ENV, DEFAULT_DATA_ROOT, WORKSPACE_SUBDIR

WEB_HOST_ENV = "NOEZEMA_WEB_HOST"
WEB_PORT_ENV = "NOEZEMA_WEB_PORT"
ADMIN_TOKEN_ENV = "NOEZEMA_ADMIN_TOKEN"

DEFAULT_WEB_HOST = "127.0.0.1"
DEFAULT_WEB_PORT = 8321

# Stub-executor workspace of the standalone web, relative to the data root (as before). The name
# is owned by the scheduler (apps/orchestrator/scheduler.py) so the web, the wake tick and the
# manual orchestrator entry cannot drift apart (T7.59(в)).
STANDALONE_WORKSPACE_SUBDIR = WORKSPACE_SUBDIR

# Same fail-closed exit code hostctl uses for a configuration refusal (AGENTS §6).
EXIT_CONFIG_ERROR = 78


class WebBindError(ValueError):
    """The requested bind is refused (malformed value or the token rule)."""


def is_loopback_host(host: str) -> bool:
    """True for addresses that cannot be reached from another machine."""
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def resolve_web_bind(
    raw_host: str | None,
    raw_port: str | None,
    admin_token: str | None,
) -> tuple[str, int]:
    """Resolve (host, port) for uvicorn; raise WebBindError on a refused bind."""
    host = (raw_host or "").strip() or DEFAULT_WEB_HOST
    if any(char.isspace() for char in host):
        raise WebBindError(f"{WEB_HOST_ENV} is not a usable host: {raw_host!r}")

    raw = (raw_port or "").strip()
    try:
        port = int(raw) if raw else DEFAULT_WEB_PORT
    except ValueError as exc:
        raise WebBindError(f"{WEB_PORT_ENV}={raw!r} is not an integer port") from exc
    if not 1 <= port <= 65535:
        raise WebBindError(f"{WEB_PORT_ENV}={port} is outside 1..65535")

    if not is_loopback_host(host) and not (admin_token or "").strip():
        raise WebBindError(
            f"refusing to bind {host}:{port} while {ADMIN_TOKEN_ENV} is empty: the query API is "
            "open by design (§13.1), but POST /api/v1/commands (T3.18) and "
            "POST /api/v1/questions (T7.59) are guarded by that token — a public bind without it "
            f"would expose mutating endpoints unauthenticated. Set {ADMIN_TOKEN_ENV} or bind to "
            "127.0.0.1/::1/localhost."
        )
    return host, port


def resolve_standalone_workspace(raw_data_root: str | Path | None) -> Path:
    """Where the standalone web puts stub-executor files: `<data root>/workspace`.

    Accepts either the raw `NOEZEMA_DATA_ROOT` value or an already resolved data root
    (`apps/orchestrator/scheduler.data_root_from_env`) — honouring that env is what lets a dev
    stand run as an ordinary user instead of writing into a production path. An unset or empty
    value resolves to the historical `/var/lib/noezema/workspace`.
    """
    if isinstance(raw_data_root, Path):
        path = raw_data_root
    else:
        path = Path((raw_data_root or "").strip() or DEFAULT_DATA_ROOT)
    if not path.is_absolute():
        raise WebBindError(
            f"{DATA_ROOT_ENV} must be an absolute path, got {str(raw_data_root)!r}"
        )
    return path / STANDALONE_WORKSPACE_SUBDIR
