"""Tests for CuratorLLM — fallback and evidence formatting."""

from packages.domain.services.curator_llm import CuratorLLM


def test_fallback_claims_no_evidence():
    """Fallback returns empty claims when no evidence."""
    curator = CuratorLLM.__new__(CuratorLLM)  # No LLM needed
    result = curator.fallback_claims([], "Test question")
    assert len(result.claims) == 0
    assert "No evidence" in result.summary
    assert len(result.gaps) > 0


def test_fallback_claims_with_evidence():
    """Fallback generates heuristic claim from evidence."""
    curator = CuratorLLM.__new__(CuratorLLM)
    evidence = [
        {"tool": "workspace.read", "result": {"content": "data"}},
        {"tool": "shell.execute", "result": {"output": "ok"}},
    ]
    result = curator.fallback_claims(evidence, "Is the server running?")
    assert len(result.claims) == 1
    assert result.claims[0].claim_type == "operational_observation"
    assert 0.0 <= result.claims[0].confidence <= 1.0
    assert "workspace.read" in result.claims[0].statement


def test_format_evidence():
    """Evidence formatting produces readable text."""
    evidence = [
        {"tool": "read", "result": {"content": "hello"}},
        {"tool": "list", "result": {"entries": ["a", "b"]}},
    ]
    text = CuratorLLM._format_evidence(evidence)
    assert "[1]" in text
    assert "[2]" in text
    assert "read" in text
    assert "list" in text


def test_format_evidence_empty():
    """Empty evidence returns placeholder."""
    text = CuratorLLM._format_evidence([])
    assert "no evidence" in text