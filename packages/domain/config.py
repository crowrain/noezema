"""Config snapshot identity and the hash-pinned bootstrap payload (T1.3, §14.1).

``payload_sha256`` — canonical hash of the immutable model/embeddings/
prompts/policy/curiosity, budgets/limits and claim-type rules.
``sha256 = H(base_snapshot_id, payload_sha256)`` — identity of a revision.

The bootstrap payload is an immutable literal: it is seeded by the first
migration, verified against its expected hash, and never mutated by
application code.
"""

from __future__ import annotations

import uuid
from typing import Any

from packages.domain.canonical import canonical_json_bytes, canonical_sha256
from packages.domain.models.enums import ActivationMode

QUESTION_UUID5_NAMESPACE = "c0e3d3b6-dd7b-557d-a4d8-6e41049f8468"
"""UUIDv5 namespace for deterministic invalid-assessment question IDs (§8.7.1).
Pinned by migration 0001; never part of a mutable config payload."""

CLOSURE_MANIFEST_UUID5_NAMESPACE = "b3f2a9d1-7c4e-5a8f-9e2d-1c6b8a4f3e70"
"""UUIDv5 namespace for content-addressed closure manifest IDs (T4.2, §8.6):
id = uuid5(namespace, sha256) — the same closure under the same graph
revision always resolves to the same manifest row (dedup by content).
Code-pinned constant, never part of a mutable config payload."""

BOOTSTRAP_SNAPSHOT_ID = uuid.uuid5(uuid.NAMESPACE_URL, "https://github.com/crowrain/noezema/bootstrap-config-snapshot")

