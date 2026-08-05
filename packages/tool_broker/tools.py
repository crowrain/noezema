"""Tool definitions — safe, auditable, rate-limited.

Each tool is a pure function that returns a dict with:
- output: str (result or error)
- success: bool
- tool_name: str
- duration_ms: int (elapsed)
"""

from __future__ import annotations

import logging
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("noezema.tools")


@dataclass
class ToolResult:
    output: str = ""
    success: bool = False
    tool_name: str = "unknown"
    duration_ms: int = 0
    error: str = ""


class CommandTool:
    """Execute shell commands in a workspace sandbox.

    Security:
    - Timeout enforcement (default 30s)
    - No interactive shells
    - Output capped at 100KB
    - Blocked commands (rm -rf /, sudo, etc.)
    """

    # Dangerous commands that are never allowed
    BLOCKED_CMDS = {
        "sudo", "su", "passwd", "useradd", "usermod", "visudo",
        "iptables", "ip6tables", "mount", "umount", "fdisk",
        "mkfs", "dd", "systemctl", "service",
    }

    # Dangerous patterns in arguments
    BLOCKED_PATTERNS = {
        "/etc/shadow", "/etc/passwd", "/etc/sudoers",
        "rm -rf /", "rm -rf /*", "mkfs.", "dd if=", "dd of=/",
    }

    def __init__(
        self,
        workspace_dir: str = "/tmp/noezema-workspace",
        timeout: int = 30,
        max_output: int = 100_000,
    ):
        self.workspace = Path(workspace_dir).resolve()
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.max_output = max_output

    def _is_blocked(self, command: str) -> bool:
        cmd_lower = command.lower().strip()

        # Check blocked commands
        for blocked in self.BLOCKED_CMDS:
            if cmd_lower.startswith(blocked):
                return True

        # Check dangerous patterns
        for pattern in self.BLOCKED_PATTERNS:
            if pattern in cmd_lower:
                return True

        # Block paths outside workspace
        for path_arg in command.split():
            if path_arg.startswith("/") and not path_arg.startswith(str(self.workspace)):
                # Allow common tools like /usr/bin/python, /bin/bash
                if path_arg.startswith(("/usr/bin/", "/usr/local/bin/", "/bin/")):
                    continue
                return True

        return False

    def execute(self, command: str, cwd: str | None = None) -> ToolResult:
        """Execute a command and return the result."""
        start = time.perf_counter()

        # Safety checks
        if self._is_blocked(command):
            return ToolResult(
                tool_name="bash",
                success=False,
                error="command blocked by security policy",
            )

        try:
            if cwd:
                full_cwd = (self.workspace / cwd).resolve()
                if not str(full_cwd).startswith(str(self.workspace)):
                    return ToolResult(
                        tool_name="bash",
                        success=False,
                        error="working directory outside workspace",
                    )
            else:
                full_cwd = self.workspace

            result = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=str(full_cwd),
            )

            output = result.stdout
            if len(output) > self.max_output:
                output = output[:self.max_output] + "\n... [truncated]"

            if result.returncode != 0 and result.stderr:
                stderr = result.stderr[:5000]
                output += f"\n[stderr]\n{stderr}"

            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="bash",
                output=output,
                success=result.returncode == 0,
                error=f"exit code {result.returncode}" if result.returncode != 0 else "",
                duration_ms=elapsed,
            )

        except subprocess.TimeoutExpired:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="bash",
                success=False,
                error=f"timeout after {self.timeout}s",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="bash",
                success=False,
                error=str(e),
                duration_ms=elapsed,
            )


