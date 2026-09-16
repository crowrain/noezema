"""Tests for the M1 stub tool executor (dev stand-in for the sandbox)."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.orchestrator.executor import StubToolExecutor, arguments_hash
from packages.domain.models.base import JsonDict


@pytest.mark.unit
async def test_workspace_write_read_roundtrip(tmp_path: Path) -> None:
    ex = StubToolExecutor(tmp_path)
    w = await ex.execute("workspace.write", {"path": "a/b.txt", "content": "hello"})
    assert w.ok and w.data["bytes"] == 5
    r = await ex.execute("workspace.read", {"path": "a/b.txt"})
    assert r.ok and r.data["content"] == "hello"


@pytest.mark.unit
async def test_path_traversal_blocked(tmp_path: Path) -> None:
    (tmp_path / "secret.txt").write_text("s3cret")
    ex = StubToolExecutor(tmp_path)
    obs = await ex.execute("workspace.read", {"path": "../secret.txt"})
    assert not obs.ok
    obs2 = await ex.execute("workspace.read", {"path": "/etc/passwd"})
    assert not obs2.ok


@pytest.mark.unit
async def test_workspace_list(tmp_path: Path) -> None:
    ex = StubToolExecutor(tmp_path)
    await ex.execute("workspace.write", {"path": "x.txt", "content": "x"})
    obs = await ex.execute("workspace.list", {"path": "."})
    assert obs.ok and "x.txt" in obs.data["entries"]


@pytest.mark.unit
async def test_python_execute(tmp_path: Path) -> None:
    ex = StubToolExecutor(tmp_path)
    obs = await ex.execute("python.execute", {"code": "print(6*7)"})
    assert obs.ok and obs.data["stdout"].strip() == "42" and obs.data["exit_code"] == 0


@pytest.mark.unit
async def test_python_execute_failure(tmp_path: Path) -> None:
    ex = StubToolExecutor(tmp_path)
    obs = await ex.execute("python.execute", {"code": "raise SystemExit(3)"})
    assert not obs.ok and obs.data["exit_code"] == 3


@pytest.mark.unit
async def test_unknown_tool(tmp_path: Path) -> None:
    ex = StubToolExecutor(tmp_path)
    obs = await ex.execute("net.fetch", {"url": "http://x"})
    assert not obs.ok and "unknown tool" in (obs.error or "")


@pytest.mark.unit
async def test_memory_search_without_db(tmp_path: Path) -> None:
    # T7.7 (EVAL-2): no db / no snapshot pin → empty, never an error
    ex = StubToolExecutor(tmp_path)
    obs = await ex.execute("memory.search", {"query": "x"})
    assert obs.ok and obs.data["results"] == []


@pytest.mark.unit
def test_arguments_hash_is_canonical() -> None:
    a: JsonDict = {"b": 1, "a": [1, 2]}
    b: JsonDict = {"a": [1, 2], "b": 1}
    assert arguments_hash(a) == arguments_hash(b)
    assert arguments_hash(a) != arguments_hash({"a": [1, 2], "b": 2})
