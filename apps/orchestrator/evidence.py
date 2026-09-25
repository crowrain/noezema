"""Host-side adapter: typed observation → evidence record (T1.18).

The model never decides evidence identity or kind: the trusted host maps
each successful tool observation to a typed, hashed record. Failed
observations and host-deferred tools (question.create, message.reply)
produce no evidence.

``research.fetch`` maps to a ``source_assertion`` record (EVAL-3
precondition, ADR-0006 rev): the fetched page is an external source's
assertion about the world. Identity = ``source_assertion_identity``
over the ORIGINAL content hash + chunk + kind (stable across re-fetches
of the same content), provenance = the durable ``sources`` row
(``source_id`` / ``chunk_id``) — the source-independence graph groups
the evidence by registrable domain, which is what the external/temporal
claim rules (≥2 independent groups, E3) require.

T7.8 (EVAL-3b post-mortem P.1, §6.4): the payload additionally carries
``assertion_text`` — the fragment of the NORMALIZED chunk text the
assertion is read from. Without it the curator (a fresh chat call that
never saw the explorer's fenced content) could only meta-claim about
the URL; with it the claim statement is grounded in the source text.
The fragment is budgeted explicitly (``SOURCE_ASSERTION_TEXT_BUDGET``
chars) and is NOT part of the identity: identity stays over the
original content hash, so re-fetches of identical content dedupe
exactly as before.

T7.16 (EVAL-3b residual defect, §6.4): the fragment is no longer the
LEADING prefix of the normalized text — on most full pages that is
navigation, and the fact sits deeper (measured: up to ~6.7k chars in).
The window is now QUESTION-DEPENDENT: ``assertion_window.
select_assertion_window`` finds the region of the normalized text with
the highest density of the question/plan's content terms and cuts the
budgeted window around it (same budget — the fragment rides in the
curator's claims_evidence section, 8192-token cap). The question and
the plan ride in the observation data (the host adds them; the model
never supplies them) and are NOT part of the identity either.

T7.22 (EVAL-3d group A, ADR-0011, §6.4): the fragment can carry up to
TWO non-overlapping budget windows joined by a " […]" separator — the
T7.16 term-density window plus a value window (the fact-zone region
where a data value co-occurs with the question's terms). The single
term window missed the assertion in 7 of the 12 measured group-A cases
(lead/infobox, first paragraph, data widget); the total fragment is
bounded by ``SOURCE_ASSERTION_TEXT_BUDGET * SOURCE_ASSERTION_MAX_WINDOWS
+ separator`` and still fits the curator's claims_evidence budget with
margin (ADR-0011 §4 — two evidence of one claim ≈ 2.6–2.9k tokens of
the 8192-token section).
"""

from __future__ import annotations

from apps.orchestrator.assertion_window import select_assertion_windows
from apps.orchestrator.executor import Observation
from packages.domain.canonical import canonical_sha256
from packages.domain.models.base import JsonDict
from packages.domain.models.enums import EvidenceKind
from packages.domain.sanitization import mask_nul, mask_nul_deep
from packages.domain.schemas.evidence import EvidenceRecord
from packages.memory.evidence import source_assertion_identity

#: T7.8 (EVAL-3b post-mortem P.1, §6.4): the explicit budget of the
#: assertion-text fragment carried in a source_assertion payload
#: (characters of the normalized chunk text). The curator must see the
#: fragment the claim is grounded in, not only the URL and the hashes;
#: the fragment is bounded so the role prompts and the DB payload stay
#: predictable. It is NOT part of the identity (see the module docstring).
SOURCE_ASSERTION_TEXT_BUDGET = 2_000

#: T7.22 (ADR-0011): the fragment carries up to this many NON-OVERLAPPING
#: budget windows (the T7.16 term-density window + the value window),
#: joined by a separator. Two windows keep the total fragment at
#: ≤ 2*SOURCE_ASSERTION_TEXT_BUDGET + separator chars, which still fits
#: the curator's claims_evidence section (8192 tokens) with margin —
#: the worst case of TWO source_assertion evidence on ONE claim is
#: measured in tests/unit/test_assertion_window.py::
#: test_two_evidence_fragments_fit_the_claims_evidence_budget.
SOURCE_ASSERTION_MAX_WINDOWS = 2

#: T7.22: separator between the two fragment windows (the curator sees
#: a continuous fragment with a visible gap marker, not two disjoint
#: slices). 5 chars.
_FRAGMENT_SEPARATOR = "\n[…]\n"


def _finalize(record: EvidenceRecord) -> EvidenceRecord:
    """T7.46a: the EVIDENCE boundary invariant — no NUL byte in any
    string of the durable payload. The source strings are already masked
    above (idempotent); this is the defense-in-depth pass so a future
    payload field cannot carry a NUL into the JSONB (SMOKE-V12-K2 §3)."""
    record.payload = mask_nul_deep(record.payload)
    return record


