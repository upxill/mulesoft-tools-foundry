"""Human-readable CLI summary of a generate run.

Pure formatting over already-computed data -- no parsing or generation
logic lives here.
"""

from __future__ import annotations

from typing import List

from .xml_parser import ParsedApp, dedupe_calls
from .scaffold import flows_in_scope


def build_summary(parsed: ParsedApp, include_subflows: bool) -> str:
    lines: List[str] = []
    flows = flows_in_scope(parsed, include_subflows)

    total_raw_calls = 0
    total_distinct_calls = 0

    for flow in flows:
        distinct = dedupe_calls(flow.outbound_calls)
        total_raw_calls += len(flow.outbound_calls)
        total_distinct_calls += len(distinct)

        entry = f" [{flow.entry_point_type}]" if flow.entry_point_type else ""
        lines.append(
            f"  - {flow.name} ({flow.kind}{entry}): "
            f"{len(flow.outbound_calls)} outbound call(s) found, "
            f"{len(distinct)} distinct -> {len(distinct)} mock-when generated"
        )
        for call in distinct:
            disambiguator = ""
            if call.config_ref:
                disambiguator = f" [config-ref={call.config_ref}]"
            elif call.doc_name:
                disambiguator = f" [doc:name={call.doc_name}]"
            lines.append(f"      * {call.operation}{disambiguator}")

    skipped = [f for f in parsed.flows.values() if f.kind == "sub-flow" and not include_subflows]

    header = (
        f"Flows processed: {len(flows)}\n"
        f"Outbound calls found (raw): {total_raw_calls}\n"
        f"Distinct outbound calls (mock-when blocks generated): {total_distinct_calls}"
    )
    if skipped:
        header += (
            f"\nSub-flows skipped (pass --include-subflows to also scaffold "
            f"these): {', '.join(f.name for f in skipped)}"
        )

    body = "\n".join(lines) if lines else "  (no flows in scope)"
    return header + "\n\nPer-flow detail:\n" + body
