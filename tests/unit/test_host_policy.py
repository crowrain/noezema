"""Unit: host recovery policy — schema v1 validation + JCS hash (T3.10,
§8.7.1.1)."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from hostctl.policy import (
    POLICY_SCHEMA_VERSION,
    HostPolicy,
    PolicyError,
    load_policy,
    parse_duration_ns,
    read_policy_file,
)

VALID = textwrap.dedent(
    """
    schema_version = 1
    resume_retry_initial = "30s"
    resume_retry_multiplier = 2.0
    resume_retry_max = "30min"
    resume_retry_jitter = 0.0
    resume_retry_escalate_after = "15min"
    retry_timer_period = "30s"
    retry_timer_accuracy = "1s"
    maintenance_flock_deadline = "2s"
    """
)


def test_parse_duration_units():
    assert parse_duration_ns("1s", "x") == 1_000_000_000
    assert parse_duration_ns("30min", "x") == 30 * 60 * 1_000_000_000
    assert parse_duration_ns("2s", "x") == 2_000_000_000
    assert parse_duration_ns("150ms", "x") == 150_000_000


def test_parse_duration_invalid():
    with pytest.raises(PolicyError):
        parse_duration_ns("soon", "x")
    with pytest.raises(PolicyError):
        parse_duration_ns(5, "x")


def test_valid_policy_parses_and_hashes():
    p = load_policy(VALID.encode(), source="baseline")
    assert p.schema_version == POLICY_SCHEMA_VERSION
    assert p.resume_retry_initial == 30_000_000_000
    assert p.resume_retry_multiplier == 2.0
    assert p.resume_retry_jitter == 0.0
    assert p.host_policy_sha256
    # hash is deterministic and order-insensitive in the canonical dict
    p2 = load_policy(VALID.encode(), source="baseline")
    assert p.host_policy_sha256 == p2.host_policy_sha256
    assert isinstance(p, HostPolicy)


def test_nonzero_jitter_rejected_in_v1():
    bad = VALID.replace("resume_retry_jitter = 0.0", "resume_retry_jitter = 0.5")
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_wrong_schema_version_rejected():
    bad = VALID.replace("schema_version = 1", "schema_version = 2")
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_missing_key_rejected():
    bad = "\n".join(line for line in VALID.splitlines() if "retry_timer_accuracy" not in line)
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_timer_period_must_be_le_initial():
    bad = VALID.replace('resume_retry_initial = "30s"', 'resume_retry_initial = "10s"')
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_accuracy_must_be_lt_period():
    bad = VALID.replace('retry_timer_accuracy = "1s"', 'retry_timer_accuracy = "30s"')
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_max_must_be_ge_initial():
    bad = VALID.replace('resume_retry_max = "30min"', 'resume_retry_max = "5s"')
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_multiplier_must_be_ge_1():
    bad = VALID.replace("resume_retry_multiplier = 2.0", "resume_retry_multiplier = 0.5")
    with pytest.raises(PolicyError):
        load_policy(bad.encode())


def test_read_policy_file_missing(tmp_path: Path):
    with pytest.raises(PolicyError):
        read_policy_file(tmp_path / "nope.toml")


def test_read_policy_file_symlink_rejected(tmp_path: Path):
    real = tmp_path / "real.toml"
    real.write_text(VALID, encoding="utf-8")
    link = tmp_path / "link.toml"
    link.symlink_to(real)
    with pytest.raises(PolicyError):
        read_policy_file(link)


def test_read_policy_file_valid(tmp_path: Path):
    real = tmp_path / "real.toml"
    real.write_text(VALID, encoding="utf-8")
    p = read_policy_file(real)
    assert p.host_policy_sha256
