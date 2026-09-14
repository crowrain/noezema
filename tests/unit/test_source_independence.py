"""Unit: conservative source independence groups (T3.5, §8.5)."""

from __future__ import annotations

from packages.memory.evidence import INDEPENDENCE_ALGORITHM_VERSION
from packages.memory.independence import (
    IndependenceInput,
    group_sources,
    normalize_uri,
    registrable_domain,
)


def test_uri_normalization_case_and_default_port():
    assert normalize_uri("HTTPS://Example.COM:443/a") == "https://example.com/a"
    assert normalize_uri("http://www.example.com/a/") == "http://example.com/a"


def test_registrable_domain_plain():
    assert registrable_domain("https://blog.example.com/post") == "example.com"


def test_registrable_domain_multi_part_suffix():
    # co.uk is a multi-part public suffix → registrable = one label up
    assert registrable_domain("https://shop.example.co.uk/x") == "example.co.uk"
    assert registrable_domain("https://a.b.com.au/x") == "b.com.au"


def test_same_domain_same_group():
    res = group_sources(
        [
            IndependenceInput("s1", canonical_uri="https://a.example.com/1"),
            IndependenceInput("s2", canonical_uri="https://b.example.com/2"),
        ]
    )
    assert res.groups["s1"] == res.groups["s2"]


def test_different_domains_different_groups():
    res = group_sources(
        [
            IndependenceInput("s1", canonical_uri="https://example.com/1"),
            IndependenceInput("s2", canonical_uri="https://other.org/2"),
        ]
    )
    assert res.groups["s1"] != res.groups["s2"]


def test_text_overlap_merges_groups():
    a = "the quick brown fox jumps over the lazy dog near the river bank today"
    b = "the quick brown fox jumps over the lazy dog near the river bank tomorrow"
    res = group_sources(
        [
            IndependenceInput("s1", canonical_uri="https://one.net/1", sample_text=a),
            IndependenceInput("s2", canonical_uri="https://two.net/2", sample_text=b),
        ]
    )
    assert res.groups["s1"] == res.groups["s2"]  # overlap above threshold


def test_unknown_lineage_single_group():
    res = group_sources(
        [
            IndependenceInput("s1"),
            IndependenceInput("s2"),
            IndependenceInput("s3", canonical_uri="https://known.org/x"),
        ]
    )
    # the two lineage-unknown sources share one conservative group
    assert res.groups["s1"] == res.groups["s2"]
    assert res.groups["s1"] != res.groups["s3"]
    assert res.bases["s1"] == "unknown_lineage"


def test_snapshot_carries_algorithm_fingerprint():
    res = group_sources([IndependenceInput("s1", canonical_uri="https://example.com/1")])
    assert res.algorithm_version == INDEPENDENCE_ALGORITHM_VERSION
    assert res.psl_fingerprint
    assert res.thresholds["text_overlap"] == 0.8
    assert res.uri_normalizer_version
