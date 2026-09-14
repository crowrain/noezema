"""Tests for the content-addressed artifact store (T2.11, ADR-0002)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from packages.artifacts.store import ArtifactStoreError, FilesystemArtifactStore


@pytest.fixture
def store(tmp_path: Path) -> FilesystemArtifactStore:
    return FilesystemArtifactStore(tmp_path / "artifacts")


@pytest.mark.unit
def test_put_get_roundtrip(store) -> None:
    data = b"hello artifact"
    sha = store.put(data, origin="session_workspace", trust_class="session_workspace")
    assert sha == hashlib.sha256(data).hexdigest()
    assert store.get(sha) == data
    assert store.exists(sha)


@pytest.mark.unit
def test_layout_first_two_chars(store) -> None:
    data = b"x" * 10
    sha = store.put(data, origin="o", trust_class="t")
    assert (store.root / sha[:2] / sha).is_file()


@pytest.mark.unit
def test_put_is_idempotent(store) -> None:
    data = b"same bytes"
    sha1 = store.put(data, origin="o", trust_class="t")
    sha2 = store.put(data, origin="o", trust_class="t")
    assert sha1 == sha2
    assert store.head(sha1).size == len(data)


@pytest.mark.unit
def test_get_missing_raises(store) -> None:
    with pytest.raises(ArtifactStoreError):
        store.get("0" * 64)


@pytest.mark.unit
def test_invalid_hash_rejected(store) -> None:
    with pytest.raises(ArtifactStoreError):
        store.get("nothex")
    assert store.exists("nothex") is False
    assert store.head("nothex") is None


@pytest.mark.unit
def test_objects_are_immutable_paths(store) -> None:
    data = b"v1"
    sha = store.put(data, origin="o", trust_class="t")
    path = store.root / sha[:2] / sha
    # simulate on-disk tampering of the immutable object
    path.write_bytes(b"TAMPERED!")
    # re-putting the ORIGINAL bytes must detect the size mismatch, not
    # silently "restore" the object
    with pytest.raises(ArtifactStoreError):
        store.put(data, origin="o", trust_class="t")
