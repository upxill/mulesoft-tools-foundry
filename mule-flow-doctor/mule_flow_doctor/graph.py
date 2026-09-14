"""Build and render the real flow dependency graph.

Everything here operates on the :class:`~mule_flow_doctor.xml_parser.ParsedApp`
produced by deterministic XML parsing -- there is no LLM involvement. The
resulting mermaid diagram is a 1:1 rendering of the <flow-ref> edges and
listener/source elements actually present in the app's XML.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from mule_flow_doctor.xml_parser import ParsedApp


@dataclass
class GraphNode:
    name: str
    kind: str  # "flow" | "sub-flow" | "external" (referenced but never defined)
    has_error_handler: bool = False
    is_entry_point: bool = False
    entry_point_type: Optional[str] = None


def build_flow_graph(parsed: ParsedApp) -> Tuple[Dict[str, GraphNode], List[Tuple[str, str]]]:
    """Return (nodes-by-name, edges) purely from parsed structure."""
    nodes: Dict[str, GraphNode] = {}
    for name, info in parsed.flows.items():
        nodes[name] = GraphNode(
            name=name,
            kind=info.kind,
            has_error_handler=info.has_error_handler,
            is_entry_point=info.is_entry_point,
            entry_point_type=info.entry_point_type,
        )

    edges: List[Tuple[str, str]] = []
    for src, dst in parsed.edges:
        edges.append((src, dst))
        if dst not in nodes:
            # Referenced via flow-ref but not defined anywhere we parsed --
            # still a real, observable fact about the app, so show it.
            nodes[dst] = GraphNode(name=dst, kind="external")

    return nodes, edges


def _mermaid_id(name: str) -> str:
    return "n_" + re.sub(r"[^A-Za-z0-9_]", "_", name)


def render_mermaid(nodes: Dict[str, GraphNode], edges: List[Tuple[str, str]]) -> str:
    """Render a mermaid ``flowchart`` from the real flow-ref graph.

    - Flows with an external listener/source (HTTP, JMS, scheduler, ...)
      get the ``entrypoint`` style and an entry-point marker in the label.
    - Flows with no <error-handler> anywhere in their subtree get a
      dashed/red ``noerrorhandler`` outline and a warning marker.
    - sub-flows get the ``subflow`` style.
    - Names referenced via <flow-ref> but never defined anywhere in the
      parsed app get the ``external`` style, so a dangling reference is
      visible rather than silently dropped.
    """
    lines = ["flowchart TD"]

    for name in sorted(nodes):
        node = nodes[name]
        node_id = _mermaid_id(name)
        label_parts = [name]
        if node.kind == "external":
            label_parts.append("(undefined)")
        else:
            label_parts.append(f"({node.kind})")
        if node.is_entry_point:
            label_parts.append(f"\U0001F310 {node.entry_point_type}")
        if node.kind != "external" and not node.has_error_handler:
            label_parts.append("⚠ no error-handler")
        label = "<br/>".join(label_parts)
        lines.append(f'    {node_id}["{label}"]')

        if node.kind == "external":
            css_class = "external"
        elif node.is_entry_point:
            css_class = "entrypoint"
        elif node.kind == "sub-flow":
            css_class = "subflow"
        else:
            css_class = "internal"
        lines.append(f"    class {node_id} {css_class};")

    for src, dst in edges:
        lines.append(f"    {_mermaid_id(src)} -->|flow-ref| {_mermaid_id(dst)}")

    lines.append("")
    lines.append("    classDef entrypoint fill:#dbeafe,stroke:#2563eb,stroke-width:2px;")
    lines.append("    classDef subflow fill:#f1f5f9,stroke:#64748b,stroke-width:1px;")
    lines.append("    classDef internal fill:#ffffff,stroke:#64748b,stroke-width:1px;")
    lines.append(
        "    classDef external fill:#fee2e2,stroke:#dc2626,stroke-width:1px,stroke-dasharray: 4 2;"
    )

    return "\n".join(lines)


def render_architecture_markdown(parsed: ParsedApp) -> str:
    """Render the full architecture.md content: the mermaid diagram plus a
    plain-text summary of what was actually found, all derived from the
    real parsed XML (no LLM).
    """
    nodes, edges = build_flow_graph(parsed)
    mermaid = render_mermaid(nodes, edges)

    lines: List[str] = []
    lines.append("# Architecture (derived from real flow-ref graph)")
    lines.append("")
    lines.append(
        "This diagram and summary are generated entirely by deterministic XML "
        "parsing of the actual application files -- no LLM is involved. See "
        "`mule_flow_doctor/xml_parser.py` and `mule_flow_doctor/graph.py`."
    )
    lines.append("")
    lines.append("```mermaid")
    lines.append(mermaid)
    lines.append("```")
    lines.append("")

    lines.append("## Flows")
    lines.append("")
    for name in sorted(n for n in nodes if nodes[n].kind != "external"):
        info = parsed.flows[name]
        entry = f"entry point ({info.entry_point_type})" if info.is_entry_point else "internal only"
        eh = "has error-handler" if info.has_error_handler else "**NO error-handler**"
        lines.append(f"- `{name}` ({info.kind}, `{info.file}`) -- {entry}; {eh}")
        for call in info.connector_calls:
            recon = (
                "reconnection configured"
                if call.has_reconnection
                else "**no reconnection strategy**"
            )
            lines.append(f"  - `{call.operation}` (config-ref=`{call.config_ref}`) -- {recon}")
    lines.append("")

    external = sorted(n for n in nodes if nodes[n].kind == "external")
    if external:
        lines.append("## Dangling flow-ref targets (referenced but not found in the parsed XML)")
        lines.append("")
        for name in external:
            lines.append(f"- `{name}`")
        lines.append("")

    if parsed.secrets:
        lines.append("## Hardcoded-secret-looking values found")
        lines.append("")
        for s in parsed.secrets:
            ctx = f" (flow `{s.flow_name}`)" if s.flow_name else " (global config)"
            lines.append(f"- `{s.file}`{ctx}: `{s.location}` = `{s.value_preview}`")
        lines.append("")

    if parsed.dataweave_issues:
        lines.append("## Potentially inefficient DataWeave transforms")
        lines.append("")
        for d in parsed.dataweave_issues:
            lines.append(
                f"- `{d.file}` flow `{d.flow_name}`, `{d.element}`: "
                f"`{d.collection}` is scanned {d.pass_count} times ({d.kind})"
            )
        lines.append("")

    return "\n".join(lines)
