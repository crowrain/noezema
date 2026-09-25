"""Unit: T7.46a — the evidence boundary masks NUL bytes.

The source (executor) masks NUL at capture; ``observation_to_evidence``
is the defensive EVIDENCE boundary: it must guarantee that no NUL
reaches the durable payload AND that the identity hash is computed over
the SAME (masked) value that lands in the payload — otherwise the stored
value and the hash input diverge (the M3 host recomputes the durable
identity from the payload at the commit boundary).
"""

from __future__ import annotations

import pytest

from apps.orchestrator.evidence import observation_to_evidence
from apps.orchestrator.executor import Observation
from packages.domain.canonical import canonical_sha256
from packages.domain.sanitization import NUL_MARKER

# a NUL inside BOTH the payload cap (2000) and the identity cap (4000)
RAW_STDOUT = "a" * 1500 + "\x00" + "b" * 500
CODE = "print('x')"


@pytest.mark.unit
def test_python_execute_evidence_masks_nul_and_hashes_masked_value() -> None:
    obs = Observation(
        tool="python.execute", ok=True, data={"exit_code": 0, "stdout": RAW_STDOUT, "stderr": ""}
    )
    rec = observation_to_evidence(obs, {"code": CODE})
    assert rec is not None
    # no NUL survives into the durable payload; the marker is visible
    assert "\x00" not in str(rec.payload)
    masked = RAW_STDOUT.replace("\x00", NUL_MARKER)
    assert rec.payload["stdout"] == masked[:2000]
    assert rec.payload["stdout"].count(NUL_MARKER) == 1
    # the identity is computed over the MASKED stdout — the same value
    # family that ends up in the payload (no divergence between the
    # stored value and the hash input)
    assert rec.identity_hash == canonical_sha256(
        {"tool": "python.execute", "code": CODE, "exit_code": 0, "stdout": masked[:4000]}
    )


@pytest.mark.unit
def test_python_execute_evidence_clean_output_identity_unchanged() -> None:
    """No behavior change for NUL-free output: the identity over clean
    stdout is exactly what the pre-T7.46a code computed."""
    clean = "c" * 3000
    obs = Observation(
        tool="python.execute", ok=True, data={"exit_code": 0, "stdout": clean, "stderr": ""}
    )
    rec = observation_to_evidence(obs, {"code": CODE})
    assert rec is not None
    assert rec.identity_hash == canonical_sha256(
        {"tool": "python.execute", "code": CODE, "exit_code": 0, "stdout": clean[:4000]}
    )
    assert rec.payload["stdout"] == clean[:2000]


@pytest.mark.unit
def test_workspace_read_evidence_masks_nul_in_content() -> None:
    obs = Observation(tool="workspace.read", ok=True, data={"path": "n.txt", "content": "x\x00y"})
    rec = observation_to_evidence(obs, {"path": "n.txt"})
    assert rec is not None
    masked = f"x{NUL_MARKER}y"
    assert rec.payload["content"] == masked
    assert rec.identity_hash == canonical_sha256(
        {"tool": "workspace.read", "path": "n.txt", "content": masked[:4000]}
    )
