"""Maturity stage derivation — pure function per spec §4.6.

Computes the S0-S4 maturity stage for a node from its own fields
(``node_type``, ``facets``, ``vetted``) and the set of *live* inbound edges
pointing at it (each reduced to its ``edge_type`` and the ``node_type`` of
its source node — see ``InboundEdge``).

This module performs NO database access and imports nothing from
``store.py``. Filtering edges to "live" (``retracted_at IS NULL``) and
resolving each edge's source node type is the caller's responsibility
(build-plan rule 0.4 — all persistent writes/reads route through
``kernel/store.py``, never this module).

# SPEC-QUESTION (T1.5): spec §4.6 also says maturity must be "recomputed
# inside the same transaction as any mutation that can change the inputs."
# Wiring that recompute call into store.py's mutating functions
# (create_edge, retract_edge, node commit/vet, etc.) is intentionally
# DEFERRED to T1.6, which owns store.py and is where S0-vs-S1+ deletion
# behavior first depends on maturity. store.py is not in T1.5's Files list,
# so only the pure derivation function and its unit tests live here.
"""

from __future__ import annotations

from typing import NamedTuple

from akasha.kernel.model import JUSTIFICATION_EDGE_TYPES, EdgeType, Maturity, NodeType

# Node types that reach S2 without the "len(facets) >= 1" requirement
# (spec §4.6: "S2 iff ... len(facets) >= 1 (types other than task/entity)").
_S2_FACET_EXEMPT_TYPES: frozenset[NodeType] = frozenset({"task", "entity"})

# Source node types that satisfy the S3 "inbound justification edge from an
# evidence/proof node" requirement (spec §4.6).
_S3_JUSTIFYING_SOURCE_TYPES: frozenset[NodeType] = frozenset({"evidence", "proof"})


class InboundEdge(NamedTuple):
    """A single *live* inbound edge, reduced to exactly the fields maturity
    derivation needs.

    Invariant: callers MUST pre-filter to edges with ``retracted_at IS
    NULL`` (or the in-memory equivalent) before constructing these — this
    module has no concept of retraction and treats every ``InboundEdge`` it
    is given as live, per spec §4.6 ("S1 iff live inbound edge count >= 1").
    """

    edge_type: EdgeType
    src_node_type: NodeType


def derive(
    node_type: NodeType,
    facet_count: int,
    vetted: bool,
    inbound_edges: list[InboundEdge],
) -> Maturity:
    """Derive the maturity stage per spec §4.6.

    Invariant: returns the single highest-numbered stage among {S0..S4}
    whose condition below is satisfied (S0 is the fallback when none of
    S1-S4 hold):

      - S1 iff ``len(inbound_edges) >= 1`` (all entries are assumed live —
        see ``InboundEdge``).
      - S2 iff S1 holds AND (``node_type`` is one of the S2-facet-exempt
        types {"task", "entity"} OR ``facet_count >= 1``). ("node_type set"
        from the spec text is always true for a constructed ``Node``, since
        it is a required, validated field — included here only for spec
        fidelity, not as a runtime check.)
      - S3 iff S2 holds AND at least one entry in ``inbound_edges`` has an
        ``edge_type`` in the justification-edge set AND a
        ``src_node_type`` of "evidence" or "proof".
      - S4 iff ``vetted`` is True. Per the literal spec text this is an
        independent condition (unlike S2/S3, spec §4.6 does NOT chain S4
        to "S3-eligible AND ..."), so a vetted node is S4 regardless of
        whether S1-S3 are separately satisfied.

    Pure function: no database access, no imports from ``store.py``, no
    mutation of any argument.
    """
    s1 = len(inbound_edges) >= 1
    s2 = s1 and (node_type in _S2_FACET_EXEMPT_TYPES or facet_count >= 1)
    s3 = s2 and any(
        edge.edge_type in JUSTIFICATION_EDGE_TYPES
        and edge.src_node_type in _S3_JUSTIFYING_SOURCE_TYPES
        for edge in inbound_edges
    )
    s4 = vetted

    if s4:
        return "S4"
    if s3:
        return "S3"
    if s2:
        return "S2"
    if s1:
        return "S1"
    return "S0"
