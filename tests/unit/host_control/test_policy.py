"""Host recovery policy parsing and source-selection tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from apps.host_control import HostPolicyInvalidError, load_host_recovery_policy

_ROOT = Path(__file__).resolve().parents[3]
_BASELINE = _ROOT / "infra" / "systemd" / "host-recovery.defaults.toml"


def test_packaged_policy_is_hash_pinned_and_has_capped_backoff() -> None:
    loaded = load_host_recovery_policy(_BASELINE, enforce_root_metadata=False)

    assert loaded.source_kind == "packaged"
    assert len(loaded.source_file_sha256) == 64
    assert len(loaded.canonical_sha256) == 64
    assert loaded.policy.retry_delay_ns(0) == 30_000_000_000
    assert loaded.policy.retry_delay_ns(1) == 60_000_000_000
    assert loaded.policy.retry_delay_ns(100) == 1_800_000_000_000


def test_present_invalid_override_never_falls_back_to_baseline(tmp_path: Path) -> None:
    override = tmp_path / "host-recovery.toml"
    override.write_text('schema_version = 1\nresume_retry_initial = "30s"\n', encoding="utf-8")

    with pytest.raises(HostPolicyInvalidError, match="invalid override"):
        load_host_recovery_policy(
            _BASELINE,
            override_path=override,
            enforce_root_metadata=False,
        )


def test_schema_v1_rejects_jitter_and_timer_drift(tmp_path: Path) -> None:
    content = _BASELINE.read_text(encoding="utf-8")
    override = tmp_path / "host-recovery.toml"
    override.write_text(
        content.replace("resume_retry_jitter = 0.0", "resume_retry_jitter = 0.1"),
        encoding="utf-8",
    )
    with pytest.raises(HostPolicyInvalidError):
        load_host_recovery_policy(
            _BASELINE,
            override_path=override,
            enforce_root_metadata=False,
        )

    override.write_text(
        content.replace('retry_timer_period = "30s"', 'retry_timer_period = "29s"'),
        encoding="utf-8",
    )
    with pytest.raises(HostPolicyInvalidError, match="pins retry_timer_period"):
        load_host_recovery_policy(
            _BASELINE,
            override_path=override,
            enforce_root_metadata=False,
        )