def observation_to_evidence(observation: Observation, arguments: JsonDict) -> EvidenceRecord | None:
    if not observation.ok:
        return None

    tool = observation.tool
    if tool == "python.execute":
        data = observation.data
        # T7.46a: mask BEFORE the caps — the identity and the payload are
        # computed from the SAME masked value, so the stored value and the
        # hash input cannot diverge (the trusted host recomputes the
        # durable identity from the payload at the commit boundary). The
        # source (executor) already masks at capture; this covers every
        # other capture path (SMOKE-V12-K2 §3.4).
        stdout = mask_nul(str(data.get("stdout", "")))
        code = mask_nul(str(arguments.get("code", "")))
        identity = canonical_sha256(
            {"tool": tool, "code": code, "exit_code": data.get("exit_code"), "stdout": stdout[:4000]}
        )
        return _finalize(
            EvidenceRecord(
                kind=EvidenceKind.COMPUTATION,
                identity_hash=identity,
                payload={
                    "exit_code": data.get("exit_code"),
                    "stdout": stdout[:2000],
                    # the exact input (M3: the trusted host recomputes the
                    # durable identity from result + inputs + tool fingerprint)
                    "code": code[:4000],
                },
                note="python.execute (M1 stub executor)",
            )
        )

    if tool == "workspace.read":
        # T7.46a: mask before the caps (same rule as python.execute)
        content = mask_nul(str(observation.data.get("content", "")))
        path = mask_nul(str(arguments.get("path", "")))
        identity = canonical_sha256(
            {"tool": tool, "path": path, "content": content[:4000]}
        )
        return _finalize(
            EvidenceRecord(
                kind=EvidenceKind.LOCAL_OBSERVATION,
                identity_hash=identity,
                payload={"path": mask_nul(str(observation.data.get("path", ""))), "content": content[:2000]},
            )
        )

    if tool == "workspace.list":
        # T7.46a: mask the entry names (defensive — POSIX filenames
        # cannot carry NUL, but the boundary invariant covers all)
        entries = mask_nul_deep(observation.data.get("entries", []))
        path = mask_nul(str(arguments.get("path", "")))
        identity = canonical_sha256(
            {"tool": tool, "path": path, "entries": entries}
        )
        return _finalize(
            EvidenceRecord(
                kind=EvidenceKind.LOCAL_OBSERVATION,
                identity_hash=identity,
                payload={"entries": entries[:100]},
            )
        )

    if tool == "research.fetch":
        # EVAL-3 precondition (ADR-0006 rev): the only session path that
        # produces source-based evidence. The envelope data carries the
        # durable source reference (the proxy registered the sources row
        # in its own transaction); identity is over the ORIGINAL
        # content hash so a re-fetch of identical content dedupes, and
        # the truncated context text is NOT part of the identity (the
        # assertion is the source's, not the truncation's).
        data = observation.data or {}
        # T7.46a: mask the provenance strings (defensive — the hashes are
        # hex and the id is a UUID, but the boundary invariant covers all)
        osha = mask_nul(str(data.get("original_sha256", "")))
        source_id = mask_nul(str(data.get("source_id", "")))
        if not osha or not source_id or len(source_id) != 36:
            return None  # no durable source reference → no provenance
        chunk_id = mask_nul(str(data.get("chunk_id", "chunk-0")))
        identity = source_assertion_identity(osha, chunk_id, EvidenceKind.SOURCE_ASSERTION.value)
        # T7.8 (§6.4): the fragment of the normalized chunk text the
        # assertion is read from — bounded by SOURCE_ASSERTION_TEXT_BUDGET,
        # NOT part of the identity (identity stays over the original
        # content hash, so dedupe semantics are unchanged).
        #
        # T7.16: the fragment is QUESTION-DEPENDENT (assertion_window
        # module) — the budgeted window around the densest region of the
        # question/plan's content terms, not the leading prefix. The
        # host adds question/plan to the observation data; their absence
        # (or no term match) falls back to the leading prefix — the T7.8
        # behavior.
        #
        # T7.22 (ADR-0011): up to SOURCE_ASSERTION_MAX_WINDOWS
        # non-overlapping budget windows (the term-density window + the
        # value window) joined by _FRAGMENT_SEPARATOR — EVAL-3d group A
        # showed the single term window misses the assertion in 7 of 12
        # cases (lead/infobox, first paragraph, data widget). The joined
        # text is NOT part of the identity (as the single window was).
        normalized_text = mask_nul(str(data.get("normalized_text", "")))
        windows = select_assertion_windows(
            normalized_text,
            f"{data.get('question', '')}\n{data.get('plan', '')}",
            SOURCE_ASSERTION_TEXT_BUDGET,
            max_windows=SOURCE_ASSERTION_MAX_WINDOWS,
        )
        assertion_text = _FRAGMENT_SEPARATOR.join(w.text for w in windows)
        return _finalize(
            EvidenceRecord(
                kind=EvidenceKind.SOURCE_ASSERTION,
                identity_hash=identity,
                payload={
                    "url": mask_nul(str(data.get("url", "")))[:500],
                    "original_sha256": osha,
                    "normalized_sha256": mask_nul(str(data.get("normalized_sha256", ""))),
                    "chunk_id": chunk_id,
                    "assertion_text": assertion_text,
                    # the exact input (the trusted host recomputes identity
                    # from the provenance, not from this payload)
                    "url_arg": mask_nul(str(arguments.get("url", "")))[:500],
                },
                source_id=source_id,
                chunk_id=chunk_id,
                note="research.fetch (untrusted external source)",
            )
        )

    return None  # memory.search / question.create / message.reply / failures
