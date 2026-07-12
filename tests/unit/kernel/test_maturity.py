import pytest

from akasha.kernel.maturity import InboundEdge, derive


def test_s0_default_no_inbound_edges():
    assert derive("claim", facet_count=0, vetted=False, inbound_edges=[]) == "S0"


def test_s0_boundary_below_s1_zero_inbound_edges_even_with_facets():
    # facets present but zero inbound edges: still S0, since S1 gates everything above it
    assert derive("definition", facet_count=3, vetted=False, inbound_edges=[]) == "S0"


def test_s1_one_inbound_edge_no_facets():
    edges = [InboundEdge(edge_type="composes", src_node_type="claim")]
    assert derive("claim", facet_count=0, vetted=False, inbound_edges=edges) == "S1"


def test_s1_boundary_below_s2_missing_facet_for_non_exempt_type():
    # 1 inbound edge + node_type set, but 0 facets and type is NOT task/entity => stays S1
    edges = [InboundEdge(edge_type="composes", src_node_type="claim")]
    assert derive("definition", facet_count=0, vetted=False, inbound_edges=edges) == "S1"


def test_s2_one_inbound_plus_type_plus_one_facet():
    edges = [InboundEdge(edge_type="composes", src_node_type="claim")]
    assert derive("definition", facet_count=1, vetted=False, inbound_edges=edges) == "S2"


@pytest.mark.parametrize("exempt_type", ["task", "entity"])
def test_s2_task_and_entity_exception_reach_s2_without_facets(exempt_type):
    edges = [InboundEdge(edge_type="composes", src_node_type="claim")]
    assert derive(exempt_type, facet_count=0, vetted=False, inbound_edges=edges) == "S2"


def test_s2_boundary_below_s3_justification_edge_from_non_evidence_source():
    # S2-eligible, has a justification edge, but its source is not evidence/proof => stays S2
    edges = [
        InboundEdge(edge_type="supports", src_node_type="claim"),
    ]
    assert derive("definition", facet_count=1, vetted=False, inbound_edges=edges) == "S2"


def test_s2_boundary_below_s3_non_justification_edge_from_evidence_source():
    # source is evidence, but the edge_type is not a justification type => stays S2
    edges = [
        InboundEdge(edge_type="composes", src_node_type="evidence"),
    ]
    assert derive("definition", facet_count=1, vetted=False, inbound_edges=edges) == "S2"


def test_s3_justification_edge_from_evidence_node():
    edges = [
        InboundEdge(edge_type="supports", src_node_type="evidence"),
    ]
    assert derive("definition", facet_count=1, vetted=False, inbound_edges=edges) == "S3"


def test_s3_justification_edge_from_proof_node():
    edges = [
        InboundEdge(edge_type="derived_from", src_node_type="proof"),
    ]
    assert derive("claim", facet_count=2, vetted=False, inbound_edges=edges) == "S3"


def test_s3_reached_alongside_other_non_qualifying_inbound_edges():
    edges = [
        InboundEdge(edge_type="composes", src_node_type="claim"),
        InboundEdge(edge_type="cites", src_node_type="proof"),
    ]
    assert derive("relation", facet_count=1, vetted=False, inbound_edges=edges) == "S3"


def test_s4_vetted_flag_set():
    edges = [
        InboundEdge(edge_type="cites", src_node_type="proof"),
    ]
    assert derive("definition", facet_count=1, vetted=True, inbound_edges=edges) == "S4"


def test_s4_vetted_flag_is_independent_of_lower_stages():
    # literal spec reading: "S4 iff vetted flag set" has no explicit
    # "S3-eligible AND ..." chain (unlike S2/S3), so vetted alone is S4
    # even with zero inbound edges.
    assert derive("claim", facet_count=0, vetted=True, inbound_edges=[]) == "S4"


def test_boundary_just_below_s4_not_vetted_stays_at_highest_satisfied_stage():
    edges = [
        InboundEdge(edge_type="supports", src_node_type="evidence"),
    ]
    assert derive("definition", facet_count=1, vetted=False, inbound_edges=edges) == "S3"


def test_maturity_is_highest_satisfied_stage_not_first_match():
    # S1, S2, S3 all satisfied simultaneously; S3 must win.
    edges = [
        InboundEdge(edge_type="depends_on", src_node_type="evidence"),
    ]
    assert derive("claim", facet_count=2, vetted=False, inbound_edges=edges) == "S3"