BOOTSTRAP_PAYLOAD: dict[str, Any] = {
    "schema_version": 1,
    "model": {
        "provider": "openai-compatible",
        "model_alias": "thinker-local",
        "context_window": 32768,
        "max_output_tokens": 4096,
        "safety_margin_tokens": 2048,
        "structured_output": {"mode": "json_schema", "schema_version": "action-envelope/v1"},
        "sampling": {
            "seed": 42,
            "top_p": 0.95,
            "top_k": 40,
            "temperature_by_phase": {"planning": 0.25, "exploration": 0.6, "verification": 0.15},
        },
    },
    "embeddings": {"enabled": False, "dimensions": None},
    "prompts": {
        "explorer": {"version": "explorer-v2", "path": "prompts/explorer.md"},
        "curator": {"version": "curator-v2", "path": "prompts/curator.md"},
    },
    "policy": {
        "access_profile": "sealed",
        "capabilities": {
            "tools": [
                "workspace.read",
                "workspace.list",
                "workspace.write",
                "artifact.create",
                "memory.search",
                "question.create",
                "message.reply",
                "shell.execute",
                "python.execute",
            ],
            "network": "none",
            "write_paths": ["/workspace"],
        },
        "limits": {
            "command_timeout_seconds": 60,
            "session_timeout_seconds": 1800,
            "max_output_bytes": 1048576,
            "workspace_quota_mb": 512,
        },
    },
    "curiosity": {"selector": "fifo", "epsilon": 0.0, "top_m": 1, "delta": 0.0},
    "token_budgets": {
        "protocol": 4096,
        "identity": 2048,
        "question_plan": 3072,
        "last_session": 2048,
        "claims_evidence": 8192,
        "contradictions": 3072,
        "messages": 2048,
        "recent_errors": 2048,
    },
    "session_limits": {
        "max_explorer_steps": 10,
        "max_claims_assessed_per_session": 32,
        "max_new_claims_per_session": 16,
        "max_evidence_items_per_session": 64,
        "max_new_questions_per_session": 4,
        "phase_deadline_seconds": 600,
        "session_timeout_seconds": 1800,
    },
    "activation_limits": {"offline_activation_max_invalid_questions": 100},
    # Wake scheduling (§5.2.1, T3.29). Owned by the trusted boundary: the
    # sandbox never sees it. ``interval_seconds`` is the base periodic
    # schedule (MVP cron case "every N seconds");
    # ``min_session_interval_seconds`` is the enforced minimum gap between
    # sessions; ``backoff_*`` — exponential backoff after a failed session;
    # ``max_consecutive_failures`` — the node goes to ``paused`` after that
    # many consecutive failed sessions; ``disk_quota_mb`` / ``gpu_required``
    # — wake admission gates.
    "wake_schedule": {
        "interval_seconds": 3600,
        "min_session_interval_seconds": 600,
        "backoff_base_seconds": 60,
        "backoff_multiplier": 2,
        "backoff_max_seconds": 86400,
        "max_consecutive_failures": 3,
        "disk_quota_mb": 1024,
        "gpu_required": False,
    },
    # Reassessment admission (§5.9.1, T4.4). The thresholds are pinned in the
    # config snapshot per spec ("порог допуска, T_escalate, retry budget и
    # SLO фиксируются в конфигурации"). Derived from the 2026-09-14 load
    # series (PLAN M4 thresholds): wake interval 3600 s, session wall P95
    # <= 200 s — "not more than 2 wake intervals behind": both thresholds
    # default to 2 * 3600 = 7200 s.
    # ``t_escalate_seconds`` — a runnable job OLDER than this is treated as
    # dependency-critical regardless of its original reason (no row
    # mutation: the predicate is derived at admission time).
    # ``t_worker_admission_seconds`` — a wake is SKIPPED while the oldest
    # runnable dependency-critical job is older than this: the queue gets
    # the window between sessions and never fights the active one.
    # ``queue_slo_seconds`` — the wall-clock SLO of the runnable queue
    # (depth/age/attempts are operator metrics; a long-non-empty queue is a
    # memory degradation, §5.9.1).
    "reassessment_admission": {
        "t_escalate_seconds": 7200,
        "t_worker_admission_seconds": 7200,
        "queue_slo_seconds": 172800,
    },
    "claim_type_rules": {
        "local_observation": {
            "min_grade_for_supported": "E2",
            "allowed_kinds": ["local_observation"],
            "min_support_evidence": 1,
            "min_independence_groups": 1,
            "requires_scope": True,
            "volatility": "static",
        },
        "computed_result": {
            "min_grade_for_supported": "E2",
            "allowed_kinds": ["computation"],
            "min_support_evidence": 1,
            "min_independence_groups": 1,
            "requires_scope": True,
            "volatility": "static",
        },
        "formal_theorem": {
            "min_grade_for_supported": "E4",
            "allowed_kinds": ["formal_check"],
            "min_support_evidence": 1,
            "min_independence_groups": 1,
            "requires_scope": True,
            "volatility": "static",
        },
        "empirical_conjecture": {
            "min_grade_for_supported": "E3",
            "allowed_kinds": ["experiment_run"],
            "min_support_evidence": 2,
            "min_independence_groups": 2,
            "requires_scope": True,
            "volatility": "static",
        },
        "procedural": {
            "min_grade_for_supported": "E3",
            "allowed_kinds": ["experiment_run", "computation"],
            "min_support_evidence": 2,
            "min_independence_groups": 2,
            "requires_scope": True,
            "volatility": "static",
        },
        "external_fact": {
            "min_grade_for_supported": "E3",
            "allowed_kinds": ["source_assertion", "quote_integrity"],
            "min_support_evidence": 2,
            "min_independence_groups": 2,
            "requires_scope": True,
            "volatility": "configurable",
        },
        "temporal_fact": {
            "min_grade_for_supported": "E3",
            "allowed_kinds": ["source_assertion", "quote_integrity"],
            "min_support_evidence": 2,
            "min_independence_groups": 2,
            "requires_as_of": True,
            "requires_scope": True,
            "volatility": "configurable",
        },
        "self_model": {
            "min_grade_for_supported": "E2",
            "allowed_kinds": ["local_observation"],
            "min_support_evidence": 1,
            "min_independence_groups": 1,
            "requires_scope": False,
            "volatility": "static",
        },
    },
}

BOOTSTRAP_PAYLOAD_SHA256 = canonical_sha256(BOOTSTRAP_PAYLOAD)
"""Expected canonical hash of BOOTSTRAP_PAYLOAD. Migration 0001 re-computes
and aborts on mismatch (fail-closed, §14.1)."""


def config_snapshot_sha256(base_snapshot_id: uuid.UUID | None, payload_sha256: str) -> str:
    """Revision identity: H(base_snapshot_id, payload_sha256) (§14.1)."""
    body = {
        "base_snapshot_id": str(base_snapshot_id) if base_snapshot_id is not None else None,
        "payload_sha256": payload_sha256,
    }
    return canonical_sha256(body)


def bootstrap_snapshot_sha256() -> str:
    return config_snapshot_sha256(None, BOOTSTRAP_PAYLOAD_SHA256)


def bootstrap_payload_bytes() -> bytes:
    return canonical_json_bytes(BOOTSTRAP_PAYLOAD)


def activation_mode_name(mode: ActivationMode) -> str:
    return mode.value
