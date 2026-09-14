"""Offline tests for mermaid rendering from a small synthetic flow-ref graph."""

from mule_flow_doctor import graph
from mule_flow_doctor.xml_parser import ConnectorCall, FlowInfo, ParsedApp


def _synthetic_app() -> ParsedApp:
    api_flow = FlowInfo(
        name="api-flow",
        kind="flow",
        file="api.xml",
        has_error_handler=False,
        is_entry_point=True,
        entry_point_type="http:listener",
        flow_refs=["helper-subflow"],
        connector_calls=[
            ConnectorCall(
                file="api.xml",
                flow_name="api-flow",
                operation="http:request",
                config_ref="backendConfig",
                has_reconnection=False,
            )
        ],
    )
    helper_flow = FlowInfo(
        name="helper-subflow",
        kind="sub-flow",
        file="helper.xml",
        has_error_handler=True,
        is_entry_point=False,
        flow_refs=["missing-subflow"],
    )

    return ParsedApp(
        files=["api.xml", "helper.xml"],
        flows={"api-flow": api_flow, "helper-subflow": helper_flow},
        connector_calls=list(api_flow.connector_calls),
        secrets=[],
        dataweave_issues=[],
        edges=[("api-flow", "helper-subflow"), ("helper-subflow", "missing-subflow")],
        unresolved_configs=[],
    )


def test_build_flow_graph_nodes_and_edges():
    parsed = _synthetic_app()
    nodes, edges = graph.build_flow_graph(parsed)

    assert set(nodes.keys()) == {"api-flow", "helper-subflow", "missing-subflow"}
    assert nodes["missing-subflow"].kind == "external"
    assert ("api-flow", "helper-subflow") in edges
    assert ("helper-subflow", "missing-subflow") in edges


def test_render_mermaid_structure():
    parsed = _synthetic_app()
    nodes, edges = graph.build_flow_graph(parsed)
    mermaid = graph.render_mermaid(nodes, edges)

    assert mermaid.startswith("flowchart TD")
    # Every node must appear as a mermaid node declaration.
    assert "n_api_flow" in mermaid
    assert "n_helper_subflow" in mermaid
    assert "n_missing_subflow" in mermaid
    # Edges rendered with the flow-ref label.
    assert "n_api_flow -->|flow-ref| n_helper_subflow" in mermaid
    assert "n_helper_subflow -->|flow-ref| n_missing_subflow" in mermaid
    # Entry point marker and missing-error-handler marker show up on api-flow.
    assert "http:listener" in mermaid
    assert "no error-handler" in mermaid
    # The dangling flow-ref target is styled as external.
    assert "class n_missing_subflow external;" in mermaid
    assert "classDef external" in mermaid


def test_render_architecture_markdown_contains_mermaid_and_summary():
    parsed = _synthetic_app()
    md = graph.render_architecture_markdown(parsed)

    assert "```mermaid" in md
    assert "flowchart TD" in md
    assert "api-flow" in md
    assert "**NO error-handler**" in md
    assert "**no reconnection strategy**" in md
    assert "Dangling flow-ref targets" in md
    assert "missing-subflow" in md