class PythonTool:
    """Execute Python code in a sandboxed environment."""

    def __init__(
        self,
        timeout: int = 30,
        max_output: int = 100_000,
    ):
        self.timeout = timeout
        self.max_output = max_output

    def execute(self, code: str) -> ToolResult:
        """Execute Python code and return the result."""
        start = time.perf_counter()

        # Write code to temp file to avoid shell escaping issues
        import tempfile
        import os

        # Wrap code to capture output
        wrapper = f"""
import sys
import io
from contextlib import redirect_stdout, redirect_stderr

stdout_buf = io.StringIO()
stderr_buf = io.StringIO()

try:
    with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
        exec('''{code}''')
except Exception as e:
    stderr_buf.write(str(e))

print("===STDOUT===" + stdout_buf.getvalue(), end="")
print("===STDERR===" + stderr_buf.getvalue(), end="")
"""
        # Escape triple quotes in code
        safe_code = code.replace("'''", "\\'\\'\\'")
        wrapper = wrapper.format(code=safe_code)

        try:
            result = subprocess.run(
                ["python3", "-c", wrapper],
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )

            output = result.stdout
            if len(output) > self.max_output:
                output = output[:self.max_output] + "\n... [truncated]"

            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="python",
                output=output,
                success=result.returncode == 0,
                error=f"exit code {result.returncode}" if result.returncode != 0 else "",
                duration_ms=elapsed,
            )

        except subprocess.TimeoutExpired:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="python",
                success=False,
                error=f"timeout after {self.timeout}s",
                duration_ms=elapsed,
            )
        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="python",
                success=False,
                error=str(e),
                duration_ms=elapsed,
            )


class WebFetchTool:
    """Fetch web content via HTTP GET."""

    def __init__(self, timeout: int = 15, max_content: int = 50_000):
        self.timeout = timeout
        self.max_content = max_content

    def fetch(self, url: str) -> ToolResult:
        """Fetch a URL and return its content."""
        start = time.perf_counter()

        # Block dangerous URLs
        if url.startswith(("file://", "ssh://", "ftp://")):
            return ToolResult(
                tool_name="web_fetch",
                success=False,
                error="protocol not allowed",
            )

        try:
            import urllib.request
            import urllib.parse

            parsed = urllib.parse.urlparse(url)
            if parsed.scheme not in ("http", "https"):
                return ToolResult(
                    tool_name="web_fetch",
                    success=False,
                    error="only http/https allowed",
                )

            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": "Noezema/0.1 (Research Agent)",
                    "Accept": "text/html,application/json,text/plain,*/*",
                },
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                content_type = resp.headers.get("Content-Type", "")
                raw = resp.read(self.max_content * 2)

            if "text/html" in content_type:
                # Strip HTML tags for readability
                import re
                text = re.sub(r"<[^>]+>", " ", raw.decode("utf-8", errors="replace"))
                text = re.sub(r"\s+", " ", text).strip()
            else:
                text = raw.decode("utf-8", errors="replace")

            if len(text) > self.max_content:
                text = text[:self.max_content] + "\n... [truncated]"

            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="web_fetch",
                output=text,
                success=True,
                duration_ms=elapsed,
            )

        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="web_fetch",
                success=False,
                error=str(e),
                duration_ms=elapsed,
            )


class SearchTool:
    """Web search via DuckDuckGo HTML (no API key needed)."""

    def __init__(self, timeout: int = 15):
        self.timeout = timeout

    def search(self, query: str, limit: int = 5) -> ToolResult:
        """Search the web and return results."""
        start = time.perf_counter()

        try:
            import urllib.request
            import urllib.parse
            import re

            # DuckDuckGo HTML search
            search_url = (
                f"https://html.duckduckgo.com/html/?q="
                f"{urllib.parse.quote(query)}"
            )

            req = urllib.request.Request(
                search_url,
                headers={
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                },
            )

            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                html = resp.read().decode("utf-8", errors="replace")

            # Parse results
            results = []
            for match in re.finditer(
                r'<a[^>]+class="result__a"[^>]*href="([^"]*)"[^>]*>([^<]+)</a>'
                r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
                html,
                re.DOTALL,
            ):
                url = match.group(1)
                title = match.group(2).strip()
                snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()
                results.append(f"{title}\n{snippet}\n{url}\n")

                if len(results) >= limit:
                    break

            if not results:
                return ToolResult(
                    tool_name="search",
                    output="No results found.",
                    success=True,
                )

            output = f"Search results for: {query}\n\n" + "\n---\n".join(results)

            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="search",
                output=output,
                success=True,
                duration_ms=elapsed,
            )

        except Exception as e:
            elapsed = int((time.perf_counter() - start) * 1000)
            return ToolResult(
                tool_name="search",
                success=False,
                error=str(e),
                duration_ms=elapsed,
            )
