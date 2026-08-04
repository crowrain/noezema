"""Shared primitives for strict external and trusted-host contracts."""

from __future__ import annotations

from typing import Annotated, TypeAlias

from pydantic import BaseModel, ConfigDict, JsonValue, StringConstraints


class ContractModel(BaseModel):
    """Base model that rejects protocol drift and accidental mutation."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
        str_strip_whitespace=True,
    )


NonEmptyText: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=4096),
]
ShortReason: TypeAlias = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]
Sha256Hex: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
]
JsonObject: TypeAlias = dict[str, JsonValue]
