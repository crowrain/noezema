"""Trusted-host binding of validated decisions to executable actions."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from types import MappingProxyType
from typing import Final, cast

from pydantic import model_validator

from packages.domain._base import ContractModel, JsonObject, Sha256Hex
from packages.domain.decisions import ToolDecision
from packages.domain.enums import ActionState, IdempotencyClass, ToolName
from packages.domain.ids import ActionId, IdempotencyKey, ModelRunId, SessionId

_TOOL_IDEMPOTENCY: Final[Mapping[ToolName, IdempotencyClass]] = MappingProxyType(
    {
        ToolName.WORKSPACE_READ: IdempotencyClass.PURE,
        ToolName.WORKSPACE_LIST: IdempotencyClass.PURE,
        ToolName.WORKSPACE_WRITE: IdempotencyClass.IDEMPOTENT,
        ToolName.ARTIFACT_CREATE: IdempotencyClass.IDEMPOTENT,
        ToolName.MEMORY_SEARCH: IdempotencyClass.PURE,
        ToolName.QUESTION_CREATE: IdempotencyClass.IDEMPOTENT,
        ToolName.MESSAGE_REPLY: IdempotencyClass.IDEMPOTENT,
        ToolName.SHELL_EXECUTE: IdempotencyClass.NON_IDEMPOTENT,
        ToolName.PYTHON_EXECUTE: IdempotencyClass.NON_IDEMPOTENT,
        ToolName.WEB_SEARCH: IdempotencyClass.OBSERVATION,
        ToolName.WEB_FETCH: IdempotencyClass.OBSERVATION,
    }
)


def _canonical_json(value: JsonObject) -> str:
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def canonical_json_sha256(value: JsonObject) -> str:
    """Hash canonical JSON without accepting NaN or representation-dependent spacing."""

    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


class BoundAction(ContractModel):
    """A validated tool decision augmented only by trusted-host fields."""

    action_id: ActionId
    session_id: SessionId
    model_run_id: ModelRunId
    idempotency_key: IdempotencyKey
    idempotency_class: IdempotencyClass
    tool: ToolName
    arguments_json: str
    arguments_sha256: Sha256Hex
    state: ActionState = ActionState.PROPOSED

    @property
    def arguments(self) -> JsonObject:
        """Return a fresh mutable view without exposing the bound representation."""

        value = json.loads(self.arguments_json)
        if not isinstance(value, dict):
            raise ValueError("arguments_json must contain a JSON object")
        return cast(JsonObject, value)

    @model_validator(mode="after")
    def verify_binding(self) -> BoundAction:
        if self.idempotency_class is not _TOOL_IDEMPOTENCY[self.tool]:
            raise ValueError("idempotency_class does not match the trusted tool registry")
        arguments = self.arguments
        if _canonical_json(arguments) != self.arguments_json:
            raise ValueError("arguments_json is not canonical")
        if self.arguments_sha256 != canonical_json_sha256(arguments):
            raise ValueError("arguments_sha256 does not match canonical arguments")
        return self


def bind_action(
    *,
    session_id: SessionId,
    model_run_id: ModelRunId,
    decision: ToolDecision,
) -> BoundAction:
    """Assign action and idempotency identities after schema validation."""

    arguments_json = _canonical_json(decision.arguments)
    arguments_hash = hashlib.sha256(arguments_json.encode("utf-8")).hexdigest()
    return BoundAction(
        action_id=ActionId.new(),
        session_id=session_id,
        model_run_id=model_run_id,
        idempotency_key=IdempotencyKey.new(),
        idempotency_class=_TOOL_IDEMPOTENCY[decision.tool],
        tool=decision.tool,
        arguments_json=arguments_json,
        arguments_sha256=arguments_hash,
    )
