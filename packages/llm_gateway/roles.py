"""Model roles and prompt loading (T1.10).

Roles are a closed set defined by the config snapshot's prompts section;
the gateway knows how to load and version them. Prompts live in
``prompts/<role>.md`` with a ``version:`` header line — the version goes
into the call fingerprint.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from packages.domain.canonical import canonical_sha256

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


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


@dataclass(frozen=True, slots=True)
class LoadedPrompt:
    role: Role
    version: str
    text: str
    sha256: str


def load_prompt(role: Role, prompts_dir: Path | None = None) -> LoadedPrompt:
    directory = prompts_dir if prompts_dir is not None else PROMPTS_DIR
    path = directory / f"{role.value}.md"
    text = path.read_text(encoding="utf-8")

    version = "unversioned"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("version:"):
            version = stripped.split(":", 1)[1].strip()
            break

    return LoadedPrompt(
        role=role,
        version=version,
        text=text,
        sha256=canonical_sha256(text),
    )


def tool_schema_hash(tool_names: list[str]) -> str:
    """Hash of the tool set presented to the model (part of fingerprint)."""
    return canonical_sha256(sorted(tool_names))
