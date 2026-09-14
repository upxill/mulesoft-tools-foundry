from datetime import datetime, timedelta, timezone

from mule_log_tracer.log_parser import LogEvent
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


def test_build_traces_groups_by_correlation_id():
    events = [
        make_event("A", 0, line=1),
        make_event("B", 10, line=2),
        make_event("A", 20, line=3),
        make_event("B", 30, line=4),
    ]
    traces = build_traces(events)
    ids = {t.correlation_id for t in traces}
    assert ids == {"A", "B"}
    trace_a = next(t for t in traces if t.correlation_id == "A")
    trace_b = next(t for t in traces if t.correlation_id == "B")
    assert len(trace_a.steps) == 2
    assert len(trace_b.steps) == 2


def test_build_traces_sorts_by_timestamp_even_if_interleaved_in_input():
    # Deliberately fed out of chronological order for correlation id "A".
    events = [
        make_event("A", 100, message="second", line=1),
        make_event("A", 0, message="first", line=2),
    ]
    traces = build_traces(events)
    trace = traces[0]
    assert [s.event.message for s in trace.steps] == ["first", "second"]


def test_build_traces_computes_delta_and_elapsed_ms():
    events = [
        make_event("A", 0, line=1),
        make_event("A", 150, line=2),
        make_event("A", 400, line=3),
    ]
    traces = build_traces(events)
    trace = traces[0]
    assert trace.steps[0].delta_ms == 0
    assert trace.steps[0].elapsed_ms == 0
    assert trace.steps[1].delta_ms == 150
    assert trace.steps[1].elapsed_ms == 150
    assert trace.steps[2].delta_ms == 250
    assert trace.steps[2].elapsed_ms == 400


def test_build_traces_preserves_arrival_order_distinct_from_sorted_steps():
    # File arrival order differs from chronological order.
    events = [
        make_event("A", 100, message="arrived-first-but-later-timestamp", line=1),
        make_event("A", 0, message="arrived-second-but-earlier-timestamp", line=2),
    ]
    traces = build_traces(events)
    trace = traces[0]
    # arrival_events keeps the original (file) order:
    assert [e.message for e in trace.arrival_events] == [
        "arrived-first-but-later-timestamp",
        "arrived-second-but-earlier-timestamp",
    ]
    # steps is sorted chronologically:
    assert [s.event.message for s in trace.steps] == [
        "arrived-second-but-earlier-timestamp",
        "arrived-first-but-later-timestamp",
    ]


def test_trace_has_errors_property():
    events = [
        make_event("A", 0, level="INFO", line=1),
        make_event("A", 10, level="ERROR", line=2),
    ]
    trace = build_traces(events)[0]
    assert trace.has_errors is True

    healthy_events = [
        make_event("B", 0, level="INFO", line=1),
        make_event("B", 10, level="INFO", line=2),
    ]
    healthy_trace = build_traces(healthy_events)[0]
    assert healthy_trace.has_errors is False
