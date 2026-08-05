"""Sandbox Executor — workspace isolation (§5.8).

MVP: file-system sandbox with path traversal protection.
Later: rootless Podman/Docker container.
"""

import os
from pathlib import Path


class SandboxExecutor:
    """Execute operations in a workspace sandbox."""

    def __init__(self, workspace_dir: str = "/tmp/noezema-workspace"):
        self.workspace_dir = Path(workspace_dir).resolve()
        self.workspace_dir.mkdir(parents=True, exist_ok=True)

    def _safe_path(self, path: str) -> Path:
        """Resolve path and ensure it's within workspace."""
        full = (self.workspace_dir / path).resolve()
        if not str(full).startswith(str(self.workspace_dir)):
            raise SandboxError("path traversal blocked")
        return full

    def read_file(self, path: str) -> str:
        full = self._safe_path(path)
        if not full.exists():
            return ""
        return full.read_text()[:50000]

    def write_file(self, path: str, content: str) -> int:
        full = self._safe_path(path)
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content)
        return len(content)

    def list_dir(self, path: str = ".") -> list[str]:
        full = self._safe_path(path)
        if not full.is_dir():
            return []
        return [str(p.relative_to(full)) for p in sorted(full.iterdir())]


class SandboxError(Exception):
    pass
