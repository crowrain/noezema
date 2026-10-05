"""Node session guard — one running session per node (T7.61(а), §5.2.1).

Why the wake admission alone is not mutual exclusion (§5.2.1 line 451 «нет незавершённой сессии с
живым lease»):

- :meth:`WakeScheduler._admission` counts **committed** rows: `SELECT count(*) FROM sessions WHERE
  state NOT IN (<terminal>)`. `run_session` creates its session row inside phase 1, and phase 1 stays
  open until the session reaches COMMITTING — so while a session is running in another process, that
  process's admission answer for a second wake is still "wake".
- `system_constants.node_state = 'session_running'` is not an admission condition either (`_admission`
  honours only `paused`, and T7.59(в) deliberately keeps a leftover external-tick marker non-blocking:
  making it blocking would wedge the node after a killed unit).

So every host entry point that starts a session takes this lock **between** its admission decision and
the marker write, and holds it for the whole `run_session`: wake tick, web «wake now», eval-run and the
manual `python -m apps.orchestrator`. The key is `hashtext(node_session_lock_name(owner))` computed by
PostgreSQL — identical in every process of the node (see `apps.orchestrator.scheduler`); Python
`hash()` is salted per process and would give each unit its own lock.

The mechanism is a session-level PostgreSQL advisory lock held on a **dedicated** connection (`NullPool`,
AUTOCOMMIT): ARCHITECTURE already uses this idiom for host ownership — §8.7.2 «DB ownership —
session-level PostgreSQL advisory lock на scope; это не lease и не fencing protocol», §12 «Advisory lock
держит НЕ транзакция, а соединение… потеря connection немедленно завершает скрипт» — and the project has
the same shape in `packages/memory/cascade.py` (writer gate) and `hostctl/offline_rules.py`. Holding it
on a separate connection is also what makes it safe next to phase 1: the guard never sits inside the
session's transaction, so it neither joins the canonical DB lock order (§5.2.2) nor waits on a row lock.

Crash semantics are the point of choosing this mechanism: Postgres drops a session-level advisory lock
when the holding connection dies, so a tick unit that is killed mid-session (`systemctl stop`, an OOM, a
crash) leaves NO wedged node lane — nothing to reset and no manual cleanup, exactly as `status.sh`
promises. The same applies to `pg_terminate_backend`.

Residual property (recorded in STATUS.md T7.61(а)): session-level advisory locks need one DB session per
client connection. They do NOT provide mutual exclusion behind a transaction-mode pooler (pgbouncer).
The project connects directly to PostgreSQL at every host entry point (no pooler in `deploy/`), so the
choice is sound; if a transaction pooler ever appears, this guard must become a durable row with its own
lease instead.
"""

from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

from sqlalchemy import text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from apps.orchestrator.scheduler import node_session_lock_name

logger = logging.getLogger(__name__)


class NodeSessionInProgress(RuntimeError):
    """Another process of this node already owns the session lane (T7.61(а))."""


class NodeSessionGuardError(RuntimeError):
    """The lane cannot be evaluated (the DB refused the try-lock) — fail closed, no session."""


def _connect_arg(target: AsyncEngine | str) -> str | URL:
    """What `create_async_engine` must be given to open a SECOND connection to the same database.

    An `AsyncEngine` is accepted because every host entry point already owns one. Its `.url` is passed
    as the URL OBJECT, not as a string: `str(engine.url)` masks the password (`user:***@host/db`) and
    would produce an engine that cannot connect.
    """
    if isinstance(target, AsyncEngine):
        return target.url
    return target


async def _close_quietly(engine: AsyncEngine, conn: AsyncConnection | None) -> None:
    """Close the guard's dedicated connection; with `NullPool` this is a physical close.

    A closed connection is itself the release guarantee: PostgreSQL drops every session-level advisory
    lock of a backend whose connection went away. Errors here are noise about an already-dead backend.
    """
    if conn is not None:
        with contextlib.suppress(Exception):
            await conn.close()
    with contextlib.suppress(Exception):
        await engine.dispose()


@dataclass
class NodeSessionGuard:
    """This process's hold on one node's session lane. `acquire` → run the session → `release`.

    Not a lease and not a fencing token (§8.7.2): it decides who may START a session, nothing more. The
    session's own lease (`packages/domain/services/lease.py`, §5.2.3) is still what authorizes its
    commit, and `WakeScheduler.decide` keeps checking the §5.2.1 admission list unchanged.
    """

    target: AsyncEngine | str
    node_owner: str
    _engine: AsyncEngine | None = field(default=None, repr=False)
    _conn: AsyncConnection | None = field(default=None, repr=False)

    @property
    def name(self) -> str:
        """The lock NAME; PostgreSQL hashes it (`hashtext`), so the key is process-independent."""
        return node_session_lock_name(self.node_owner)

    @property
    def held(self) -> bool:
        return self._conn is not None

    async def acquire(self) -> bool:
        """Take the lane without waiting. True = ours; False = another session is running.

        Idempotent: re-acquiring while held returns True (one guard owns one connection). A DB failure is
        raised as `NodeSessionGuardError` rather than reported as "free": not being able to check
        exclusivity must not start a second session (§3 «Host-контур: fail-closed»).
        """
        if self._conn is not None:
            return True
        engine = create_async_engine(
            _connect_arg(self.target), poolclass=NullPool, isolation_level="AUTOCOMMIT"
        )
        conn: AsyncConnection | None = None
        try:
            conn = await engine.connect()
            granted = (
                await conn.execute(text("SELECT pg_try_advisory_lock(hashtext(:n))"), {"n": self.name})
            ).scalar_one()
        except Exception as exc:  # any driver/DB failure: exclusivity is unknowable → fail closed
            await _close_quietly(engine, conn)
            raise NodeSessionGuardError(
                f"cannot evaluate the node session lock {self.name!r}: {type(exc).__name__}: {exc}"
            ) from exc
        if not bool(granted):
            await _close_quietly(engine, conn)
            return False
        self._engine, self._conn = engine, conn
        return True

    async def release(self) -> None:
        """Give the lane back. Idempotent and safe to call twice (session task + done callback do)."""
        conn, engine = self._conn, self._engine
        self._conn, self._engine = None, None
        if conn is None or engine is None:
            return
        try:
            released = (
                await conn.execute(text("SELECT pg_advisory_unlock(hashtext(:n))"), {"n": self.name})
            ).scalar_one()
            if not bool(released):
                logger.warning(
                    "node session lock %s: this connection no longer holds it (backend restarted?)",
                    self.name,
                )
        except Exception as exc:
            # The backend vanished mid-session. Postgres has already dropped the session-level lock with
            # the connection; the close below is what makes that certain. Named in the log, not swallowed.
            logger.warning(
                "node session lock %s: unlock failed (%s); closing the connection releases it",
                self.name,
                type(exc).__name__,
            )
        await _close_quietly(engine, conn)


@asynccontextmanager
async def node_session_guard(target: AsyncEngine | str, node_owner: str) -> AsyncIterator[NodeSessionGuard]:
    """`async with node_session_guard(engine, owner): run_session()` — busy raises NodeSessionInProgress.

    Entry points that must answer "skip" instead of raising use :meth:`NodeSessionGuard.acquire` /
    :meth:`NodeSessionGuard.release` directly (wake tick, eval-run); the manager is the shape that cannot
    forget the release.
    """
    guard = NodeSessionGuard(target=target, node_owner=node_owner)
    if not await guard.acquire():
        raise NodeSessionInProgress(
            f"node {node_owner!r}: a session is already running (lock {guard.name})"
        )
    try:
        yield guard
    finally:
        await guard.release()
