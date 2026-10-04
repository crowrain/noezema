"""Shared pytest fixtures (T0.6).

``fake_llm`` spawns a deterministic OpenAI-compatible server (T0.5) on an
ephemeral port and yields a handle to script its responses. ``db`` provides an
async SQLAlchemy session bound to the CI PostgreSQL service when
``NOEZEMA_TEST_DATABASE_URL`` is set, and skips otherwise so unit tests run
anywhere.
"""

from __future__ import annotations

import asyncio
import fcntl
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import warnings
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from packages.sandbox.runtime import ContainerSandboxRuntime, SandboxProfile, sandbox_available

REPO_ROOT = Path(__file__).resolve().parent.parent


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class FakeLLM:
    """Handle to a running fake OpenAI-compatible server."""

    def __init__(self, root: str) -> None:
        self.root = root  # http://127.0.0.1:port (no /v1)
        self.base_url = f"{root}/v1"
        self._client = httpx.Client(timeout=10.0)

    def script(self, responses: list[dict]) -> None:
        r = self._client.post(f"{self.root}/_noezema/scenario", json={"responses": responses})
        r.raise_for_status()

    def state(self) -> dict:
        r = self._client.get(f"{self.root}/_noezema/state")
        r.raise_for_status()
        return r.json()

    def requests(self) -> list[dict]:
        """The per-request log (model, response_format, last user
        message) — scenario tests assert what actually reached the
        model (e.g. the extraction profile, T5.5)."""
        r = self._client.get(f"{self.root}/_noezema/requests")
        r.raise_for_status()
        return r.json()["requests"]

    def close(self) -> None:
        self._client.close()


