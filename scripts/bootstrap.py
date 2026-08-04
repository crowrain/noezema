"""Create the local development environment for NOEZEMA."""

from __future__ import annotations

import argparse
import os
import subprocess
import venv
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _environment_python(environment_dir: Path) -> Path:
    if os.name == "nt":
        return environment_dir / "Scripts" / "python.exe"
    return environment_dir / "bin" / "python"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--venv",
        type=Path,
        default=Path(".venv"),
        help="virtual environment path relative to the project root (default: .venv)",
    )
    args = parser.parse_args()

    environment_dir = args.venv
    if not environment_dir.is_absolute():
        environment_dir = PROJECT_ROOT / environment_dir

    venv.EnvBuilder(with_pip=True).create(environment_dir)
    environment_python = _environment_python(environment_dir)
    subprocess.run(
        [
            str(environment_python),
            "-m",
            "pip",
            "install",
            "--editable",
            f"{PROJECT_ROOT}[dev]",
        ],
        cwd=PROJECT_ROOT,
        check=True,
    )

    print(f"Development environment is ready: {environment_python}")


if __name__ == "__main__":
    main()
