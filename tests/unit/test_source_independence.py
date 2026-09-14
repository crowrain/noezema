"""Unit: conservative source independence groups (T3.5, §8.5) + the full
source graph (T4.7, §11.3/§14)."""

from __future__ import annotations

from packages.memory.evidence import (
    INDEPENDENCE_ALGORITHM_VERSION,
    SOURCE_GRAPH_ALGORITHM_VERSION,
)
from packages.memory.independence import (
    IndependenceInput,
    SourceGraphCorrection,
    SourceGraphEdge,
    SourceGraphInput,
    group_source_graph,
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


# ── T4.7: the full source graph (§11.3, §14) ──────────────────────────


def test_graph_identical_content_hash_merges():
    # one document mirrored across two domains: 100% text overlap
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1", content_hash="h1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2", content_hash="h1"),
            SourceGraphInput("s3", canonical_uri="https://c.org/3", content_hash="h2"),
        ]
    )
    assert res.groups["s1"] == res.groups["s2"]
    assert res.groups["s1"] != res.groups["s3"]
    assert res.bases["s1"].startswith("content:")
    assert res.algorithm_version == SOURCE_GRAPH_ALGORITHM_VERSION


def test_graph_parent_source_merges_children():
    # two children of one parent merge THROUGH the parent (the parent is
    # a graph node even when it is not itself a member)
    res = group_source_graph(
        [
            SourceGraphInput("a", canonical_uri="https://a.net/1", parent_source_id="p"),
            SourceGraphInput("b", canonical_uri="https://b.org/2", parent_source_id="p"),
            SourceGraphInput("p", canonical_uri="https://primary.org/0"),
        ]
    )
    assert res.groups["a"] == res.groups["b"] == res.groups["p"]
    assert res.bases["a"].startswith("parent:")


def test_graph_dependency_edge_merges():
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
        ],
        edges=[SourceGraphEdge("e1", "s1", "s2", "link_to_primary")],
    )
    assert res.groups["s1"] == res.groups["s2"]
    assert res.bases["s1"] == "edge:link_to_primary:e1"


def test_graph_split_correction_cancels_direct_edge():
    # the correction of a false merge: the DIRECT edge basis between the
    # pair is cancelled → the groups separate again
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
        ],
        edges=[SourceGraphEdge("e1", "s1", "s2", "link_to_primary")],
        corrections=[SourceGraphCorrection("c1", "s1", "s2", "split", valid=True)],
    )
    assert res.groups["s1"] != res.groups["s2"]
    assert res.bases["s1"] == "single"


def test_graph_split_cannot_undo_algorithmic_facts():
    # a split never undoes a shared domain / parent / content hash —
    # those are data, not graph relations (§11.3: never over-grade,
    # but a false split is equally a misclassification)
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.example.com/1"),
            SourceGraphInput("s2", canonical_uri="https://b.example.com/2"),
        ],
        corrections=[SourceGraphCorrection("c1", "s1", "s2", "split", valid=True)],
    )
    assert res.groups["s1"] == res.groups["s2"]
    assert res.bases["s1"].startswith("domain:")


def test_graph_merge_correction_across_domains():
    # an explicit operator merge with a provenance chain merges even
    # across different domains
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
        ],
        corrections=[SourceGraphCorrection("c1", "s1", "s2", "merge", valid=True)],
    )
    assert res.groups["s1"] == res.groups["s2"]
    assert res.bases["s1"] == "correction:merge"


def test_graph_split_beats_conflicting_merge():
    # fail-closed: a valid split on the pair cancels a conflicting
    # explicit merge (the conservative direction keeps groups separate)
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
        ],
        corrections=[
            SourceGraphCorrection("c1", "s1", "s2", "merge", valid=True),
            SourceGraphCorrection("c2", "s2", "s1", "split", valid=True),
        ],
    )
    assert res.groups["s1"] != res.groups["s2"]


def test_graph_invalid_correction_ignored():
    res = group_source_graph(
        [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
        ],
        corrections=[SourceGraphCorrection("c1", "s1", "s2", "merge", valid=False)],
    )
    assert res.groups["s1"] != res.groups["s2"]


def test_graph_unknown_lineage_shared_group():
    res = group_source_graph(
        [
            SourceGraphInput("s1"),
            SourceGraphInput("s2"),
            SourceGraphInput("s3", canonical_uri="https://known.org/x"),
        ]
    )
    assert res.groups["s1"] == res.groups["s2"]
    assert res.groups["s1"] != res.groups["s3"]
    assert res.bases["s1"] == "unknown_lineage"


def test_graph_transitive_merge_and_determinism():
    # s1—s2 (edge), s2—s3 (edge) → one group; the result does not depend
    # on the input order (callers sort by id — simulate both orders)
    edges = [
        SourceGraphEdge("e1", "s1", "s2", "derived_from"),
        SourceGraphEdge("e2", "s2", "s3", "derived_from"),
    ]
    def mk() -> list:
        return [
            SourceGraphInput("s1", canonical_uri="https://a.net/1"),
            SourceGraphInput("s2", canonical_uri="https://b.org/2"),
            SourceGraphInput("s3", canonical_uri="https://c.io/3"),
        ]

    r1 = group_source_graph(mk(), edges)
    r2 = group_source_graph(list(reversed(mk())), list(reversed(edges)))
    assert r1.groups["s1"] == r1.groups["s3"]
    assert r1.groups["s1"] == r2.groups["s1"]
    assert r1.groups["s2"] == r2.groups["s2"]
    assert r1.groups["s3"] == r2.groups["s3"]
