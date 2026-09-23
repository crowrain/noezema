"""Model roles and prompt loading (T1.10; T7.35/ADR-0019: content pinning).

Roles are a closed set. The session's prompt set is resolved from the
effective config snapshot's ``prompts`` section: each role entry is a
CONTENT PIN — ``path`` (relative to the repository root, e.g.
``prompts/curator/curator-v4.md``), ``version`` (the ``version:`` header
label) and ``sha256`` (hash of the file's raw bytes). The loader
resolves the file BY THE PAYLOAD REFERENCE (the path is no longer
hardcoded) and verifies BOTH the version header and the content hash;
any mismatch raises ``PromptPinError`` — fail-closed, the session does
not start (the silent substitution of T7.33/ADR-0019 is impossible:
disk edits never change what a pinned snapshot loads).

Historical prompt texts live in ``prompts/<role>/<version>.md``
(restored byte-exact from git history); the versioned files are the
single source of truth — there are no second, diverging copies.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from packages.domain.canonical import canonical_sha256, sha256_hex

# The prompt paths in a payload are relative to the repository root
# (the historical shape ``prompts/<role>.md``, T7.35: now versioned files).
REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS_DIR = REPO_ROOT / "prompts"  # kept for docs/tests: the prompts tree


class Role(StrEnum):
    EXPLORER = "explorer"
    CURATOR = "curator"
    # T5.2 (stage 4): multi-step planning — the explorer role with a
    # dedicated planning prompt snapshot (§5.5: one model, different
    # prompts; role switch = context rebuild + new prefill)
    PLANNER = "planner"
    # T5.3 (stage 4): the verifier — organizes deterministic checks
    # and interprets their results; never assigns grade/confidence
    # (§3.7, §5.5)
    VERIFIER = "verifier"
    # T5.5 (stage 4): the extractor — a model without tools that
    # extracts verbatim chunks from untrusted documents (§11.2)
    EXTRACTOR = "extractor"


class PromptPinError(RuntimeError):
    """A payload prompt pin cannot be satisfied (T7.35, ADR-0019).

    Fail-closed: the session must not start. The message names the role
    and the exact check that failed (missing pin, missing file, path
    outside the repository root, sha256 mismatch, version-header
    mismatch, unknown/missing role).
    """


@dataclass(frozen=True, slots=True)
class LoadedPrompt:
    role: Role
    version: str
    text: str
    # sha256 of the file's raw bytes — the content identity pinned in
    # the payload (T7.35). Not a canonical-JSON hash: the pin is over
    # the bytes the model actually receives.
    sha256: str


def parse_prompt_version(text: str) -> str:
    """Extract the ``version:`` header label (``unversioned`` if absent)."""
    version = "unversioned"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            version = stripped.split(":", 1)[1].strip()
            break
    return version


def _resolve_one(role: Role, entry: Any, root: Path) -> LoadedPrompt:
    if not isinstance(entry, Mapping):
        raise PromptPinError(f"prompt pin for role '{role.value}' must be a mapping")
    path_raw = entry.get("path")
    version = entry.get("version")
    sha_pin = entry.get("sha256")
    if not isinstance(path_raw, str) or not path_raw:
        raise PromptPinError(f"prompt pin for role '{role.value}': 'path' is missing or not a string")
    if not isinstance(version, str) or not version:
        raise PromptPinError(f"prompt pin for role '{role.value}': 'version' is missing or not a string")
    if not isinstance(sha_pin, str) or len(sha_pin) != 64:
        raise PromptPinError(
            f"prompt pin for role '{role.value}': payload is NOT content-pinned "
            f"(no 'sha256' of the prompt text) — legacy unpinned payloads are "
            "rejected, the session does not start (ADR-0019)"
        )

    path = Path(path_raw)
    if path.is_absolute():
        raise PromptPinError(f"prompt pin for role '{role.value}': path must be relative: {path_raw}")
    resolved = (root / path).resolve()
    if resolved != root.resolve() and root.resolve() not in resolved.parents:
        raise PromptPinError(f"prompt pin for role '{role.value}': path escapes the repository root: {path_raw}")

    if not resolved.is_file():
        raise PromptPinError(f"prompt pin for role '{role.value}': file not found: {path_raw}")
    raw = resolved.read_bytes()
    actual_sha = sha256_hex(raw)
    if actual_sha != sha_pin.lower():
        raise PromptPinError(
            f"prompt pin for role '{role.value}' ({path_raw}): sha256 mismatch — "
            f"payload pins {sha_pin.lower()}, file content is {actual_sha}; "
            "the file was modified after the payload was frozen"
        )
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PromptPinError(
            f"prompt pin for role '{role.value}' ({path_raw}): not a UTF-8 text file"
        ) from exc
    header_version = parse_prompt_version(text)
    if header_version != version:
        raise PromptPinError(
            f"prompt pin for role '{role.value}' ({path_raw}): version header "
            f"'{header_version}' does not match the declared version '{version}'"
        )
    return LoadedPrompt(role=role, version=version, text=text, sha256=actual_sha)


def resolve_prompts(
    prompts_section: Mapping[str, Any] | None,
    prompts_root: Path | None = None,
) -> dict[Role, LoadedPrompt]:
    """Resolve the session's full prompt set from a payload's ``prompts``
    section (T7.35, ADR-0019). Every role of the closed set must be
    present and content-pinned; any problem raises ``PromptPinError``
    (fail-closed). The pure function is testable in isolation; the
    orchestrator calls it per session from the effective snapshot."""
    root = prompts_root if prompts_root is not None else REPO_ROOT
    if not isinstance(prompts_section, Mapping):
        raise PromptPinError("payload 'prompts' section is missing or not a mapping")
    unknown = set(prompts_section) - {role.value for role in Role}
    if unknown:
        raise PromptPinError(
            f"payload 'prompts' section has entries for unknown roles: {sorted(unknown)} "
            "(the role set is closed)"
        )

    resolved: dict[Role, LoadedPrompt] = {}
    for role in Role:
        if role.value not in prompts_section:
            raise PromptPinError(f"payload 'prompts' section has no entry for role '{role.value}'")
        resolved[role] = _resolve_one(role, prompts_section[role.value], root)
    return resolved


def tool_schema_hash(tool_names: list[str]) -> str:
    """Hash of the tool set presented to the model (part of fingerprint)."""
    return canonical_sha256(sorted(tool_names))
