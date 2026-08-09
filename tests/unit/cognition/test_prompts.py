"""Tests for config-owned immutable prompt snapshots."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError

from packages.cognition import PromptBundle, PromptKind, PromptSnapshot


def _load(path: Path, kind: PromptKind, version: str = "v1") -> PromptSnapshot:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    return PromptSnapshot.load(
        path,
        kind=kind,
        version=version,
        expected_sha256=digest,
    )


def test_snapshot_requires_the_configured_digest(tmp_path: Path) -> None:
    path = tmp_path / "explorer.md"
    path.write_bytes(b"Exact prompt.\n")

    with pytest.raises(ValueError, match="digest mismatch"):
        PromptSnapshot.load(
            path,
            kind=PromptKind.EXPLORER,
            version="explorer/v1",
            expected_sha256="0" * 64,
        )


@pytest.mark.parametrize("raw", [b"CRLF\r\n", b"missing final LF", b"\xef\xbb\xbfBOM\n"])
def test_snapshot_rejects_noncanonical_bytes(tmp_path: Path, raw: bytes) -> None:
    path = tmp_path / "prompt.md"
    path.write_bytes(raw)

    with pytest.raises(ValueError):
        PromptSnapshot.load(
            path,
            kind=PromptKind.IDENTITY,
            version="identity/v1",
            expected_sha256=hashlib.sha256(raw).hexdigest(),
        )


def test_bundle_composes_exact_role_and_identity_bytes(tmp_path: Path) -> None:
    identity_path = tmp_path / "identity.md"
    role_path = tmp_path / "curator.md"
    identity_path.write_bytes(b"Identity.\n")
    role_path.write_bytes(b"Curator.\n")
    bundle = PromptBundle(
        identity=_load(identity_path, PromptKind.IDENTITY, "identity/v1"),
        role=_load(role_path, PromptKind.CURATOR, "curator/v1"),
    )

    assert bundle.system_prompt == "Identity.\n\nCurator.\n"
    assert bundle.version == "identity/v1+curator/v1"
    assert bundle.sha256 == hashlib.sha256(bundle.system_prompt.encode()).hexdigest()


def test_bundle_rejects_two_role_prompts(tmp_path: Path) -> None:
    explorer_path = tmp_path / "explorer.md"
    curator_path = tmp_path / "curator.md"
    explorer_path.write_bytes(b"Explorer.\n")
    curator_path.write_bytes(b"Curator.\n")

    with pytest.raises(ValidationError, match="kind=identity"):
        PromptBundle(
            identity=_load(explorer_path, PromptKind.EXPLORER),
            role=_load(curator_path, PromptKind.CURATOR),
        )
