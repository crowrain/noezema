"""Tool observation — the result of one tool execution (T2.7).

Shared by the dev stub executor and the sandboxed Tool Broker so the
orchestrator sees one contract regardless of the backend.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from packages.domain.models.base import JsonDict
from packages.domain.models.enums import IdempotencyClass


@dataclass(slots=True)
class Observation:
    tool: str
    ok: bool
    data: JsonDict = field(default_factory=dict)
    error: str | None = None
    # True when the failure is an infrastructure failure (engine, sandbox,
    # I/O) rather than a tool result (e.g. a nonzero exit code). Only
    # transient failures are candidates for retry under the class policy.
    transient: bool = False
    # set when the idempotency key matched an already-completed action
    replayed: bool = False
    # the repeatability class of the executed tool (§5.7)
    idempotency_class: IdempotencyClass | None = None
    # which attempt produced this observation (0-based)
    attempt: int = 0
    note: str | None = None
