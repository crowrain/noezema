"""Unit: the versioned environment-independence algorithm (T4.6, §8.7.3)
and the rules engine's ``required_independence`` check.

The algorithm is pure over manifest views — groups are built over
(protocol, implementation, dataset lineage) ONLY: a different GPU/backend,
seed or data order never creates an independent group. Relations between
manifests: repeatability / reproducibility / independent_replication /
variation / untracked / none; unknown lineage is fail-closed (never
independence).
"""

from __future__ import annotations

import uuid

import pytest

from packages.domain.config import BOOTSTRAP_PAYLOAD
from packages.memory.env_independence import (
    INDEPENDENT_REPLICATION,
    NONE,
    REPEATABILITY,
    REPRODUCIBILITY,
    UNTRACKED,
    VARIATION,
    EnvManifestView,
    classify_pair,
    environment_group,
    strongest_pair_relation,
)
from packages.memory.rules_engine import (
    ClaimTypeRule,
    EvaluatedEvidence,
    evaluate,
)

_A = uuid.UUID(int=0xA)
_B = uuid.UUID(int=0xB)
_C = uuid.UUID(int=0xC)


def _manifest(
    tag: uuid.UUID,
    *,
    protocol: str = "proto-1",
    implementation: str = "impl-1",
    dataset_lineage: str | None = None,
    runtime: str = "py-3.11",
    hardware: str = "hw-1",
    toolchain: str = "tools-1",
    dependency: str | None = None,
    seed: int | None = None,
    data_order: str | None = None,
) -> EnvManifestView:
    return EnvManifestView(
        id=tag,
        protocol_hash=protocol,
        implementation_hash=implementation,
        dataset_lineage=dataset_lineage,
        runtime_hash=runtime,
        hardware_hash=hardware,
        toolchain_hash=toolchain,
        dependency_hash=dependency,
        seed=seed,
        data_order_hash=data_order,
    )


def test_group_key_ignores_gpu_seed_and_data_order() -> None:
    """§8.7.3: another GPU/backend, seed or data order NEVER creates an
    independent group — the group is (protocol, implementation,
    dataset lineage) only."""
    a = _manifest(_A)
    b = _manifest(_B, hardware="hw-gpu-9", seed=7, data_order="shuffle-2")
    c = _manifest(_C, runtime="py-3.12")
    assert environment_group(a) == environment_group(b) == environment_group(c)


def test_group_key_changes_with_method_or_data_lineage() -> None:
    a = _manifest(_A)
    b = _manifest(_B, implementation="impl-2")
    c = _manifest(_C, dataset_lineage="ds-x")
    assert environment_group(a) != environment_group(b)
    assert environment_group(a) != environment_group(c)
    assert environment_group(b) != environment_group(c)


def test_untracked_group() -> None:
    untracked = _manifest(_A, protocol=None, implementation=None, dataset_lineage=None)
    assert environment_group(untracked) == "envgrp:untracked"
    # one recorded field already leaves the untracked group
    partial = _manifest(_A, protocol=None, implementation="impl-1", dataset_lineage=None)
    assert environment_group(partial) != "envgrp:untracked"


def test_classify_repeatability() -> None:
    a = _manifest(_A)
    b = _manifest(_B)
    assert classify_pair(a, b) == REPEATABILITY


def test_classify_reproducibility_different_hardware() -> None:
    """Same method, different runtime/hardware/toolchain: portability —
    still the SAME group, still NOT independent."""
    a = _manifest(_A)
    b = _manifest(_B, hardware="hw-gpu-9")
    assert classify_pair(a, b) == REPRODUCIBILITY
    c = _manifest(_C, runtime="py-3.12", toolchain="tools-2")
    assert classify_pair(a, c) == REPRODUCIBILITY


def test_classify_independent_replication() -> None:
    """An independently implemented protocol OR implementation is the
    only relation that may lift a grade to E3."""
    a = _manifest(_A)
    assert classify_pair(a, _manifest(_B, implementation="impl-2")) == INDEPENDENT_REPLICATION
    assert classify_pair(a, _manifest(_C, protocol="proto-2")) == INDEPENDENT_REPLICATION


def test_shared_dataset_lineage_kills_independence() -> None:
    """Where the claim depends on data, two known-equal dataset lineages
    are NOT an independent replication (same data, different code)."""
    a = _manifest(_A, dataset_lineage="ds-x")
    b = _manifest(_B, implementation="impl-2", dataset_lineage="ds-x")
    assert classify_pair(a, b) == VARIATION
    # ...but independent lineages keep it
    c = _manifest(_C, implementation="impl-2", dataset_lineage="ds-y")
    assert classify_pair(a, c) == INDEPENDENT_REPLICATION


def test_same_method_different_data_lineage_is_variation() -> None:
    """Different groups (different data lineage) but the same method:
    not independent (the implementation is the same)."""
    a = _manifest(_A, dataset_lineage="ds-x")
    b = _manifest(_B, dataset_lineage="ds-y")
    assert environment_group(a) != environment_group(b)
    assert classify_pair(a, b) == VARIATION


def test_untracked_never_independent() -> None:
    """Unknown lineage is fail-closed: it creates no independence (and no
    repeatability) — in either direction."""
    a = _manifest(_A)
    u = _manifest(_B, protocol=None, implementation=None, dataset_lineage=None)
    assert classify_pair(a, u) == UNTRACKED
    assert classify_pair(u, a) == UNTRACKED
    # two untracked manifests share the conservative group, but the
    # relation stays untracked (nothing is known)
    v = _manifest(_C, protocol=None, implementation=None, dataset_lineage=None)
    assert environment_group(u) == environment_group(v)
    assert classify_pair(u, v) == UNTRACKED


