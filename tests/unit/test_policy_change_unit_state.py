"""Unit: host-policy change protocol (T3.15) + unit-state publisher
(T3.16, §8.7.1.1)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hostctl import policy_change, unit_state
from hostctl.policy import PolicyError

VALID_A = (
    b"schema_version = 1\nresume_retry_initial = \"30s\"\nresume_retry_multiplier = 2.0\n"
    b"resume_retry_max = \"30min\"\nresume_retry_jitter = 0.0\n"
    b"resume_retry_escalate_after = \"15min\"\nretry_timer_period = \"30s\"\n"
    b"retry_timer_accuracy = \"1s\"\nmaintenance_flock_deadline = \"2s\"\n"
)
VALID_B = (
    b"schema_version = 1\nresume_retry_initial = \"45s\"\nresume_retry_multiplier = 2.0\n"
    b"resume_retry_max = \"30min\"\nresume_retry_jitter = 0.0\n"
    b"resume_retry_escalate_after = \"15min\"\nretry_timer_period = \"45s\"\n"
    b"retry_timer_accuracy = \"1s\"\nmaintenance_flock_deadline = \"2s\"\n"
)


@pytest.fixture()
def host(tmp_path: Path) -> dict[str, Path]:
    base = tmp_path / "host-lib"
    base.mkdir()
    override = tmp_path / "override.toml"
    override.write_bytes(VALID_A)
    return {"base": base, "override": override}


def test_install_policy_commits_and_clears_head(host):
    base, override = host["base"], host["override"]
    candidate = host["base"].parent / "candidate.toml"
    candidate.write_bytes(VALID_B)
    policy = policy_change.install_policy(
        base=base, override=override, candidate_file=candidate, actor="op", reason="tune backoff"
    )
    # the override now equals the proposed policy
    assert override.read_bytes() == VALID_B
    assert policy.host_policy_sha256
    # the head is cleared after the terminal event
    assert policy_change.read_policy_head(base) is None
    # the event stream has prepared + committed
    events_dir = policy_change.policy_events_dir(base)
    streams = list(events_dir.iterdir())
    assert len(streams) == 1
    kinds = sorted(
        json.loads(p.read_bytes())["kind"] for p in streams[0].glob("*.json")
    )
    assert kinds == ["committed", "prepared"]


def test_install_policy_refuses_when_head_active(host):
    base, override = host["base"], host["override"]
    # simulate an in-progress change
    (base / "host-policy-change-head.json").write_text("{}")
    candidate = base.parent / "candidate.toml"
    candidate.write_bytes(VALID_B)
    with pytest.raises(policy_change.PolicyChangeError):
        policy_change.install_policy(
            base=base, override=override, candidate_file=candidate, actor="op", reason="x"
        )


def test_install_policy_refuses_identical_candidate(host):
    base, override = host["base"], host["override"]
    candidate = base.parent / "candidate.toml"
    candidate.write_bytes(VALID_A)  # same as current
    with pytest.raises(policy_change.PolicyChangeError):
        policy_change.install_policy(
            base=base, override=override, candidate_file=candidate, actor="op", reason="x"
        )


def test_resolve_accept_current_requires_valid_file(host):
    base, override = host["base"], host["override"]
    head = {"schema_version": 1, "change_id": "abc", "old_hash": "o", "proposed_hash": "p", "created_at": "t"}
    (base / "host-policy-change-head.json").write_text(json.dumps(head))
    # current override is valid -> accept_current works
    policy = policy_change.resolve_policy(
        base=base, override=override, actor="op", reason="accept", accept_current=True
    )
    assert policy.host_policy_sha256
    assert policy_change.read_policy_head(base) is None


def test_resolve_accept_current_rejects_invalid_file(host):
    base, override = host["base"], host["override"]
    head = {"schema_version": 1, "change_id": "abc", "old_hash": "o", "proposed_hash": "p", "created_at": "t"}
    (base / "host-policy-change-head.json").write_text(json.dumps(head))
    override.write_bytes(b"schema_version = 9\n")  # invalid current file
    with pytest.raises(PolicyError):
        policy_change.resolve_policy(
            base=base, override=override, actor="op", reason="accept", accept_current=True
        )
    # the head is left in place (a crash/invalid state is not auto-cleared)
    assert policy_change.read_policy_head(base) is not None


def test_resolve_install_replacement_works_on_invalid_current(host):
    base, override = host["base"], host["override"]
    head = {"schema_version": 1, "change_id": "abc", "old_hash": "o", "proposed_hash": "p", "created_at": "t"}
    (base / "host-policy-change-head.json").write_text(json.dumps(head))
    override.write_bytes(b"garbage")  # invalid current file
    replacement = base.parent / "replacement.toml"
    replacement.write_bytes(VALID_B)
    policy = policy_change.resolve_policy(
        base=base,
        override=override,
        actor="op",
        reason="fix",
        replacement_file=replacement,
    )
    assert policy.host_policy_sha256
    assert override.read_bytes() == VALID_B
    assert policy_change.read_policy_head(base) is None


def test_unit_state_publish_and_fresh(tmp_path: Path):
    path = tmp_path / "unit-state.json"
    snapshot = unit_state.publish_unit_state(
        path, units={"noezema-runtime.target": "active"}, boot_id="boot-1"
    )
    assert snapshot.boot_id == "boot-1"
    assert snapshot.is_fresh()
    # read it back
    loaded = unit_state.read_unit_state(path)
    assert loaded is not None
    assert loaded.units["noezema-runtime.target"] == "active"
    assert loaded.ttl_seconds == unit_state.UNIT_STATE_TTL_SECONDS


def test_unit_state_missing_is_none(tmp_path: Path):
    assert unit_state.read_unit_state(tmp_path / "nope.json") is None


def test_unit_state_torn_file_is_none(tmp_path: Path):
    path = tmp_path / "unit-state.json"
    path.write_text("{ not json")
    assert unit_state.read_unit_state(path) is None
