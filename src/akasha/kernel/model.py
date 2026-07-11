"""Node, Edge, Facet pydantic models — single source of truth (spec §4.2).

This module only defines and validates in-memory shapes; it never touches
the database (build-plan rule 0.4 — all persistent writes go through
``kernel/store.py``).
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, model_validator

NodeType = Literal["entity", "definition", "claim", "relation", "proof", "evidence", "task"]
EdgeType = Literal[
    "composes", "supports", "contradicts", "depends_on", "derived_from", "cites", "redirects_to"
]
Maturity = Literal["S0", "S1", "S2", "S3", "S4"]
ChangeClass = Literal["patch", "minor", "major"]

# Edge types that participate in the "justification" relation set (spec §4.2).
# These require a non-None facet_binding (facet_id or "*"); "*" bindings are
# legal but counted against the facet-coverage metric (spec §7).
JUSTIFICATION_EDGE_TYPES: frozenset[EdgeType] = frozenset(
    {"supports", "contradicts", "depends_on", "derived_from", "cites"}
)


class Facet(BaseModel):
    facet_id: str  # id8, minted like node ids
    name: str  # short label, unique per node
    span: str  # the highlighted span of the definition (facets-from-spans)
    version: int  # bumped on interface break of this facet


class Node(BaseModel):
    id: str  # id8
    node_type: NodeType
    body: str  # canonical text (§4.3)
    facets: list[Facet] = []
    task_state: Literal["open", "done"] | None = None  # tasks only
    vetted: bool = False  # S4 flag
    status: Literal["live", "retracted", "tombstone"] = "live"


class Edge(BaseModel):
    id: str
    src: str
    dst: str
    edge_type: EdgeType
    facet_binding: str | None  # REQUIRED (facet_id or "*") for justification
    # edge types; None allowed only for composes/redirects_to
    provenance: Literal["human", "agent_approved", "imported"]
    mode: Literal["track", "pin"] = "track"
    pinned_commit: str | None = None

    @model_validator(mode="after")
    def _check_facet_binding(self) -> "Edge":
        if self.edge_type in JUSTIFICATION_EDGE_TYPES and self.facet_binding is None:
            raise ValueError(
                f"edge_type {self.edge_type!r} is a justification edge type and "
                "requires a facet_binding (facet_id or '*'); None is only "
                "allowed for composes/redirects_to"
            )
        return self
