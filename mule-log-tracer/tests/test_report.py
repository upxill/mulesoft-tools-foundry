import json
from datetime import datetime, timedelta, timezone

from mule_log_tracer.log_parser import LogEvent
from mule_log_tracer.report import build_trace_reports, render_json_report, render_markdown_report
from mule_log_tracer.tracer import build_traces

BASE = datetime(2026, 9, 13, 9, 0, 0, tzinfo=timezone.utc)


def make_event(cid, offset_ms, flow="test-flow", level="INFO", message="msg", line=0):
    return LogEvent(
        timestamp=BASE + timedelta(milliseconds=offset_ms),
        correlation_id=cid,
        flow_name=flow,
        level=level,
        message=message,
        source_line=line,
    )


def test_build_trace_reports_orders_errors_first():
    events = [
        make_event("healthy", 0, message="start", line=1),
        make_event("healthy", 50, message="end", line=2),
        make_event("errored", 0, message="start", line=3),
        make_event("errored", 50, level="ERROR", message="boom", line=4),
    ]
    traces = build_traces(events)
    reports = build_trace_reports(traces, slow_threshold_ms=2000.0)
    assert reports[0].trace.correlation_id == "errored"
    assert reports[1].trace.correlation_id == "healthy"


def test_render_markdown_report_flags_error_and_summary():
    events = [
        make_event("errored", 0, message="start", line=1),
        make_event("errored", 50, level="ERROR", message="boom", line=2),
    ]
    traces = build_traces(events)
    reports = build_trace_reports(traces, slow_threshold_ms=2000.0)
    md = render_markdown_report(reports)

    assert "**ERROR**" in md
    assert "Total traces: 1" in md
    assert "Traces with errors: 1" in md


def test_render_json_report_structure():
    events = [
        make_event("A", 0, message="start", line=1),
        make_event("A", 50, message="end", line=2),
    ]
    traces = build_traces(events)
    reports = build_trace_reports(traces, slow_threshold_ms=2000.0)
    payload = json.loads(render_json_report(reports))

    assert payload["summary"]["total_traces"] == 1
    assert payload["traces"][0]["correlation_id"] == "A"
    assert len(payload["traces"][0]["steps"]) == 2
