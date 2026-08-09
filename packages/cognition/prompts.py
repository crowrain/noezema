"""Immutable prompt snapshots supplied by a runtime configuration snapshot."""

from __future__ import annotations

import hashlib
from enum import StrEnum
from pathlib import Path
from typing import Annotated

from pydantic import ConfigDict, StringConstraints, model_validator

from packages.domain._base import ContractModel, Sha256Hex

PromptText = Annotated[str, StringConstraints(min_length=1, max_length=100_000)]
PromptVersion = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=96),
]


class PromptKind(StrEnum):
    IDENTITY = "identity"
    EXPLORER = "explorer"
    CURATOR = "curator"


class PromptSnapshot(ContractModel):
    """Exact UTF-8 prompt bytes bound to a configured version and digest."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=False,
    )

    kind: PromptKind
    version: PromptVersion
    sha256: Sha256Hex
    content: PromptText

    @model_validator(mode="after")
    def verify_content_digest(self) -> PromptSnapshot:
        raw = self.content.encode("utf-8")
        if "\r" in self.content or not self.content.endswith("\n"):
            raise ValueError("prompt content must use LF and end with one LF")
        if hashlib.sha256(raw).hexdigest() != self.sha256:
            raise ValueError("prompt content does not match its digest")
        return self

    @classmethod
    def load(
        cls,
        path: Path,
        *,
        kind: PromptKind,
        version: str,
        expected_sha256: str,
    ) -> PromptSnapshot:
        """Load only the exact prompt approved by the active config snapshot."""

        raw = path.read_bytes()
        if raw.startswith(b"\xef\xbb\xbf"):
            raise ValueError("prompt must be UTF-8 without a byte-order mark")
        if b"\r" in raw:
            raise ValueError("prompt must use LF line endings")
        if not raw.endswith(b"\n"):
            raise ValueError("prompt must end with one LF")
        try:
            content = raw.decode("utf-8")
        except UnicodeDecodeError as error:
            raise ValueError("prompt must be valid UTF-8") from error

        actual_sha256 = hashlib.sha256(raw).hexdigest()
        if actual_sha256 != expected_sha256:
            raise ValueError(
                f"prompt digest mismatch: expected {expected_sha256}, got {actual_sha256}"
            )
        return cls(
            kind=kind,
            version=version,
            sha256=actual_sha256,
            content=content,
        )


class PromptBundle(ContractModel):
    """Identity plus exactly one role prompt used for an invocation."""

    identity: PromptSnapshot
    role: PromptSnapshot

    @model_validator(mode="after")
    def validate_kinds(self) -> PromptBundle:
        if self.identity.kind is not PromptKind.IDENTITY:
            raise ValueError("identity snapshot must have kind=identity")
        if self.role.kind not in {PromptKind.EXPLORER, PromptKind.CURATOR}:
            raise ValueError("role snapshot must have kind=explorer or kind=curator")
        return self

    @property
    def version(self) -> str:
        """Human-readable composition recorded with the invocation."""

        return f"{self.identity.version}+{self.role.version}"

    @property
    def system_prompt(self) -> str:
        """Return the exact deterministic system prompt sent to the model."""

        return f"{self.identity.content}\n{self.role.content}"

    @property
    def sha256(self) -> str:
        """Fingerprint the composed prompt bytes, not its filesystem path."""

        return hashlib.sha256(self.system_prompt.encode("utf-8")).hexdigest()
