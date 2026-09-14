"""Verifies the bundled example log file produces exactly the three trace
outcomes it was designed to demonstrate: one healthy trace, one trace with a
genuine slow step, and one trace with a real ERROR event. This exercises the
full deterministic pipeline (log_parser -> tracer -> anomaly) against a
realistic, interleaved multi-request log file -- not just synthetic
single-rule fixtures.
"""

import os

from mule_log_tracer.anomaly import detect_anomalies
from mule_log_tracer.log_parser import parse_log_file
from mule_log_tracer.tracer import build_traces

EXAMPLE_PATH = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_cloudhub_logs.jsonl")

HEALTHY_CID = "a47ac10b-58cc-4372-a567-0e02b2c3d479"
SLOW_CID = "b58ac10b-58cc-4372-a567-0e02b2c3d480"
ERROR_CID = "c69ac10b-58cc-4372-a567-0e02b2c3d481"


def test_example_log_has_at_least_three_interleaved_correlation_ids():
    events, stats = parse_log_file(EXAMPLE_PATH)
    assert stats.skipped == 0, "the bundled example should parse cleanly"
    correlation_ids_in_order = [e.correlation_id for e in events]
    distinct = set(correlation_ids_in_order)
    assert len(distinct) >= 3

    # Confirm the lines are genuinely interleaved (not grouped contiguously
    # by correlation id) -- the first several lines should already reference
    # more than one correlation id.
    first_five_ids = set(correlation_ids_in_order[:5])
    assert len(first_five_ids) >= 2


def test_example_produces_exactly_one_healthy_one_slow_one_error_trace():
    events, stats = parse_log_file(EXAMPLE_PATH)
    traces = build_traces(events)
    assert len(traces) == 3

    by_id = {t.correlation_id: detect_anomalies(t) for t in traces}

    assert set(by_id.keys()) == {HEALTHY_CID, SLOW_CID, ERROR_CID}

    healthy = by_id[HEALTHY_CID]
    slow = by_id[SLOW_CID]
    error = by_id[ERROR_CID]

    # The healthy trace: no anomalies at all.
    assert healthy.is_anomalous is False
    assert healthy.has_errors is False
    assert healthy.has_slow_steps is False

    # The slow trace: flagged for a slow step, but no ERROR-level event.
    assert slow.has_slow_steps is True
    assert slow.has_errors is False

    # The error trace: flagged for an ERROR-level event.
    assert error.has_errors is True

    # Exactly one trace of each kind -- the scenario the example was built to prove.
    outcomes = [t for t in (healthy, slow, error)]
    assert sum(1 for o in outcomes if o.is_anomalous is False) == 1
    assert sum(1 for o in outcomes if o.has_slow_steps) == 1
    assert sum(1 for o in outcomes if o.has_errors) == 1


def test_example_report_file_is_checked_in_and_mentions_all_three_traces():
    report_path = os.path.join(os.path.dirname(__file__), "..", "examples", "sample_report.md")
    assert os.path.exists(report_path), "run the CLI against the example and check in the generated report"
    content = open(report_path, encoding="utf-8").read()
    assert HEALTHY_CID in content
    assert SLOW_CID in content
    assert ERROR_CID in content
