"""`mule-log-tracer` CLI entry point.

    mule-log-tracer trace logs.jsonl --out report.md \
        [--correlation-id <id>] [--slow-threshold-ms 2000] \
        [--format md|json] [--explain] [--model claude-opus-5] [--mermaid]

The trace/anomaly-detection pipeline (`log_parser.py` -> `tracer.py` ->
`anomaly.py` -> `report.py`) never touches the network or an API key. Only
`--explain` does, and it is off by default (see `explain.py`).
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional

from .anomaly import DEFAULT_SLOW_THRESHOLD_MS
from .explain import DEFAULT_MODEL, dry_run_explain, explain_trace
from .log_parser import parse_log_file
from .report import build_trace_reports, render_json_report, render_markdown_report
from .tracer import build_traces


def _get_explain_client():
    """Try to build a real Anthropic client. Returns None if unavailable
    (no package installed, or no credentials) -- never raises."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return None

    if not os.environ.get("ANTHROPIC_API_KEY") and not os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return None

    try:
        return anthropic.Anthropic()
    except Exception:
        return None


def cmd_trace(args: argparse.Namespace) -> int:
    events, stats = parse_log_file(args.log_file)

    print(f"Parsed {stats.parsed} log line(s) ({stats.parsed_json} JSON, {stats.parsed_plaintext} plaintext).")
    if stats.skipped:
        print(f"Skipped {stats.skipped} malformed/unparseable line(s).")
    if stats.blank_lines:
        print(f"Ignored {stats.blank_lines} blank line(s).")

    if not events:
        print("No parseable log events found -- nothing to trace.")
        return 1

    traces = build_traces(events)

    if args.correlation_id:
        traces = [t for t in traces if t.correlation_id == args.correlation_id]
        if not traces:
            print(f"No trace found for correlationId={args.correlation_id}")
            return 1

    reports = build_trace_reports(traces, slow_threshold_ms=args.slow_threshold_ms)

    explanations = {}
    if args.explain:
        anomalous_reports = [r for r in reports if r.anomalies.is_anomalous]
        client = _get_explain_client()
        if client is None:
            print(
                "--explain set: no Anthropic API key found in this environment "
                "(ANTHROPIC_API_KEY/ANTHROPIC_AUTH_TOKEN unset, or the `anthropic` "
                "package is not installed) -- generating DISCLOSED DRY-RUN "
                "explanations synthesized from the deterministic anomaly data "
                "instead of calling Claude."
            )
            for r in anomalous_reports:
                explanations[r.trace.correlation_id] = dry_run_explain(r.trace, r.anomalies)
        else:
            print(f"--explain set: calling Claude ({args.model}) for {len(anomalous_reports)} anomalous trace(s).")
            for r in anomalous_reports:
                try:
                    explanations[r.trace.correlation_id] = explain_trace(client, r.trace, r.anomalies, model=args.model)
                except Exception as exc:  # noqa: BLE001
                    print(f"  explain call failed for {r.trace.correlation_id}: {exc}; falling back to dry run.")
                    explanations[r.trace.correlation_id] = dry_run_explain(r.trace, r.anomalies)

    if args.format == "json":
        output = render_json_report(reports, parse_stats=stats, explanations=explanations)
    else:
        output = render_markdown_report(reports, parse_stats=stats, include_mermaid=args.mermaid, explanations=explanations)

    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(output)
        print(f"Wrote {args.out}")
    else:
        print(output)

    total = len(reports)
    with_errors = sum(1 for r in reports if r.anomalies.has_errors)
    with_slow = sum(1 for r in reports if r.anomalies.has_slow_steps)
    with_ooo = sum(1 for r in reports if r.anomalies.has_out_of_order)
    print(
        f"Traces: {total} total, {with_errors} with errors, {with_slow} with slow steps, "
        f"{with_ooo} with out-of-order events."
    )

    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule-log-tracer",
        description=(
            "Reconstruct per-request waterfall traces from interleaved Mule/CloudHub-style "
            "JSON-lines (or plaintext) application logs, grouped by correlation ID. The core "
            "engine (parsing, grouping, timeline reconstruction, anomaly detection) is 100%% "
            "deterministic and needs no API key."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    trace_parser = subparsers.add_parser("trace", help="Trace requests from a log file by correlation ID.")
    trace_parser.add_argument("log_file", help="Path to a JSON-lines (or plaintext-fallback) log file.")
    trace_parser.add_argument("--out", help="Path to write the report to (default: print to stdout).")
    trace_parser.add_argument("--correlation-id", help="Only report the trace for this correlation ID.")
    trace_parser.add_argument(
        "--slow-threshold-ms",
        type=float,
        default=DEFAULT_SLOW_THRESHOLD_MS,
        help=f"Gap (ms) between consecutive events that counts as a slow step (default: {DEFAULT_SLOW_THRESHOLD_MS:.0f}).",
    )
    trace_parser.add_argument("--format", choices=["md", "json"], default="md", help="Report output format (default: md).")
    trace_parser.add_argument(
        "--explain",
        action="store_true",
        help="Optional: ask Claude for a one-paragraph summary of anomalous traces. Off by default; needs no API key otherwise.",
    )
    trace_parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL),
        help=f"Anthropic model to use with --explain (default: {DEFAULT_MODEL}, or $ANTHROPIC_MODEL).",
    )
    trace_parser.add_argument("--mermaid", action="store_true", help="Also emit a mermaid sequence diagram per trace (md format only).")
    trace_parser.set_defaults(func=cmd_trace)

    return parser


def main(argv: Optional[list] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
