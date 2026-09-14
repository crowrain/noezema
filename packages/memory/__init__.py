"""Memory model (M3): evidence identity, rules engine, independence,
service."""

from packages.memory.evidence import (
    computation_identity,
    local_observation_identity,
    observation_artifact_hash,
    source_assertion_identity,
)
from packages.memory.independence import group_sources
from packages.memory.rules_engine import ClaimTypeRule, RuleValidationError, evaluate
from packages.memory.service import ClaimView, ClaimViewEvidence, MemoryService

__all__ = [
    "ClaimTypeRule",
    "ClaimView",
    "ClaimViewEvidence",
    "MemoryService",
    "RuleValidationError",
    "computation_identity",
    "evaluate",
    "group_sources",
    "local_observation_identity",
    "observation_artifact_hash",
    "source_assertion_identity",
]
