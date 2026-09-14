from datetime import datetime, timedelta, timezone

from mule_log_tracer.anomaly import detect_anomalies
from mule_log_tracer.log_parser import LogEvent
from mule_log_tracer.tracer import build_traces

BASE = datetime(2026, 9, 13, 9, 0, 0, tzinfo=timezone.utc)
THRESHOLD_MS = 2000.0


def make_event(cid, offset_ms, flow="test-flow", level="INFO", message="msg", line=0):
    return LogEvent(
        timestamp=BASE + timedelta(milliseconds=offset_ms),
        correlation_id=cid,
        flow_name=flow,
        level=level,
        message=message,
        source_line=line,
    )


def one_trace(events):
    traces = build_traces(events)
    assert len(traces) == 1
    return traces[0]


# ---------------------------------------------------------------------------
# (a) error rule
# ---------------------------------------------------------------------------

def test_error_rule_triggers_on_error_level_event():
    events = [
        make_event("A", 0, level="INFO", message="start", line=1),
        make_event("A", 50, level="INFO", message="step 2", line=2),
        make_event("A", 100, level="ERROR", message="boom", line=3),
        make_event("A", 150, level="INFO", message="end", line=4),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_errors is True
    assert anomalies.error_step_indices == [2]
    # isolation: this scenario should not also trigger the other two rules
    assert anomalies.has_slow_steps is False
    assert anomalies.has_out_of_order is False


def test_error_rule_does_not_trigger_on_healthy_trace():
    events = [
        make_event("A", 0, level="INFO", message="start", line=1),
        make_event("A", 50, level="INFO", message="step 2", line=2),
        make_event("A", 100, level="INFO", message="end", line=3),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_errors is False
    assert anomalies.error_step_indices == []
    assert anomalies.is_anomalous is False


# ---------------------------------------------------------------------------
# (b) slow-step rule
# ---------------------------------------------------------------------------

def test_slow_step_rule_triggers_on_gap_exceeding_threshold():
    events = [
        make_event("A", 0, message="start", line=1),
        make_event("A", 100, message="fast step", line=2),
        make_event("A", 100 + 5000, message="after a long wait", line=3),  # 5000ms gap
        make_event("A", 100 + 5000 + 80, message="end", line=4),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_slow_steps is True
    assert len(anomalies.slow_steps) == 1
    assert anomalies.slow_steps[0].step_index == 2
    assert anomalies.slow_steps[0].delta_ms == 5000
    # isolation
    assert anomalies.has_errors is False
    assert anomalies.has_out_of_order is False


def test_slow_step_rule_does_not_trigger_when_all_gaps_are_small():
    events = [
        make_event("A", 0, message="start", line=1),
        make_event("A", 100, message="step 2", line=2),
        make_event("A", 250, message="step 3", line=3),
        make_event("A", 400, message="end", line=4),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_slow_steps is False
    assert anomalies.slow_steps == []
    assert anomalies.is_anomalous is False


def test_slow_step_threshold_is_configurable():
    events = [
        make_event("A", 0, message="start", line=1),
        make_event("A", 600, message="end", line=2),  # 600ms gap
    ]
    trace = one_trace(events)

    # default-ish threshold (2000ms): not slow
    assert detect_anomalies(trace, slow_threshold_ms=2000.0).has_slow_steps is False
    # lower threshold (500ms): now it is slow
    assert detect_anomalies(trace, slow_threshold_ms=500.0).has_slow_steps is True


# ---------------------------------------------------------------------------
# (c) out-of-order / clock-skew rule
# ---------------------------------------------------------------------------

def test_out_of_order_rule_triggers_when_arrival_order_contradicts_timestamps():
    # These arrive in the file in this order, but event 2's timestamp is
    # earlier than event 1's -- a clock-skew / out-of-order signal.
    events = [
        make_event("A", 200, message="arrived first, later timestamp", line=1),
        make_event("A", 100, message="arrived second, earlier timestamp", line=2),
        make_event("A", 300, message="arrived third, latest timestamp", line=3),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_out_of_order is True
    assert len(anomalies.out_of_order_pairs) == 1
    pair = anomalies.out_of_order_pairs[0]
    assert pair.earlier_arrival.message == "arrived first, later timestamp"
    assert pair.later_arrival.message == "arrived second, earlier timestamp"
    # isolation: gaps are small and no ERROR level present
    assert anomalies.has_errors is False
    assert anomalies.has_slow_steps is False


def test_out_of_order_rule_does_not_trigger_when_arrival_matches_timestamp_order():
    events = [
        make_event("A", 100, message="first", line=1),
        make_event("A", 200, message="second", line=2),
        make_event("A", 300, message="third", line=3),
    ]
    trace = one_trace(events)
    anomalies = detect_anomalies(trace, slow_threshold_ms=THRESHOLD_MS)

    assert anomalies.has_out_of_order is False
    assert anomalies.out_of_order_pairs == []
    assert anomalies.is_anomalous is False
