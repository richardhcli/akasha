"""Maturity stage derivation: a pure function per spec §4.6.

Computes S0-S4 from the node's own fields (``node_type``, ``facets``, ``vetted``) and its LIVE
inbound edges, each reduced to its ``edge_type`` and its source's ``node_type`` (``InboundEdge``).
No database access, no ``store.py`` import: filtering to live edges and resolving source types is
the caller's job (rule 0.4).

# SPEC-QUESTION (T1.5): §4.6 also says maturity is recomputed inside the same transaction as any
# mutation that changes its inputs. That wiring lives in ``store.py`` (T1.6), which T1.5 did not
# own; only the pure derivation is here.
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
    """One LIVE inbound edge reduced to the fields maturity needs. Callers MUST pre-filter out
    retracted edges: this module has no concept of retraction (spec §4.6: "S1 iff live inbound edge
    count >= 1").
    """

    edge_type: EdgeType
    src_node_type: NodeType


def derive(
    node_type: NodeType,
    facet_count: int,
    vetted: bool,
    inbound_edges: list[InboundEdge],
) -> Maturity:
    """Derive the maturity stage (spec §4.6): the highest of S1-S4 whose condition holds, else S0.

    * S1: at least one inbound edge (all assumed live).
    * S2: S1 and (``node_type`` in {"task", "entity"} or ``facet_count >= 1``); "node_type set" is
      always true for a validated ``Node``.
    * S3: S2 and an inbound justification-type edge whose ``src_node_type`` is "evidence" or
      "proof".
    * S4: ``vetted``: independent of S1-S3 per the literal spec text (§4.6 does not chain it).

    Pure: no database access, no mutation of any argument.
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
