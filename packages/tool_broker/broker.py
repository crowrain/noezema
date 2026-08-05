"""Tool Broker — registry, rate limiting, audit logging.

Orchestrator calls tools via the broker. The broker:
1. Validates the tool exists
2. Checks rate limits
3. Executes the tool
4. Logs the call to audit DB
5. Returns structured result
"""

from __future__ import annotations

import logging
import time
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from packages.tool_broker.tools import (
    CommandTool,
    PythonTool,
    WebFetchTool,
    SearchTool,
    ToolResult,
)

logger = logging.getLogger("noezema.broker")


@dataclass
class ToolConfig:
    """Configuration for a single tool."""
    name: str
    description: str
    enabled: bool = True
    rate_limit: int = 10  # calls per window
    rate_window: int = 60  # seconds


class ToolBroker:
    """Registry and executor for AI tools.

    Usage:
        broker = ToolBroker(workspace="/tmp/noezema")
        result = await broker.call("bash", command="ls -la")
    """

    # Default tool registry
    DEFAULT_TOOLS: dict[str, ToolConfig] = {
        "bash": ToolConfig(
            name="bash",
            description="Execute shell commands in workspace sandbox",
            rate_limit=15,
            rate_window=60,
        ),
        "python": ToolConfig(
            name="python",
            description="Execute Python code in isolated environment",
            rate_limit=10,
            rate_window=60,
        ),
        "web_fetch": ToolConfig(
            name="web_fetch",
            description="Fetch web content via HTTP GET",
            rate_limit=20,
            rate_window=60,
        ),
        "search": ToolConfig(
            name="search",
            description="Search the web via DuckDuckGo",
            rate_limit=10,
            rate_window=60,
        ),
    }

    def __init__(
        self,
        workspace_dir: str = "/tmp/noezema-workspace",
        tools: dict[str, ToolConfig] | None = None,
    ):
        self.tools = tools or dict(self.DEFAULT_TOOLS)
        self.executors = self._init_executors(workspace_dir)
        self._rate_tracker: dict[str, list[float]] = defaultdict(list)
        self._audit_log: list[dict] = []

    def _init_executors(self, workspace_dir: str) -> dict[str, Any]:
        """Initialize tool executors."""
        return {
            "bash": CommandTool(workspace_dir=workspace_dir),
            "python": PythonTool(),
            "web_fetch": WebFetchTool(),
            "search": SearchTool(),
        }

    def list_tools(self) -> list[dict]:
        """Return available tools with their descriptions."""
        return [
            {
                "name": name,
                "description": config.description,
                "enabled": config.enabled,
            }
            for name, config in self.tools.items()
            if config.enabled
        ]

    def _check_rate_limit(self, tool_name: str) -> bool:
        """Check if tool is within rate limits."""
        config = self.tools.get(tool_name)
        if not config:
            return True  # Unknown tool, skip rate limit

        now = time.time()
        window_start = now - config.rate_window

        # Clean old entries
        self._rate_tracker[tool_name] = [
            t for t in self._rate_tracker[tool_name] if t > window_start
        ]

        return len(self._rate_tracker[tool_name]) < config.rate_limit

    def _record_rate(self, tool_name: str):
        """Record a tool call for rate limiting."""
        self._rate_tracker[tool_name].append(time.time())

    def call(
        self,
        tool_name: str,
        **kwargs,
    ) -> ToolResult:
        """Execute a tool call with rate limiting and audit.

        Args:
            tool_name: One of "bash", "python", "web_fetch", "search"
            **kwargs: Tool-specific arguments
        """
        start = time.perf_counter()

        # Validate tool exists
        if tool_name not in self.tools:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"unknown tool: {tool_name}. Available: {', '.join(self.tools.keys())}",
            )

        config = self.tools[tool_name]

        # Check if enabled
        if not config.enabled:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"tool disabled: {tool_name}",
            )

        # Check rate limit
        if not self._check_rate_limit(tool_name):
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"rate limit exceeded: {tool_name} ({config.rate_limit}/{config.rate_window}s)",
            )

        # Execute
        executor = self.executors.get(tool_name)
        if not executor:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"no executor for: {tool_name}",
            )

        try:
            if tool_name == "bash":
                result = executor.execute(kwargs.get("command", "echo 'no command'"))
            elif tool_name == "python":
                result = executor.execute(kwargs.get("code", "pass"))
            elif tool_name == "web_fetch":
                result = executor.fetch(kwargs.get("url", ""))
            elif tool_name == "search":
                result = executor.search(
                    kwargs.get("query", ""),
                    kwargs.get("limit", 5),
                )
            else:
                result = ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error="unsupported tool",
                )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            result = ToolResult(
                tool_name=tool_name,
                success=False,
                error=str(e),
                duration_ms=elapsed,
            )

        # Record rate
        self._record_rate(tool_name)

        # Audit log
        audit_entry = {
            "id": str(uuid.uuid4()),
            "tool": tool_name,
            "args": {k: str(v)[:500] for k, v in kwargs.items()},
            "success": result.success,
            "error": result.error,
            "duration_ms": result.duration_ms,
            "timestamp": datetime.utcnow().isoformat(),
        }
        self._audit_log.append(audit_entry)
        logger.info(
            "Tool call: %s → %s (%dms)",
            tool_name,
            "OK" if result.success else "FAIL",
            result.duration_ms,
        )

        return result

    def get_audit_log(
        self,
        tool_name: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Return recent audit entries."""
        entries = self._audit_log
        if tool_name:
            entries = [e for e in entries if e["tool"] == tool_name]
        return entries[-limit:]
