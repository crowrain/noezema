"""Verify that the initial application and package boundaries are importable."""

from __future__ import annotations

import importlib
import tomllib
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
PUBLIC_MODULES = (
    "apps.orchestrator",
    "packages.domain",
    "packages.llm_gateway",
    "packages.memory",
    "packages.persistence",
)


class ProjectScaffoldTest(unittest.TestCase):
    def test_public_modules_are_importable(self) -> None:
        for module_name in PUBLIC_MODULES:
            with self.subTest(module=module_name):
                module = importlib.import_module(module_name)
                self.assertEqual(module.__name__, module_name)

    def test_project_metadata_names_noezema(self) -> None:
        with (PROJECT_ROOT / "pyproject.toml").open("rb") as pyproject_file:
            pyproject = tomllib.load(pyproject_file)

        self.assertEqual(pyproject["project"]["name"], "noezema")
        self.assertEqual(pyproject["project"]["requires-python"], ">=3.11")


if __name__ == "__main__":
    unittest.main()