def test_strongest_pair_reduction_with_variation() -> None:
    """A pair that classifies as ``variation`` must rank (not crash the
    reduction) below any positive relation — regression: variation was
    missing from RELATION_RANK."""
    base = _manifest(_A, dataset_lineage="ds-x")
    variation = _manifest(_B, dataset_lineage="ds-y")  # same method, other lineage
    assert classify_pair(base, variation) == VARIATION
    rel, basis = strongest_pair_relation(base, [variation])
    # the pair exists and is recorded (as variation) — but it carries no
    # positive independence
    assert rel == VARIATION
    assert basis.startswith("pair:")
    # ...and a positive pair still wins over it (an independent
    # implementation with NO shared known data lineage)
    indep = _manifest(_C, implementation="impl-2", dataset_lineage=None)
    rel, basis = strongest_pair_relation(base, [variation, indep])
    assert rel == INDEPENDENT_REPLICATION


def test_strongest_pair_reduction() -> None:
    base = _manifest(_A)
    repeat = _manifest(_B)
    repro = _manifest(_C, hardware="hw-2")
    indep = _manifest(uuid.UUID(int=0xD), implementation="impl-2")
    rel, basis = strongest_pair_relation(base, [repeat, repro, indep])
    assert rel == INDEPENDENT_REPLICATION
    assert basis.startswith(f"pair:{uuid.UUID(int=0xD).hex[:12]}:")
    # a lone manifest has no pair
    rel, basis = strongest_pair_relation(base, [])
    assert (rel, basis) == (NONE, "single")


def _ec_rule() -> ClaimTypeRule:
    """The bootstrap empirical_conjecture rule: E3, 2 groups,
    independent replication required (§8.7.3)."""
    return ClaimTypeRule.from_payload(
        "empirical_conjecture", dict(BOOTSTRAP_PAYLOAD["claim_type_rules"]["empirical_conjecture"])
    )


def _ev(group: str, relation: str, idx: int) -> EvaluatedEvidence:
    return EvaluatedEvidence(
        identity_hash=f"h{idx}",
        kind="experiment_run",
        relation="supports",
        scope={"gpu": "any"},
        independence_group=group,
        env_relation=relation,
    )


def test_engine_independent_replication_lifts_e3() -> None:
    rule = _ec_rule()
    assert rule.required_independence == "independent_replication"
    evs = [
        _ev("g1", "independent_replication", 1),
        _ev("g2", "independent_replication", 2),
    ]
    result = evaluate("empirical_conjecture", rule, {"gpu": "any"}, evs, has_as_of=False)
    assert result.epistemic_status.value == "supported"
    assert result.grade.value == "E3"


def test_engine_same_group_repeats_are_not_independent() -> None:
    """Two runs of the SAME implementation in the same environment are
    one group of repeatability — E3 is out of reach (§8.7.3)."""
    rule = _ec_rule()
    evs = [_ev("g1", "repeatability", 1), _ev("g1", "repeatability", 2)]
    result = evaluate("empirical_conjecture", rule, {"gpu": "any"}, evs, has_as_of=False)
    assert result.epistemic_status.value == "hypothesis"
    assert result.reasons == ("insufficient_independence",)


def test_engine_groups_alone_do_not_satisfy_independence() -> None:
    """Engine contract: distinct groups WITHOUT the required relation do
    not satisfy it (reproducibility between two groups is still not an
    independent replication)."""
    rule = _ec_rule()
    evs = [_ev("g1", "reproducibility", 1), _ev("g2", "reproducibility", 2)]
    result = evaluate("empirical_conjecture", rule, {"gpu": "any"}, evs, has_as_of=False)
    assert result.epistemic_status.value == "hypothesis"
    assert result.reasons == ("independence_independent_replication_not_met",)


def test_engine_relation_not_met_reason() -> None:
    """Groups are enough, the required relation is missing — a separate
    auditable reason."""
    rule = _ec_rule()
    # two groups (different data lineage), same implementation: variation
    # on both sides, no independent replication anywhere
    evs = [_ev("g1", "variation", 1), _ev("g2", "variation", 2)]
    result = evaluate("empirical_conjecture", rule, {"gpu": "any"}, evs, has_as_of=False)
    assert result.epistemic_status.value == "hypothesis"
    assert result.reasons == ("independence_independent_replication_not_met",)


def test_engine_reproducibility_requirement() -> None:
    """A portability-style rule (reproducibility suffices, §8.7.3) is
    met by two same-method runs on different hardware."""
    rule = ClaimTypeRule.from_payload(
        "portability_probe",
        {
            "min_grade_for_supported": "E2",
            "allowed_kinds": ["experiment_run"],
            "min_support_evidence": 2,
            "min_independence_groups": 1,
            "required_independence": "reproducibility",
            "requires_scope": True,
        },
    )
    evs = [_ev("g1", "reproducibility", 1), _ev("g1", "reproducibility", 2)]
    result = evaluate("portability_probe", rule, {"gpu": "any"}, evs, has_as_of=False)
    assert result.epistemic_status.value == "supported"


def test_engine_unknown_required_relation_rejected() -> None:
    with pytest.raises(ValueError):
        ClaimTypeRule.from_payload(
            "bogus", {"min_grade_for_supported": "E2", "required_independence": "vibes"}
        )