@pytest.fixture()
def fake_llm() -> Iterator[FakeLLM]:
    port = _free_port()
    root = f"http://127.0.0.1:{port}"
    proc = subprocess.Popen(
        [sys.executable, "-m", "tests.fakes.fake_openai_server", "--port", str(port)],
        cwd=REPO_ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 20
        up = False
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError("fake LLM server exited during startup")
            try:
                if httpx.get(f"{root}/v1/models", timeout=1.0).status_code == 200:
                    up = True
                    break
            except httpx.HTTPError:
                pass
            time.sleep(0.05)
        if not up:
            raise RuntimeError("fake LLM server did not become ready")
        handle = FakeLLM(root)
        yield handle
        handle.close()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture()
def test_db_url() -> str:
    url = os.environ.get("NOEZEMA_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("NOEZEMA_TEST_DATABASE_URL is not set")
    return url


async def _admin_exec(url: str, *statements: str) -> None:
    """Run DDL against the admin DB on a short-lived AUTOCOMMIT engine.

    The engine is fully disposed before returning — nothing may keep a
    connection open to the template while it is sealed or cloned.
    """
    engine = create_async_engine(url, isolation_level="AUTOCOMMIT")
    try:
        async with engine.connect() as conn:
            for stmt in statements:
                await conn.execute(text(stmt))
    finally:
        await engine.dispose()


def _admin_exec_sync(url: str, *statements: str) -> None:
    """Sync wrapper of `_admin_exec` for session-scope (non-async) fixtures."""
    asyncio.run(_admin_exec(url, *statements))


def _count_backends(url: str, dbname: str) -> int:
    """Number of backends currently connected to `dbname`."""

    async def go() -> int:
        engine = create_async_engine(url)
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text("SELECT count(*) FROM pg_stat_activity WHERE datname = :db"),
                    {"db": dbname},
                )
                return int(result.scalar_one())
        finally:
            await engine.dispose()

    return asyncio.run(go())


@pytest.fixture(scope="session")
def migrated_db_template() -> Iterator[str]:
    """Fully migrated template scratch DB per pytest session/xdist worker (T7.55).

    Reads `NOEZEMA_TEST_DATABASE_URL` directly (a session fixture must not
    depend on the function-scoped `test_db_url`; both read the same env once
    per run). Built ONCE: CREATE DATABASE `noezema_tpl_<pid>_<hex>` -> `alembic
    upgrade head` subprocess (the step the old fixture repeated on every test)
    -> wait until no connections remain -> ALLOW_CONNECTIONS false (nothing may
    attach while the template is cloned; autovacuum visits are blocked too).
    Dropped in the session finalizer. A broken or missing template fails every
    dependent test with an explicit error — there is no silent fallback to
    per-test migration. Each xdist worker builds its own template, so clones
    never race across workers.
    """
    url = os.environ.get("NOEZEMA_TEST_DATABASE_URL", "")
    if not url:
        pytest.skip("NOEZEMA_TEST_DATABASE_URL is not set")

    parts = urlparse(url)
    tpl_name = f"noezema_tpl_{os.getpid()}_{secrets.token_hex(4)}"
    tpl_url = parts._replace(path=f"/{tpl_name}").geturl()

    try:
        # Template BUILDS are serialized across every pytest process on this machine (all xdist
        # workers at session start AND nested pytest runs mid-suite). Six concurrent `alembic
        # upgrade head` subprocesses are exactly the DDL-storm pattern T7.51 named as a flake
        # source: the old fixture hit it randomly per test, while a session-scope template makes
        # all workers collide at the same instant. The lock covers only the ~1-2 s build; clones
        # run freely outside it, and the lock is always released before any cross-process wait
        # (a worker never holds it while waiting on another process), so no deadlock is possible.
        with open(os.path.join(tempfile.gettempdir(), "noezema_tpl_build.lock"), "w") as build_lock:
            fcntl.flock(build_lock, fcntl.LOCK_EX)
            _admin_exec_sync(url, f'CREATE DATABASE "{tpl_name}"')
            env = dict(os.environ)
            env["NOEZEMA_DATABASE_URL"] = tpl_url
            proc = subprocess.run(
                [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
                cwd=REPO_ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )
            if proc.returncode != 0:
                raise RuntimeError(
                    f"template migration failed (alembic upgrade head):\n{proc.stdout}\n{proc.stderr}"
                )

            deadline = time.monotonic() + 20.0
            while True:
                backends = _count_backends(url, tpl_name)
                if backends == 0:
                    break
                if time.monotonic() >= deadline:
                    raise RuntimeError(f"template {tpl_name} still has {backends} connection(s); cannot clone safely")
                time.sleep(0.2)

            last_exc: Exception | None = None
            for _attempt in range(3):
                try:
                    _admin_exec_sync(
                        url,
                        "SET lock_timeout TO '10s'",
                        f'ALTER DATABASE "{tpl_name}" WITH ALLOW_CONNECTIONS false',
                    )
                    break
                except Exception as exc:  # transient lock race (e.g. a passing autovacuum worker)
                    last_exc = exc
                    time.sleep(0.5)
            else:
                raise RuntimeError(f"cannot seal template {tpl_name}: {last_exc}")

        yield tpl_name
    finally:
        try:
            _admin_exec_sync(url, f'DROP DATABASE IF EXISTS "{tpl_name}"')
        except Exception as exc:  # teardown must not mask test results; the leak stays named for manual cleanup
            warnings.warn(f"leaked scratch template {tpl_name}: {exc}", stacklevel=2)


@pytest.fixture()
async def migrated_db(
    test_db_url: str, migrated_db_template: str
) -> AsyncIterator[tuple[str, AsyncEngine]]:
    """Scratch database cloned from the session template via CREATE DATABASE ... TEMPLATE (T7.55).

    Contract unchanged: yields (scratch_url, engine), the name stays
    `noezema_mig_<hex>`, teardown disposes the engine and drops the database.
    Only creation changed — cloning the pre-migrated template is ~8-10x
    cheaper than an empty DB plus a per-test `alembic upgrade head` subprocess,
    and it removes the concurrent-DDL storm against the shared test Postgres
    (the T7.51 flake source). Retries only cover transient ACCESS EXCLUSIVE
    contention; there is no fallback to per-test migration.
    """
    parts = urlparse(test_db_url)
    dbname = f"noezema_mig_{secrets.token_hex(4)}"
    scratch_url = parts._replace(path=f"/{dbname}").geturl()

    last_exc: Exception | None = None
    for _attempt in range(3):
        try:
            await _admin_exec(
                test_db_url,
                "SET lock_timeout TO '20s'",
                f'CREATE DATABASE "{dbname}" TEMPLATE "{migrated_db_template}"',
            )
            break
        except Exception as exc:  # transient contention — retried on the template path only
            last_exc = exc
            await asyncio.sleep(0.5)
    else:
        pytest.fail(f"cannot clone scratch DB from template {migrated_db_template}: {last_exc}")

    engine: AsyncEngine | None = None
    try:
        engine = create_async_engine(scratch_url)
        yield scratch_url, engine
    finally:
        if engine is not None:
            await engine.dispose()
        await _admin_exec(test_db_url, f'DROP DATABASE "{dbname}"')


@asynccontextmanager
async def _db_session(url: str):
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await engine.dispose()


@pytest.fixture()
def db_url_factory():
    """Return an async-context factory that yields a session for a given URL."""
    return _db_session


SANDBOX_IMAGE = "noezema-sandbox:test"


def _docker_env() -> dict[str, str]:
    """Buildx needs a writable config dir; prefer the repo-local one."""
    env = dict(os.environ)
    cfg = os.environ.get("NOEZEMA_DOCKER_CONFIG")
    if not cfg and (REPO_ROOT / ".docker-config").is_dir():
        cfg = str(REPO_ROOT / ".docker-config")
    if cfg:
        env["DOCKER_CONFIG"] = cfg
    return env


@pytest.fixture(scope="session")
def docker_engine() -> str:
    if not sandbox_available("docker"):
        pytest.skip("docker engine not available")
    env = _docker_env()
    if env.get("DOCKER_CONFIG") and os.environ.get("DOCKER_CONFIG") != env["DOCKER_CONFIG"]:
        # the runtime spawns the CLI via os.environ
        os.environ["DOCKER_CONFIG"] = env["DOCKER_CONFIG"]
    return "docker"


@pytest.fixture(scope="session")
def sandbox_image(docker_engine: str) -> str:
    # The image is built ONCE before pytest starts (full check, AGENTS.md
    # §6, T7.41). Under pytest-xdist every worker is a separate process with
    # its own session scope, so a build here could race across workers on
    # one fixed tag; the fixture only verifies presence and fails with a
    # clear error if the pre-build step was skipped.
    env = _docker_env()
    rc = subprocess.run(
        [docker_engine, "image", "inspect", SANDBOX_IMAGE], capture_output=True, timeout=30, env=env
    ).returncode
    if rc != 0:
        pytest.fail(
            f"docker image {SANDBOX_IMAGE} is not built. Run the full check from AGENTS.md §6 "
            "(it builds the image before pytest) or build it manually: "
            f"{docker_engine} build -f {REPO_ROOT / 'sandbox' / 'Containerfile'} "
            f"-t {SANDBOX_IMAGE} {REPO_ROOT / 'sandbox'}"
        )
    return SANDBOX_IMAGE


def _runtime(work_root: Path, image: str) -> ContainerSandboxRuntime:
    return ContainerSandboxRuntime(image=image, engine="docker", work_root=work_root)


def _sealed() -> SandboxProfile:
    return SandboxProfile.from_yaml(REPO_ROOT / "sandbox" / "policy" / "sealed.yaml")


__all__ = [
    "FakeLLM",
    "_runtime",
    "_sealed",
    "db_url_factory",
    "docker_engine",
    "fake_llm",
    "sandbox_image",
    "test_db_url",

]
