"""Render reconstructed traces + anomalies as a readable text/Markdown report.

Pure formatting -- no LLM, no network. Two output formats:

- ``md`` (default): a Markdown waterfall per trace, anomalies visually
  flagged, plus a summary section.
- ``json``: a machine-readable dump of the same data, for piping into other
  tooling.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import List, Optional

from .anomaly import Anomalies, detect_anomalies
from .tracer import Trace


@dataclass
class TraceReport:
    trace: Trace
    anomalies: Anomalies


def build_trace_reports(traces: List[Trace], slow_threshold_ms: float) -> List[TraceReport]:
    """Run anomaly detection on every trace and sort errors-first, then slow, then healthy.

    Within each bucket, traces are ordered by correlation_id for stable,
    reproducible output.
    """
    reports = [TraceReport(trace=t, anomalies=detect_anomalies(t, slow_threshold_ms)) for t in traces]

    def sort_key(r: TraceReport):
        # Lower sorts first: errors first, then slow/out-of-order, then healthy.
        bucket = 0 if r.anomalies.has_errors else (1 if (r.anomalies.has_slow_steps or r.anomalies.has_out_of_order) else 2)
        return (bucket, r.trace.correlation_id)

    return sorted(reports, key=sort_key)


def _format_ts(dt) -> str:
    return dt.isoformat()


def _step_flags(report: TraceReport, step_index: int) -> List[str]:
    flags = []
    if step_index in report.anomalies.error_step_indices:
        flags.append("**ERROR**")
    for slow in report.anomalies.slow_steps:
        if slow.step_index == step_index:
            flags.append(f"**SLOW** (+{slow.delta_ms:.0f}ms)")
    return flags


def render_trace_markdown(report: TraceReport) -> str:
    trace = report.trace
    lines: List[str] = []
    status_bits = []
    if report.anomalies.has_errors:
        status_bits.append("⚠ ERROR")
    if report.anomalies.has_slow_steps:
        status_bits.append("⚠ SLOW STEP")
    if report.anomalies.has_out_of_order:
        status_bits.append("⚠ OUT-OF-ORDER")
    status = " | ".join(status_bits) if status_bits else "healthy"

    lines.append(f"## Trace `{trace.correlation_id}` -- {status}")
    lines.append("")
    lines.append(f"- Steps: {len(trace.steps)}")
    lines.append(f"- Start: {_format_ts(trace.start_time)}")
    lines.append(f"- End: {_format_ts(trace.end_time)}")
    lines.append(f"- Total duration: {trace.duration_ms:.0f}ms")
    lines.append("")
    lines.append("| # | elapsed | delta | level | flow | message |")
    lines.append("|---|---------|-------|-------|------|---------|")
    for step in trace.steps:
        flags = _step_flags(report, step.index)
        flag_text = " ".join(flags)
        level = step.event.level
        if step.event.is_error:
            level = f"**{level}**"
        message = step.event.message.replace("|", "\\|")
        flow = step.event.flow_name
        marker = "⚠ " if flags else ""
        lines.append(
            f"| {step.index + 1} | {step.elapsed_ms:.0f}ms | +{step.delta_ms:.0f}ms | {level} | {flow} | {marker}{message} {flag_text}".rstrip()
            + " |"
        )

    if report.anomalies.has_out_of_order:
        lines.append("")
        lines.append("**Out-of-order / clock-skew warning:** these log lines arrived in the "
                      "source file in an order inconsistent with their own timestamps:")
        for pair in report.anomalies.out_of_order_pairs:
            lines.append(
                f"- line {pair.earlier_arrival.source_line} "
                f"(`{_format_ts(pair.earlier_arrival.timestamp)}`) arrived before "
                f"line {pair.later_arrival.source_line} "
                f"(`{_format_ts(pair.later_arrival.timestamp)}`), but the second timestamp is earlier."
            )

    lines.append("")
    return "\n".join(lines)


def render_mermaid_sequence(report: TraceReport) -> str:
    """Optional bonus: a mermaid sequence diagram for one trace."""
    trace = report.trace
    lines = ["```mermaid", "sequenceDiagram", "    participant Request"]
    seen_flows = []
    for step in trace.steps:
        flow = step.event.flow_name
        if flow not in seen_flows:
            lines.append(f"    participant {_mermaid_safe(flow)}")
            seen_flows.append(flow)

    previous_participant = "Request"
    for step in trace.steps:
        flow = _mermaid_safe(step.event.flow_name)
        label = step.event.message.replace('"', "'")
        note = ""
        if step.event.is_error:
            note = " (ERROR)"
        elif step.index in [s.step_index for s in report.anomalies.slow_steps]:
            note = f" (+{step.delta_ms:.0f}ms SLOW)"
        lines.append(f"    {previous_participant}->>{flow}: [+{step.elapsed_ms:.0f}ms] {label}{note}")
        previous_participant = flow
    lines.append("```")
    return "\n".join(lines)


def _mermaid_safe(name: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in name) or "flow"


def render_markdown_report(
    reports: List[TraceReport],
    parse_stats=None,
    include_mermaid: bool = False,
    explanations: Optional[dict] = None,
) -> str:
    total = len(reports)
    with_errors = sum(1 for r in reports if r.anomalies.has_errors)
    with_slow = sum(1 for r in reports if r.anomalies.has_slow_steps)
    with_ooo = sum(1 for r in reports if r.anomalies.has_out_of_order)
    healthy = sum(1 for r in reports if not r.anomalies.is_anomalous)

    lines: List[str] = []
    lines.append("# mule-log-tracer report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Total traces: {total}")
    lines.append(f"- Traces with errors: {with_errors}")
    lines.append(f"- Traces with slow steps: {with_slow}")
    lines.append(f"- Traces with out-of-order/clock-skew events: {with_ooo}")
    lines.append(f"- Healthy traces: {healthy}")
    if parse_stats is not None:
        lines.append(f"- Log lines parsed: {parse_stats.parsed} (JSON: {parse_stats.parsed_json}, plaintext: {parse_stats.parsed_plaintext})")
        lines.append(f"- Log lines skipped (malformed): {parse_stats.skipped}")
    lines.append("")
    lines.append("---")
    lines.append("")

    for report in reports:
        lines.append(render_trace_markdown(report))
        if explanations and report.trace.correlation_id in explanations:
            lines.append("### AI explanation (optional, `--explain`)")
            lines.append("")
            lines.append(explanations[report.trace.correlation_id])
            lines.append("")
        if include_mermaid:
            lines.append("### Sequence diagram")
            lines.append("")
            lines.append(render_mermaid_sequence(report))
            lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def render_json_report(reports: List[TraceReport], parse_stats=None, explanations: Optional[dict] = None) -> str:
    total = len(reports)
    with_errors = sum(1 for r in reports if r.anomalies.has_errors)
    with_slow = sum(1 for r in reports if r.anomalies.has_slow_steps)
    with_ooo = sum(1 for r in reports if r.anomalies.has_out_of_order)
    healthy = sum(1 for r in reports if not r.anomalies.is_anomalous)

    data = {
        "summary": {
            "total_traces": total,
            "traces_with_errors": with_errors,
            "traces_with_slow_steps": with_slow,
            "traces_with_out_of_order": with_ooo,
            "healthy_traces": healthy,
        },
        "traces": [],
    }
    if parse_stats is not None:
        data["summary"]["log_lines_parsed"] = parse_stats.parsed
        data["summary"]["log_lines_parsed_json"] = parse_stats.parsed_json
        data["summary"]["log_lines_parsed_plaintext"] = parse_stats.parsed_plaintext
        data["summary"]["log_lines_skipped"] = parse_stats.skipped

    for report in reports:
        trace = report.trace
        trace_data = {
            "correlation_id": trace.correlation_id,
            "start_time": trace.start_time.isoformat(),
            "end_time": trace.end_time.isoformat(),
            "duration_ms": trace.duration_ms,
            "has_errors": report.anomalies.has_errors,
            "has_slow_steps": report.anomalies.has_slow_steps,
            "has_out_of_order": report.anomalies.has_out_of_order,
            "steps": [
                {
                    "index": step.index,
                    "timestamp": step.event.timestamp.isoformat(),
                    "elapsed_ms": step.elapsed_ms,
                    "delta_ms": step.delta_ms,
                    "level": step.event.level,
                    "flow_name": step.event.flow_name,
                    "message": step.event.message,
                    "processor_path": step.event.processor_path,
                    "event": step.event.event,
                    "is_error": step.event.is_error,
                    "is_slow": step.index in [s.step_index for s in report.anomalies.slow_steps],
                }
                for step in trace.steps
            ],
            "out_of_order_pairs": [
                {
                    "earlier_arrival_line": p.earlier_arrival.source_line,
                    "earlier_arrival_timestamp": p.earlier_arrival.timestamp.isoformat(),
                    "later_arrival_line": p.later_arrival.source_line,
                    "later_arrival_timestamp": p.later_arrival.timestamp.isoformat(),
                }
                for p in report.anomalies.out_of_order_pairs
            ],
        }
        if explanations and trace.correlation_id in explanations:
            trace_data["explanation"] = explanations[trace.correlation_id]
        data["traces"].append(trace_data)

    return json.dumps(data, indent=2)
