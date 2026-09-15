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
        # T5.2 (stage 4): the planner role (multi-step planning)
        "planner": {"version": "planner-v1", "path": "prompts/planner.md"},
        # T5.3 (stage 4): the verifier role (deterministic checks;
        # never assigns grade/confidence — §3.7)
        "verifier": {"version": "verifier-v1", "path": "prompts/verifier.md"},
        # T5.5 (stage 4): the extraction profile (untrusted documents,
        # §11.2) — a model without tools extracts verbatim chunks
        "extractor": {"version": "extractor-v1", "path": "prompts/extractor.md"},
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
    # T5.2 (stage 4): multi-step planning. mode "template" = MVP fixed
    # template plan (the default, unchanged behavior); "llm" = the
    # planner role proposes a structured plan (schema-validated by the
    # host; an invalid proposal falls back to the template, never a
    # session failure). max_steps caps the plan against the session
    # step budget.
    "planning": {"mode": "template", "max_steps": 10},
    # T5.3 (stage 4): the verifier role in the verifying phase.
    # mode "off" = MVP no-op (the default, unchanged behavior);
    # "llm" = the verifier proposes a structured report of organized
    # deterministic checks (host-validated; an invalid/over-budget
    # report falls back to the no-op phase, never a session failure).
    # The report never carries a grade/confidence (§3.7).
    "verification": {"mode": "off", "max_checks": 8},
    # T5.4 (stage 4): protection against semantic repetition (§9).
    # enabled=false keeps the MVP selection unchanged; rephrase =
    # Jaccard over the host word set (deterministic, no LLM); a cycle
    # = rephrase of an already-investigated question + no_progress
    # consecutive sessions without a new claim → a strategy from the
    # closed §9 list (deterministic rotation, audited).
    "repetition": {
        "enabled": False,
        "rephrase_threshold": 0.6,
        "plan_cycle_threshold": 0.5,
        "no_progress_limit": 2,
    },
    # T5.5 (stage 4): untrusted extraction profile (§11.2, §10.1).
    # mode "off" = MVP raw read (the default); "llm" = a document
    # read of >= min_document_bytes is first passed to the extractor
    # (a model without tools), which must return verbatim quotes;
    # the explorer then receives only the extracted chunks with
    # host-computed provenance. A non-verbatim or over-budget report
    # falls back to the raw read (audited, never a session failure).
    # Extraction shrinks the injection surface but does not make the
    # text trusted.
    "extraction": {"mode": "off", "min_document_bytes": 500, "max_chunks": 8},
    # T6.1 (stage 5): the research proxy — the ONLY egress to the
    # network (§5.12). mode "sealed" = no egress at all (the default:
    # the Sealed mode, only the local index); "curated"/"open_lab"
    # (T6.2) unlock network fetches through the proxy. Every fetched
    # page is stored original + normalized + hash, marked untrusted
    # external content, and logged in the provenance journal.
    "research_proxy": {
        "mode": "sealed",
        "max_response_bytes": 1048576,
        "max_redirects": 3,
        "timeout_seconds": 10,
        "user_agent": "noezema-research-proxy/1.0",
        "private_allowlist": [],
        # T6.2 (stage 5, §5.12.1): mode backends. sealed = local index
        # only (no egress); curated = SearXNG through the proxy
        # (upstream log + rate limits); open_lab = fetch restricted to
        # the closed allowed_domains list under the open_lab profile.
        "searxng_url": None,
        "allowed_domains": [],
        "rate_limit_max": 20,
        "rate_limit_window_seconds": 3600,
    },
    # T5.1 (§5.3.1): the selector is config-driven — the MVP default stays
    # FIFO; a config change to "curiosity" enables the score-based ranking
    # (weights, thresholds, ε/M/δ and the similarity fingerprint below are
    # part of the session config snapshot).
    "curiosity": {
        "selector": "fifo",
        "epsilon": 0.0,
        "top_m": 1,
        "delta": 0.0,
        "recency_sessions": 5,
        "weights": {
            "novelty": 0.3,
            "coverage_gap": 0.2,
            "evidenceability": 0.2,
            "feasibility": 0.1,
            "cost": 0.1,
            "risk": 0.0,
            "topic_recency": 0.1,
        },
        "normalization": {
            "gap_debt_threshold": 4.0,
            "feasibility_word_limit": 200.0,
            "cost_word_threshold": 400.0,
            "topic_overlap_threshold": 0.34,
            "min_word_len": 3.0,
        },
        "similarity": {"fingerprint": "token-jaccard-v1"},
    },
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
    "activation_limits": {
        "offline_activation_max_invalid_questions": 100,
        # T4.5 (§8.7.2): the online post-publish manifest bound (the flip
        # refuses to publish when the pending-head question backlog would
        # exceed it — the rules change invalidates too much at once) and
        # the post-publish/repair retry budget
        "online_activation_max_pending_questions": 100,
        "online_activation_max_attempts": 78,
    },
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
    # T4.5 (§8.7.2, §5.2.1): the effective runnable repair backlog (a
    # post_publish_blocked candidate with a due cursor) has a wall-clock
    # SLO; the wake is skipped while its age exceeds T_repair_admission
    # (post_publish_blocked does NOT block the wake immediately — the
    # threshold gives the repair runner its normal window).
    "repair_admission": {
        "t_repair_admission_seconds": 7200,
        "repair_slo_seconds": 172800,
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
            # §8.7.3: E3 — только independent replication (независимая
            # реализация protocol/implementation + независимый dataset
            # lineage), а не просто два разных окружения
            "required_independence": "independent_replication",
            "requires_scope": True,
            "volatility": "static",
        },
        "procedural": {
            "min_grade_for_supported": "E3",
            "allowed_kinds": ["experiment_run", "computation"],
            "min_support_evidence": 2,
            "min_independence_groups": 2,
            "required_independence": "independent_replication",
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
