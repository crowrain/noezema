"""Shared pytest fixtures (T0.6).

``fake_llm`` spawns a deterministic OpenAI-compatible server (T0.5) on an
ephemeral port and yields a handle to script its responses. ``db`` provides an
async SQLAlchemy session bound to the CI PostgreSQL service when
``NOEZEMA_TEST_DATABASE_URL`` is set, and skips otherwise so unit tests run
anywhere.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

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


@pytest.fixture()
async def migrated_db(test_db_url: str) -> AsyncIterator[tuple[str, AsyncEngine]]:
    """Scratch database with `alembic upgrade head` applied.

    Yields (scratch_url, engine). The engine is disposed and the database
    dropped on teardown.
    """
    import secrets
    import subprocess
    from urllib.parse import urlparse

    from sqlalchemy.ext.asyncio import create_async_engine

    parts = urlparse(test_db_url)
    dbname = f"noezema_mig_{secrets.token_hex(4)}"
    scratch_url = parts._replace(path=f"/{dbname}").geturl()

    admin = create_async_engine(test_db_url, isolation_level="AUTOCOMMIT")
    from sqlalchemy import text

    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{dbname}"'))
    await admin.dispose()

    engine = None
    try:
        env = dict(os.environ)
        env["NOEZEMA_DATABASE_URL"] = scratch_url
        proc = subprocess.run(
            [sys.executable, "-m", "alembic", "-c", "alembic.ini", "upgrade", "head"],
            cwd=REPO_ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert proc.returncode == 0, f"alembic failed:\n{proc.stdout}\n{proc.stderr}"

        engine = create_async_engine(scratch_url)
        yield scratch_url, engine
    finally:
        if engine is not None:
            await engine.dispose()
        admin = create_async_engine(test_db_url, isolation_level="AUTOCOMMIT")
        try:
            async with admin.connect() as conn:
                await conn.execute(text(f'DROP DATABASE "{dbname}"'))
        finally:
            await admin.dispose()


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
    env = _docker_env()
    rc = subprocess.run(
        [docker_engine, "image", "inspect", SANDBOX_IMAGE], capture_output=True, timeout=30, env=env
    ).returncode
    if rc != 0:
        build_cmd = [
            docker_engine,
            "build",
            "-f",
            str(REPO_ROOT / "sandbox" / "Containerfile"),
            "-t",
            SANDBOX_IMAGE,
            str(REPO_ROOT / "sandbox"),
        ]
        subprocess.run(build_cmd, capture_output=True, timeout=600, check=True, env=env)
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
