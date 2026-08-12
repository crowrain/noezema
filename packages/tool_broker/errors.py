"""Sanitized Tool Broker execution errors shared by adapters."""

from __future__ import annotations


class ToolExecutionError(RuntimeError):
    """Adapter failure carrying retry and outcome semantics."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        outcome_known: bool = True,
    ) -> None:
        self.code = code
        self.public_message = message
        self.retryable = retryable
        self.outcome_known = outcome_known
        super().__init__(message)


class ToolDeadlineExceeded(ToolExecutionError):
    def __init__(self) -> None:
        super().__init__(
            "tool_timeout",
            "The bounded tool execution exceeded its policy timeout.",
            retryable=True,
        )
