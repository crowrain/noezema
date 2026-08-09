"""Deterministic adapter from Curator references to evidence staging proposals."""

from __future__ import annotations

from collections import defaultdict

from packages.cognition.protocols import CuratorProposal, EvidenceRelation
from packages.domain import (
    DuplicateEvidenceReference,
    EvidenceAdapterBudget,
    EvidenceAdapterResult,
    EvidenceProposal,
    EvidenceUse,
    ObservationId,
    TypedObservation,
    evidence_identity_sha256,
)


class EvidenceAdapterError(ValueError):
    """Base class for a rejected all-or-nothing adapter invocation."""

    code = "evidence_adapter_error"


class DuplicateObservationIdError(EvidenceAdapterError):
    code = "duplicate_observation_id"

    def __init__(self, observation_id: ObservationId) -> None:
        self.observation_id = observation_id
        super().__init__(f"duplicate trusted observation ID: {observation_id}")


class UnknownObservationError(EvidenceAdapterError):
    code = "unknown_observation"

    def __init__(self, observation_ids: tuple[ObservationId, ...]) -> None:
        self.observation_ids = observation_ids
        joined = ", ".join(str(item) for item in observation_ids)
        super().__init__(f"curator referenced unknown observations: {joined}")


class EvidenceIdentityConflictError(EvidenceAdapterError):
    code = "evidence_identity_conflict"

    def __init__(self, *, claim_ref: str, identity_sha256: str) -> None:
        self.claim_ref = claim_ref
        self.identity_sha256 = identity_sha256
        super().__init__(
            "same claim/evidence identity was proposed with different relation or scope: "
            f"{claim_ref}/{identity_sha256}"
        )


class StagingBudgetExceededError(EvidenceAdapterError):
    code = "staging_budget_exceeded"

    def __init__(self, *, requested: int, remaining: int) -> None:
        self.requested = requested
        self.remaining = remaining
        super().__init__(
            f"staging evidence budget exceeded: requested {requested}, remaining {remaining}"
        )


def adapt_evidence_proposals(
    proposal: CuratorProposal,
    *,
    observations: tuple[TypedObservation, ...],
    budget: EvidenceAdapterBudget,
) -> EvidenceAdapterResult:
    """Bind untrusted references to trusted observations without writing knowledge."""

    by_id: dict[object, TypedObservation] = {}
    for observation in observations:
        key = observation.id.root
        if key in by_id:
            raise DuplicateObservationIdError(observation.id)
        by_id[key] = observation

    candidates: list[tuple[str, EvidenceRelation, str, TypedObservation, str]] = []
    unknown: dict[object, ObservationId] = {}
    for claim in proposal.claims:
        for reference in claim.evidence:
            observation = by_id.get(reference.observation_id.root)
            if observation is None:
                unknown[reference.observation_id.root] = reference.observation_id
                continue
            candidates.append(
                (
                    claim.ref,
                    reference.relation,
                    reference.scope,
                    observation,
                    evidence_identity_sha256(observation),
                )
            )
    if unknown:
        ordered_unknown = tuple(unknown[key] for key in sorted(unknown, key=str))
        raise UnknownObservationError(ordered_unknown)

    grouped: dict[
        tuple[str, str, str],
        list[tuple[EvidenceRelation, str, TypedObservation]],
    ] = defaultdict(list)
    for claim_ref, relation, scope, observation, identity_sha256 in candidates:
        key = (claim_ref, observation.kind.value, identity_sha256)
        grouped[key].append((relation, scope, observation))

    if len(grouped) > budget.remaining_evidence_items:
        raise StagingBudgetExceededError(
            requested=len(grouped),
            remaining=budget.remaining_evidence_items,
        )

    proposals: list[EvidenceProposal] = []
    duplicates: list[DuplicateEvidenceReference] = []
    relation_map = {
        EvidenceRelation.SUPPORTS: EvidenceUse.SUPPORT,
        EvidenceRelation.COUNTERS: EvidenceUse.COUNTER,
    }
    for claim_ref, kind_value, identity_sha256 in sorted(grouped):
        members = grouped[(claim_ref, kind_value, identity_sha256)]
        signatures = {(relation, scope) for relation, scope, _ in members}
        if len(signatures) != 1:
            raise EvidenceIdentityConflictError(
                claim_ref=claim_ref,
                identity_sha256=identity_sha256,
            )

        ordered_members = sorted(members, key=lambda item: str(item[2].id))
        relation, scope, kept = ordered_members[0]
        proposals.append(
            EvidenceProposal(
                claim_ref=claim_ref,
                observation_id=kept.id,
                relation=relation_map[relation],
                evidence_kind=kept.kind,
                identity_sha256=identity_sha256,
                scope=scope,
                observation=kept,
            )
        )
        for _, _, duplicate in ordered_members[1:]:
            duplicates.append(
                DuplicateEvidenceReference(
                    claim_ref=claim_ref,
                    evidence_kind=kept.kind,
                    identity_sha256=identity_sha256,
                    kept_observation_id=kept.id,
                    duplicate_observation_id=duplicate.id,
                )
            )

    duplicates.sort(
        key=lambda item: (
            item.claim_ref,
            item.evidence_kind.value,
            item.identity_sha256,
            str(item.duplicate_observation_id),
        )
    )
    return EvidenceAdapterResult(
        proposals=tuple(proposals),
        duplicates=tuple(duplicates),
    )
