"""Reverse dependency closure, stage 3b core computation (spec section 8.6).

The reverse dependency closure of a claim R is every claim that depends
on R directly or transitively. When R becomes invalid, every claim in
the closure can no longer be trusted as current and must be re-assessed:
invalidation propagates against the depends-on edge direction, which is
why the traversal is "reverse".

Edge direction (claim_dependencies): claim_id depends_on
depends_on_id means "claim_id depends on depends_on_id". The traversal
input is a reverse adjacency: x -> (y, ...) where each y directly
depends on x.

This module is pure: no database access, no I/O. The async adapter that
walks claim_dependencies while checking
domain_revisions(scope="dependency_graph") lives in
packages.domain.services.closure_service.

Guarantees (spec 8.6):

- Deterministic: identical input produces identical members, ranks and
  manifest bytes.
- Acyclic order: on an acyclic result the member rank is the longest
  path from the root, so manifest order is topological: every
  dependency of a claim inside the closure precedes the claim.
- Cycles are forbidden in evidential dependencies; a detected cycle
  makes the result unusable for a manifest (ClosureCycleError) and the
  cycle nodes are reported so the caller moves the whole cycle into
  pending state and requires operator review.
- The manifest is immutable and content-addressed: root, graph
  revision, ordered claim ids with depth and topological rank, count,
  and SHA-256 of the canonical serialization; the hash doubles as the
  manifest id.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from typing import Iterable, Mapping

MANIFEST_SCHEMA = "noezema.closure_manifest/v1"


class ClosureError(Exception):
    """Base class for reverse-closure computation failures."""


class ClosureCycleError(ClosureError):
    """The dependency closure contains a cycle (spec 8.6: forbidden)."""

    def __init__(self, cycles: tuple[tuple[uuid.UUID, ...]], message: str | None = None) -> None:
        self.cycles = cycles
        text = message or (
            "dependency closure contains %d cycle(s): move the cycle nodes "
            "to pending and require operator review"
        ) % len(cycles)
        super().__init__(text)


@dataclass(frozen=True, order=True)
class ClosureMember:
    """One claim inside a reverse dependency closure.

    depth: shortest path from the root (BFS level, root is 0).
    rank:  longest path from the root (topological order); None when the
           member depends, transitively, on a detected cycle.
    """

    claim_id: uuid.UUID
    depth: int
    rank: int | None


def _normalize_cycle(cycle: tuple[uuid.UUID, ...]) -> tuple[uuid.UUID, ...]:
    """Rotate a cycle so its smallest node comes first (canonical form)."""
    i = cycle.index(min(cycle))
    return cycle[i:] + cycle[:i]


def _find_cycles(nodes: frozenset, adjacency: Mapping) -> tuple:
    """Detect directed cycles in the subgraph induced by ``nodes``.

    Returns (cycles, taint_sets) where cycles are normalized node tuples
    and taint_sets[i] holds  members whose rank is undefined
    because they depend on  cycles[i], transitively.
    following reverse-adjacency edges).

    Iterative DFS with white/gray/black colors; adjacency is walked in
    sorted order, so the result is deterministic.
    """
    WHITE, GRAY, BLACK = 0, 1, 2
    color: dict = {n: WHITE for n in nodes}
    cycles: list = []
    seen: set = set()
    taint_sets: list = []

    for start in sorted(nodes):
        if color[start] != WHITE:
            continue
        color[start] = GRAY
        stack: list = [(start, iter(sorted(adjacency.get(start, ()))))]
        path: list = [start]
        while stack:
            node, it = stack[-1]
            advanced = False
            for nxt in it:
                if nxt not in nodes:
                    continue
                if color[nxt] == GRAY:
                    cycle = tuple(path[path.index(nxt):])
                    key = frozenset(cycle)
                    if key not in seen:
                        seen.add(key)
                        cycles.append(_normalize_cycle(cycle))
                        tainted: set = set()
                        frontier = set(cycle)
                        while frontier:
                            dependents = {
                                d
                                for f in frontier
                                for d in adjacency.get(f, ())
                                if d in nodes
                            }
                            frontier = dependents - tainted
                            tainted |= frontier
                        taint_sets.append(tainted)
                elif color[nxt] == WHITE:
                    color[nxt] = GRAY
                    stack.append((nxt, iter(sorted(adjacency.get(nxt, ())))))
                    path.append(nxt)
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()
                path.pop()

    return tuple(cycles), tuple(taint_sets)


def _compute_ranks(nodes: frozenset, adjacency: Mapping, undefined: frozenset) -> dict:
    """Longest-path ranks over the DAG induced by nodes - undefined.

    Kahn's algorithm over reverse-adjacency edges: for x -> y (y depends
    on x) we get rank[y] = max(rank[y], rank[x] + 1); the root has rank 0.
    """
    live = nodes - undefined
    in_degree: dict = {n: 0 for n in live}
    successors: dict = {n: [] for n in live}
    for x in live:
        for y in adjacency.get(x, ()):
            if y in live:
                in_degree[y] += 1
                successors[x].append(y)

    queue = sorted(n for n in live if in_degree[n] == 0)
    ranks: dict = {}
    while queue:
        x = queue.pop(0)
        ranks.setdefault(x, 0)
        for y in sorted(successors[x]):
            ranks[y] = max(ranks.get(y, -1), ranks[x] + 1)
            in_degree[y] -= 1
            if in_degree[y] == 0:
                queue.append(y)
        queue.sort()
    return ranks


@dataclass(frozen=True)
class ClosureResult:
    """Outcome of a reverse dependency traversal.

    members are ordered by (depth then id); topological_order() gives the
    job-creation order (rank then id).
    """

    root: uuid.UUID
    members: tuple = ()
    cycles: tuple = ()

    @property
    def has_cycles(self) -> bool:
        return bool(self.cycles)

    @property
    def member_ids(self) -> frozset:
        return tuple(m.claim_id for  m in self.members)

    @property
    def downstream_ids(self) -> frozset:
        """Closure members excluding the root (the claims to cascade into)."""
        return tuple(m.claim_id for m in self.members if m.claim_id != self.root)

    @property
    def cycle_node_set(self)  -> frozenset:
        """All nodes belonging to any  detected cycle (the set to move to
        pending per spec 8.6)."""
        out: set = set()
        for c in self.cycles:
            out.update(c)
        return frozenset(out)

    @property
    def undefined_rank_ids(self) -> frozenset:
        return frozenset(m.claim_id for m in self.members if m.rank is None)

    def rank_of(self, claim_id: uuid.UUID) -> int | None:
        for m in self.members:
            if m.claim_id == claim_id:
                return m.rank
        return None

    def topological_order(self) -> frozenset:
        """Member ids ordered by (rank, claim id).

        Raises ClosureCycleError when a cycle is present, because no
        topological order exists for the full closure then.
        """
        if self.has_cycles:
            raise ClosureCycleError(self.cycles)
        return tuple(m.claim_id for m in topological_members(self.members))

    def to_manifest(self, graph_revision: int) -> "ClosureManifest":
        return ClosureManifest.build(self, graph_revision)


def topological_members(members: Iterable[ClosureMember]) -> frozenset:
    """Order members by (rank, claim id); only valid for acyclic results."""
    key = lambda m: (m.rank if m.rank is not None else -1, m.claim_id)
    return tuple(sorted(members, key=key))


def compute_reverse_closure(
    root_claim_id: uuid.UUID,
    reverse_adjacency: Mapping,
) -> ClosureResult:
    """Compute the reverse dependency closure of ``root_claim_id``.

    ``reverse_adjacency`` maps a claim to the claims that directly depend
    on it. The root is included (depth 0, rank 0). Cycles in the
    reachable subgraph are detected and reported; they make
    ``to_manifest`` fail (spec 8.6: cycles are forbidden).
    """
    depth: dict = {root_claim_id: 0}
    frontier = [root_claim_id]
    while frontier:
        nxt: list = []
        for x in frontier:
            for y in reverse_adjacency.get(x, ()):
                if y not in depth:
                    depth[y] = depth[x] + 1
                    nxt.append(y)
        frontier = nxt

    nodes = frozenset(depth)
    induced = {
        x: frozenset(y for y in reverse_adjacency.get(x, ()) if y in nodes)
        for x in nodes
        if any(y in nodes for y in reverse_adjacency.get(x, ()))
    }
    cycles, taints = _find_cycles(nodes, induced)
    undefined = frozenset().union(*taints) if taints else frozenset()
    ranks = _compute_ranks(nodes, induced, undefined)

    members = tuple(
        ClosureMember(claim_id=n, depth=depth[n], rank=ranks.get(n))
        for n in sorted(nodes, key=lambda n: (depth[n], n))
    )
    return ClosureResult(root=root_claim_id, members=members, cycles=cycles)


@dataclass(frozen=True)
class ClosureManifest:
    """Immutable, content-addressed reverse closure manifest (spec 8.6).

    Fields: root, graph revision, ordered member list (claim id, depth,
    topological rank), count. Identity is the SHA-256 of the canonical
    serialization (``sha256`` / ``id``).
    """

    schema: str
    root: uuid.UUID
    graph_revision: int
    members: tuple = ()

    @classmethod
    def build(cls, result: ClosureResult, graph_revision: int) -> "ClosureManifest":
        """Build a manifest from an acyclic closure result.

        ``graph_revision`` is the domain_revisions value for scope
        "dependency_graph" the closure was computed against. Raises
        ClosureCycleError for cyclic results, ValueError for a bad
        revision.
        """
        if isinstance(graph_revision, bool) or not isinstance(graph_revision, int):
            raise ValueError("graph_revision must be an int, got %r" % (graph_revision,))
        if graph_revision < 0:
            raise ValueError("graph_revision must be >= 0, got %d" % graph_revision)
        if result.has_cycles:
            raise ClosureCycleError(result.cycles)
        return cls(
            schema=MANIFEST_SCHEMA,
            root=result.root,
            graph_revision=graph_revision,
            members=topological_members(result.members),
        )

    def payload_dict(self) -> dict:
        return {
            "schema": self.schema,
            "root": str(self.root).lower(),
            "graph_revision": self.graph_revision,
            "count": len(self.members),
            "members": [[str(m.claim_id).lower(), m.depth, m.rank] for m in self.members],
        }

    def canonical_bytes(self) -> bytes:
        return json.dumps(
            self.payload_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True
        ).encode("utf-8")

    def to_json(self) -> str:
        return self.canonical_bytes().decode("utf-8")

    @property
    def count(self) -> int:
        return len(self.members)

    @property
    def sha256(self) -> str:
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    @property
    def id(self) -> str:
        """Content-addressed manifest identifier."""
        return self.sha256

    @property
    def member_ids(self) -> frozenset:
        return tuple(m.claim_id for  m in self.members)

    @property
    def downstream_ids(self)  -> frozenset:
        """Members in manifest order, excluding the root."""
        return tuple(m.claim_id for m in self.members if m.claim_id != self.root)

    def batch(self, offset: int, limit: int) -> frozenset:
        """Manifest members [offset : offset + limit] (cursor slicing for
        barrier batches, spec 8.6)."""
        if offset < 0 or limit < 0:
            raise ValueError("offset and limit must be >= 0")
        return self.members[offset : offset + limit]

    def remaining(self, offset: int) -> int:
        if offset < 0:
            raise ValueError("offset must be >= 0")
        return max(0, self.count - offset)

    @classmethod
    def from_json(cls, raw: str) -> "ClosureManifest":
        obj = json.loads(raw)
        if obj.get("schema") != MANIFEST_SCHEMA:
            raise ValueError("unsupported manifest schema: %r" % (obj.get("schema"),))
        rows = obj.get("members") or []
        if obj.get("count") != len(rows):
            raise ValueError("manifest count does not match members")
        members = tuple(
            ClosureMember(claim_id=uuid.UUID(str(row[0])), depth=int(row[1]), rank=row[2])
            for row in rows
        )
        return cls(
            schema=MANIFEST_SCHEMA,
            root=uuid.UUID(str(obj["root"])),
            graph_revision=int(obj["graph_revision"]),
            members=members,
        )

    @classmethod
    def verify_json(cls, raw: str, expected_sha256: str) -> bool:
        """True iff the canonical hash of ``raw`` equals ``expected_sha256``."""
        try:
            manifest = cls.from_json(raw)
        except Exception:
            return False
        return manifest.sha256 == expected_sha256
