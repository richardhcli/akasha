import pytest
from pydantic import ValidationError

from akasha.kernel.model import (
    JUSTIFICATION_EDGE_TYPES,
    Edge,
    Facet,
    Node,
)


def test_justification_edge_type_constant():
    assert JUSTIFICATION_EDGE_TYPES == {
        "supports",
        "contradicts",
        "depends_on",
        "derived_from",
        "cites",
    }


def test_node_defaults():
    node = Node(id="aaaaaaaa", node_type="claim", body="hello")
    assert node.facets == []
    assert node.task_state is None
    assert node.vetted is False
    assert node.status == "live"


def test_facet_instantiates():
    facet = Facet(facet_id="bbbbbbbb", name="def", span="the span text", version=1)
    assert facet.version == 1


def test_node_with_facets():
    facet = Facet(facet_id="bbbbbbbb", name="def", span="span", version=0)
    node = Node(id="aaaaaaaa", node_type="definition", body="body", facets=[facet])
    assert node.facets[0].name == "def"


def test_edge_defaults():
    edge = Edge(
        id="cccccccc",
        src="aaaaaaaa",
        dst="bbbbbbbb",
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    assert edge.mode == "track"
    assert edge.pinned_commit is None


def test_justification_edge_requires_facet_binding():
    with pytest.raises(ValidationError):
        Edge(
            id="cccccccc",
            src="aaaaaaaa",
            dst="bbbbbbbb",
            edge_type="supports",
            facet_binding=None,
            provenance="human",
        )


def test_justification_edge_accepts_wildcard_binding():
    edge = Edge(
        id="cccccccc",
        src="aaaaaaaa",
        dst="bbbbbbbb",
        edge_type="supports",
        facet_binding="*",
        provenance="human",
    )
    assert edge.facet_binding == "*"


def test_justification_edge_accepts_facet_id_binding():
    edge = Edge(
        id="cccccccc",
        src="aaaaaaaa",
        dst="bbbbbbbb",
        edge_type="contradicts",
        facet_binding="ddddddddd",
        provenance="agent_approved",
    )
    assert edge.facet_binding == "ddddddddd"


def test_composes_edge_allows_none_facet_binding():
    edge = Edge(
        id="cccccccc",
        src="aaaaaaaa",
        dst="bbbbbbbb",
        edge_type="composes",
        facet_binding=None,
        provenance="human",
    )
    assert edge.facet_binding is None


def test_redirects_to_edge_allows_none_facet_binding():
    edge = Edge(
        id="cccccccc",
        src="aaaaaaaa",
        dst="bbbbbbbb",
        edge_type="redirects_to",
        facet_binding=None,
        provenance="imported",
    )
    assert edge.facet_binding is None


@pytest.mark.parametrize(
    "edge_type", ["supports", "contradicts", "depends_on", "derived_from", "cites"]
)
def test_all_justification_edge_types_reject_none_binding(edge_type):
    with pytest.raises(ValidationError):
        Edge(
            id="cccccccc",
            src="aaaaaaaa",
            dst="bbbbbbbb",
            edge_type=edge_type,
            facet_binding=None,
            provenance="human",
        )
