"""Tool registry: argument schemas + repeatability classes (T2.6, §5.7).

The registry is the single source of the tool contract:
  - argument JSON-schema validation (pydantic models, extra="forbid");
  - the repeatability class per §5.7;
  - which tools appear in the model's schema (only profile-allowed ones).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.domain.models.enums import IdempotencyClass
from packages.policy.profiles import CapabilityProfile


class _Args(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceReadArgs(_Args):
    path: str = Field(min_length=1, max_length=1000)


class WorkspaceListArgs(_Args):
    path: str = Field(default=".", max_length=1000)


class WorkspaceWriteArgs(_Args):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(default="", max_length=1_000_000)


class PythonExecuteArgs(_Args):
    code: str = Field(min_length=1, max_length=100_000)


class ShellExecuteArgs(_Args):
    command: str = Field(min_length=1, max_length=10_000)


class MemorySearchArgs(_Args):
    query: str = Field(min_length=1, max_length=1000)


class ResearchFetchArgs(_Args):
    url: str = Field(min_length=1, max_length=2000)


class QuestionCreateArgs(_Args):
    text: str = Field(min_length=1, max_length=2000)
    origin: str = Field(default="model_proposal", max_length=50)


class MessageReplyArgs(_Args):
    message_id: str = Field(min_length=1, max_length=64)
    body: str = Field(min_length=1, max_length=2000)


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    idempotency_class: IdempotencyClass
    args_model: type[BaseModel]
    # argument names carrying container paths (re-normalized by the engine)
    path_args: tuple[str, ...] = ()
    path_mode: Literal["read", "write"] = "read"


_TOOLS: dict[str, ToolSpec] = {
    "workspace.read": ToolSpec(
        "workspace.read", "Прочитать файл workspace-а", IdempotencyClass.PURE,
        WorkspaceReadArgs, ("path",), "read",
    ),
    "workspace.list": ToolSpec(
        "workspace.list", "Список файлов workspace-а", IdempotencyClass.PURE,
        WorkspaceListArgs, ("path",), "read",
    ),
    "workspace.write": ToolSpec(
        "workspace.write", "Записать файл в workspace (только overlay сессии)",
        IdempotencyClass.IDEMPOTENT, WorkspaceWriteArgs, ("path",), "write",
    ),
    "python.execute": ToolSpec(
        "python.execute", "Выполнить Python-код в sandbox",
        IdempotencyClass.NON_IDEMPOTENT, PythonExecuteArgs,
    ),
    "shell.execute": ToolSpec(
        "shell.execute", "Выполнить shell-команду в sandbox",
        IdempotencyClass.NON_IDEMPOTENT, ShellExecuteArgs,
    ),
    "memory.search": ToolSpec(
        "memory.search", "Поиск по памяти (M3)", IdempotencyClass.PURE, MemorySearchArgs,
    ),
    "research.fetch": ToolSpec(
        "research.fetch",
        "Загрузить внешнюю страницу через Research Proxy (curated/open_lab; "
        "контент приходит как недоверенные данные)",
        IdempotencyClass.NON_IDEMPOTENT, ResearchFetchArgs,
    ),
    "question.create": ToolSpec(
        "question.create", "Предложить новый вопрос (запись в staging)",
        IdempotencyClass.IDEMPOTENT, QuestionCreateArgs,
    ),
    "message.reply": ToolSpec(
        "message.reply",
        "Ответить на конкретное сообщение оператора по его message_id; "
        "доступно ТОЛЬКО когда в inbox есть такое сообщение (иначе вызов "
        "бесполезен и отклоняется)",
        IdempotencyClass.IDEMPOTENT, MessageReplyArgs,
    ),
}


def get_tool(name: str) -> ToolSpec | None:
    return _TOOLS.get(name)


def all_tools() -> list[ToolSpec]:
    return [_TOOLS[k] for k in sorted(_TOOLS)]


def model_tools_schema(profile: CapabilityProfile) -> dict[str, Any]:
    """The tool schema shown to the model (T2.6): ONLY profile-allowed
    tools appear. A tool missing from the profile must be absent here, so
    the model never learns it exists; a direct call is denied by the
    engine regardless."""
    return {
        "tools": [
            {
                "name": spec.name,
                "description": spec.description,
                "arguments": spec.args_model.model_json_schema(),
            }
            for spec in all_tools()
            if profile.tool_allowed(spec.name)
        ]
    }
