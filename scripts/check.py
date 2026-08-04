"""Run the complete local and CI validation suite."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SOURCE_PATHS = ("apps", "packages", "scripts", "tests")


def _run(*arguments: str) -> None:
    command = [sys.executable, *arguments]
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=PROJECT_ROOT, check=True)


def main() -> None:
    _run("-m", "ruff", "check", *SOURCE_PATHS)
    _run("-m", "ruff", "format", "--check", *SOURCE_PATHS)
    _run("-m", "compileall", "-q", *SOURCE_PATHS)
    _run("-m", "pytest")


if __name__ == "__main__":
    main()
