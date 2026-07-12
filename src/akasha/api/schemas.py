"""Re-export kernel domain models as the HTTP API's schema surface (T4.3).

Spec §8 (Phase-4 hook): ``api/schemas.py`` must be **re-exportable** so a
future MCP facade can import only the HTTP API's schema layer without pulling
in the kernel. To guarantee the HTTP schema never diverges from the kernel's
single source of truth (``kernel/model.py``), this module holds *only* thin
re-exports — no new fields, no renamed types, no parallel model definitions.
Anything that needs a domain model on the API side imports it from here.
"""

from __future__ import annotations

from akasha.kernel.model import (
    JUSTIFICATION_EDGE_TYPES,
    ChangeClass,
    Edge,
    EdgeType,
    Facet,
    Maturity,
    Node,
    NodeType,
)

__all__ = [
    "JUSTIFICATION_EDGE_TYPES",
    "ChangeClass",
    "Edge",
    "EdgeType",
    "Facet",
    "Maturity",
    "Node",
    "NodeType",
]
