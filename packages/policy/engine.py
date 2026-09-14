"""Policy engine (T2.5, §5.6, §11.2).

Authorizes each tool decision against the capability profile issued by the
trusted perimeter before the session started. Every decision is recorded
as PolicyEvaluated with the policy version and a closed result:
allow | deny | require_operator.

Checks, in order (first hard failure denies):
  1. tool is known to the registry;
  2. tool is allowed by the profile (T2.6: it is also absent from the
     model schema, so a direct attempt is itself suspicious);
  3. arguments validate against the tool's JSON schema;
  4. path arguments are re-normalized AFTER parsing and must stay inside
     the profile's readable/writable roots (a second normalization must
     be a no-op);
  5. no URL in any string argument while the profile's network is none;
  6. verbatim similarity of arguments with external (untrusted) text is a
     diagnostic signal that upgrades allow to require_operator — it never
     replaces checks 1-5 (§11.2).
"""

from __future__ import annotations

import posixpath
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from pydantic import ValidationError

from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import PolicyDecision
from packages.policy.profiles import CapabilityProfile
from packages.policy.tools import ToolSpec, get_tool

_URL_RE = re.compile(r"https?://[^\s'\"<>]+", re.IGNORECASE)
_MIN_SIMILARITY_LEN = 24
_SIMILARITY_WINDOW = 48


@dataclass(frozen=True)
class PolicyEvaluation:
    tool: str
    decision: PolicyDecision
    reasons: tuple[str, ...]
    arguments_hash: str
    profile_version: str
    similarity_signal: bool = False

    @property
    def allowed(self) -> bool:
        return self.decision is PolicyDecision.ALLOW


@dataclass
class _Acc:
    reasons: list[str] = field(default_factory=list)
    similarity: bool = False

    def deny(self, reason: str) -> None:
        self.reasons.append(reason)


def _has_verbatim_overlap(value: str, external_texts: Sequence[str]) -> bool:
    """Verbatim similarity only (semantic similarity is a later extension)."""
    v = value.strip()
    if len(v) < _MIN_SIMILARITY_LEN:
        return False
    for ext in external_texts:
        if not ext or len(ext) < _MIN_SIMILARITY_LEN:
            continue
        if v in ext:
            return True
        # sliding windows: a window that falls entirely inside an untrusted
        # text means a verbatim chunk was copied into the argument
        step = _SIMILARITY_WINDOW // 3
        for i in range(0, max(1, len(v) - _SIMILARITY_WINDOW + 1), step):
            if v[i : i + _SIMILARITY_WINDOW] in ext:
                return True
    return False


def _check_path(
    raw: str, mode: str, profile: CapabilityProfile, acc: _Acc
) -> None:
    """Re-normalize a container path AFTER parsing and re-check containment.

    Relative paths are resolved against the container's /workspace (the
    model's working directory). normpath is a fixed point by construction,
    so the check is: the NORMALIZED path must stay inside the profile roots
    — a raw "../" trick collapses into an outside path and is denied.
    """
    p = raw if raw.startswith("/") else f"/workspace/{raw}"
    normalized = posixpath.normpath(p)
    roots = profile.writable_paths if mode == "write" else profile.readable_paths
    if not any(normalized == r or normalized.startswith(r.rstrip("/") + "/") for r in roots):
        acc.deny(f"path {raw!r} outside profile {'write' if mode == 'write' else 'read'} roots")


class PolicyEngine:
    def __init__(self, profile: CapabilityProfile) -> None:
        self.profile = profile

    def evaluate(
        self,
        tool: str,
        arguments: JsonDict,
        *,
        external_texts: Sequence[str] = (),
    ) -> PolicyEvaluation:
        args_hash = canonical_sha256(arguments)
        acc = _Acc()
        spec = get_tool(tool)

        if spec is None:
            acc.deny(f"unknown tool: {tool}")
        elif not self.profile.tool_allowed(tool):
            acc.deny(f"tool not allowed by profile '{self.profile.name}': {tool}")
        else:
            try:
                args = spec.args_model.model_validate(arguments)
            except ValidationError as exc:
                for err in exc.errors()[:5]:
                    acc.deny(f"argument {err.get('loc')}: {err.get('msg')}")
            else:
                self._check_paths_and_network(spec, args.model_dump(), acc)
                self._check_similarity(args.model_dump(), external_texts, acc)

        if acc.reasons:
            decision = PolicyDecision.DENY
        elif acc.similarity:
            decision = PolicyDecision.REQUIRE_OPERATOR
        else:
            decision = PolicyDecision.ALLOW

        reasons = tuple(acc.reasons) if acc.reasons else (
            ("argument similarity with external text",) if acc.similarity else ("",)
        )
        return PolicyEvaluation(
            tool=tool,
            decision=decision,
            reasons=reasons,
            arguments_hash=args_hash,
            profile_version=self.profile.policy_version,
            similarity_signal=acc.similarity,
        )

    # ── individual checks ─────────────────────────────────────────────────

    def _check_paths_and_network(
        self, spec: ToolSpec, args: dict[str, Any], acc: _Acc
    ) -> None:
        for arg in spec.path_args:
            if arg in args:
                _check_path(str(args[arg]), spec.path_mode, self.profile, acc)
        if self.profile.network.value == "none":
            for key, value in args.items():
                if isinstance(value, str) and _URL_RE.search(value):
                    acc.deny(
                        f"argument {key!r} carries a URL; network is disabled for this profile"
                    )
                    break

    def _check_similarity(
        self, args: dict[str, Any], external_texts: Sequence[str], acc: _Acc
    ) -> None:
        if not external_texts:
            return
        for value in args.values():
            if isinstance(value, str) and _has_verbatim_overlap(value, external_texts):
                acc.similarity = True
                return
