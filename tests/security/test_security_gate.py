"""Security (T7.4, stage 7): the security regression gate.

Covers the gate job itself: ``noezemactl security-gate`` runs the full
``pytest -m security`` suite as a subprocess and exits 0 only if every
security test passes (non-zero = gate failed). The gate is the
mechanism the CI / systemd wire-up calls; this test pins the exit-code
contract. The §16.3 security report (the gate's metric baseline) is
covered by the scenario test (``test_web_metrics.py``).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.security]

REPO_ROOT = Path(__file__).resolve().parents[2]


def _run_gate() -> subprocess.CompletedProcess[str]:
    # hostctl is not a package with __main__; invoke the click app
    # directly through a tiny runner (the CLI is a thin wrapper).
    # The inner pytest is told to ignore THIS file (it would otherwise
    # run the gate inside itself → infinite recursion). The security
    # suite is small (15 tests, <30s) — the timeout is generous.
    runner = (
        "import sys; from hostctl.cli import main; sys.argv = "
        "['noezemactl', 'security-gate']; main()"
    )
    return subprocess.run(
        [sys.executable, "-c", runner],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=300,
        env={
            **__import__("os").environ,
            # the gate's inner pytest must not re-run this file
            "PYTEST_ADDOPTS": "--ignore=tests/security/test_security_gate.py",
        },
    )


def test_security_gate_runs_and_passes() -> None:
    """The gate runs the full security suite and exits 0 (all pass)."""
    proc = _run_gate()
    assert proc.returncode == 0, f"security gate failed:\n{proc.stdout}\n{proc.stderr}"
    assert "security gate: PASSED" in proc.stdout
