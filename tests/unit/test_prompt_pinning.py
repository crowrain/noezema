"""T7.35 (ADR-0019): the prompt is bound to the snapshot BY CONTENT.

The main regression: a modification of a prompt file ON DISK must not
change what a pinned snapshot loads — it must either load the pinned
text or fail closed (``PromptPinError``), never a silent substitution.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.llm_gateway.roles import (
    REPO_ROOT,
    PromptPinError,
    Role,
    parse_prompt_version,
    resolve_prompts,
)

pytestmark = pytest.mark.unit

EXPLORER_V1 = "version: explorer-v1\n\n# Explorer\nDo one thing per turn.\n"
CURATOR_V1 = "version: curator-v1\n\n# Curator\nPropose memory changes.\n"
PLANNER_V1 = "version: planner-v1\n\n# Planner\n"
VERIFIER_V1 = "version: verifier-v1\n\n# Verifier\n"
EXTRACTOR_V1 = "version: extractor-v1\n\n# Extractor\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write(prompts: Path, rel: str, text: str) -> None:
    p = prompts / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def _pinned_section(prompts: Path) -> dict[str, Any]:
    """A fully-pinned prompts section pointing at the tmp tree."""
    return {
        "explorer": {"version": "explorer-v1", "path": "prompts/explorer/explorer-v1.md",
                     "sha256": _sha(EXPLORER_V1)},
        "curator": {"version": "curator-v1", "path": "prompts/curator/curator-v1.md",
                    "sha256": _sha(CURATOR_V1)},
        "planner": {"version": "planner-v1", "path": "prompts/planner/planner-v1.md",
                    "sha256": _sha(PLANNER_V1)},
        "verifier": {"version": "verifier-v1", "path": "prompts/verifier/verifier-v1.md",
                     "sha256": _sha(VERIFIER_V1)},
        "extractor": {"version": "extractor-v1", "path": "prompts/extractor/extractor-v1.md",
                      "sha256": _sha(EXTRACTOR_V1)},
    }


def _tree(prompts: Path) -> Path:
    _write(prompts, "prompts/explorer/explorer-v1.md", EXPLORER_V1)
    _write(prompts, "prompts/curator/curator-v1.md", CURATOR_V1)
    _write(prompts, "prompts/planner/planner-v1.md", PLANNER_V1)
    _write(prompts, "prompts/verifier/verifier-v1.md", VERIFIER_V1)
    _write(prompts, "prompts/extractor/extractor-v1.md", EXTRACTOR_V1)
    return prompts


def test_resolve_prompts_pinned_ok(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    loaded = resolve_prompts(_pinned_section(root), prompts_root=root)
    assert set(loaded) == set(Role)
    explorer = loaded[Role.EXPLORER]
    assert explorer.version == "explorer-v1"
    assert explorer.sha256 == _sha(EXPLORER_V1)
    assert "Do one thing per turn" in explorer.text


def test_disk_mutation_never_changes_pinned_text(tmp_path: Path) -> None:
    """THE regression of T7.35: someone edits prompts/*.md on disk after
    the payload was frozen — the pinned snapshot either loads the
    pinned bytes or fails closed; it must NEVER load the new text."""
    root = _tree(tmp_path)
    section = _pinned_section(root)

    # the file is replaced with different content (the T7.33 situation:
    # the disk silently ran a newer prompt than the payload declared)
    (root / "prompts" / "curator" / "curator-v1.md").write_text(
        "version: curator-v1\n\n# Curator\nCHANGED ON DISK\n", encoding="utf-8"
    )
    with pytest.raises(PromptPinError, match="sha256 mismatch"):
        resolve_prompts(section, prompts_root=root)

    # and with the same content but a changed header version
    (root / "prompts" / "curator" / "curator-v1.md").write_text(
        f"version: curator-v9\n\n# Curator\n{EXPLORER_V1}", encoding="utf-8"
    )
    with pytest.raises(PromptPinError):
        resolve_prompts(section, prompts_root=root)


def test_version_header_mismatch_fails_closed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    section = _pinned_section(root)
    # content hash re-computed for the tampered file: the hash check
    # passes only if the pin is also tampered — with an HONEST pin the
    # header mismatch is caught by the sha check first; catch the
    # header check itself by pinning the tampered file's hash
    tampered = "version: curator-v9\n\n# Curator\n"
    (root / "prompts" / "curator" / "curator-v1.md").write_text(tampered, encoding="utf-8")
    section["curator"] = {"version": "curator-v1", "path": "prompts/curator/curator-v1.md", "sha256": _sha(tampered)}
    with pytest.raises(PromptPinError, match="version header"):
        resolve_prompts(section, prompts_root=root)


def test_unpinned_legacy_payload_rejected(tmp_path: Path) -> None:
    """Decision (b) of ADR-0019: a payload that declares a version
    WITHOUT a content sha256 (config-v2…v6) is refused, fail-closed."""
    root = _tree(tmp_path)
    section = _pinned_section(root)
    del section["explorer"]["sha256"]  # the v2…v6 shape
    with pytest.raises(PromptPinError, match="NOT content-pinned"):
        resolve_prompts(section, prompts_root=root)


def test_loader_uses_payload_path_not_hardcoded_name(tmp_path: Path) -> None:
    """The payload reference, not a hardcoded `prompts/<role>.md`,
    decides the text (T7.35: the path is no longer ignored)."""
    root = _tree(tmp_path)
    # a file at a DIFFERENT name for the explorer role
    alt_text = "version: explorer-v1\n\n# Explorer\nAlt prompt from another path.\n"
    _write(root, "prompts/legacy/explorer-alt.md", alt_text)
    section = _pinned_section(root)
    section["explorer"] = {"version": "explorer-v1", "path": "prompts/legacy/explorer-alt.md", "sha256": _sha(alt_text)}
    loaded = resolve_prompts(section, prompts_root=root)
    assert "Alt prompt from another path" in loaded[Role.EXPLORER].text
    # and the flat hardcoded name does not even need to exist
    assert not (root / "prompts" / "explorer.md").exists()


def test_missing_file_fails_closed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    section = _pinned_section(root)
    (root / "prompts" / "verifier" / "verifier-v1.md").unlink()
    with pytest.raises(PromptPinError, match="file not found"):
        resolve_prompts(section, prompts_root=root)


def test_path_traversal_fails_closed(tmp_path: Path) -> None:
    # the prompts root is a SUBDIRECTORY: a path climbing above it
    # (even to an existing, sha-matching file) is refused
    root = tmp_path / "repo"
    _tree(root)
    outside = tmp_path / "outside.md"
    outside.write_text(EXPLORER_V1, encoding="utf-8")
    section = _pinned_section(root)
    section["explorer"] = {
        "version": "explorer-v1",
        "path": "../outside.md",
        "sha256": _sha(EXPLORER_V1),
    }
    with pytest.raises(PromptPinError, match="escapes the repository root"):
        resolve_prompts(section, prompts_root=root)


def test_unknown_or_missing_role_fails_closed(tmp_path: Path) -> None:
    root = _tree(tmp_path)
    section = _pinned_section(root)
    del section["curator"]
    with pytest.raises(PromptPinError, match="no entry for role 'curator'"):
        resolve_prompts(section, prompts_root=root)
    section2 = _pinned_section(root)
    section2["summarizer"] = {"version": "x", "path": "prompts/x.md", "sha256": "0" * 64}
    with pytest.raises(PromptPinError, match="unknown roles"):
        resolve_prompts(section2, prompts_root=root)


def test_missing_prompts_section_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(PromptPinError, match=r"prompts.*missing"):
        resolve_prompts(None, prompts_root=tmp_path)


def test_parse_prompt_version() -> None:
    assert parse_prompt_version("version: curator-v3\n\nbody") == "curator-v3"
    assert parse_prompt_version("  version: explorer-v4  \nbody") == "explorer-v4"
    assert parse_prompt_version("no header here") == "unversioned"


def test_bootstrap_payload_pins_match_repo_files() -> None:
    """The BOOTSTRAP_PAYLOAD pins (pattern T7.14, now by CONTENT,
    ADR-0019): every role resolves against the committed repo files —
    version header, path and sha256 all agree."""
    section = BOOTSTRAP_PAYLOAD["prompts"]
    assert set(section) == {r.value for r in Role}
    loaded = resolve_prompts(section)  # prompts_root defaults to the repo
    for role in Role:
        pin = section[role.value]
        assert pin["sha256"] == loaded[role].sha256
        assert pin["version"] == loaded[role].version
        assert (REPO_ROOT / pin["path"]).is_file()


def test_historical_prompt_files_match_git_blobs() -> None:
    """The restored prompts/<role>/<version>.md are byte-identical to
    the git blobs they were recovered from (ADR-0019 §3): the recorded
    sha256 constants are the hashes of `git show <commit>:prompts/<role>.md`
    at the commit where that version was current."""
    expected = {
        "prompts/curator/curator-v1.md":
            "4fe40e294717f22c79ae267b9aa6e5a97112c9b483023d282f4c83402eb18520",  # e943db7
        "prompts/curator/curator-v2.md":
            "a1b1d76b5bc0655e77a4953a301fe33d0df6076ba543d8c2d0a8a15d93c3d64e",  # c4fe70b
        "prompts/curator/curator-v3.md":
            "f93853b6cdb7f4e0a5deb17f9b642d77bd860e25814a286f9cd222d43e6c8c28",  # 6163045
        "prompts/curator/curator-v4.md":
            "6e129ded5485733a4f8e83a64e102a4985ec1a4d0f758bef88549e770d4f5692",  # 90a398f
        "prompts/explorer/explorer-v1.md":
            "2e393109d1615b3307b1b54a3dabf5bcdfe9406a430924a85bd7b7af55fb08d4",  # e943db7
        "prompts/explorer/explorer-v2.md":
            "38e9b6885d1fdcdb4700ec745091074fe907954630308512c0629935cf6a1c3f",  # c4fe70b
        "prompts/explorer/explorer-v3.md":
            "d13e87f0ada6c4430353684081cdf3c4546a0dfac52b75cdb7fe25f9f03f69db",  # 6163045
        "prompts/explorer/explorer-v4.md":
            "5829a55c4e5d92762c2d9909dd7ced9614c389448a813d52496e9d46e4b5e87e",  # b59d2b8
        "prompts/planner/planner-v1.md":
            "6aeb22bc727b9c0e24bcd8c31ba64fc653b842072bd9130409682283f83a87ee",  # de01314
        "prompts/verifier/verifier-v1.md":
            "34fe8069f7ab88dc337efeb82e89c01853f645d3a4cc1c14fec3ce4fd53c9463",  # e314246
        "prompts/extractor/extractor-v1.md":
            "af5dba626b8a5a3995f89e6ca0b892a82976d8a7d32737c1f834e4b1b4c37866",  # fb05942
    }
    for rel, sha in expected.items():
        path = REPO_ROOT / rel
        assert path.is_file(), rel
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert actual == sha, f"{rel}: {actual} != git blob {sha}"
        # the header label agrees with the file name
        text = path.read_text(encoding="utf-8")
        version = parse_prompt_version(text)
        assert version == path.name.removesuffix(".md"), rel
