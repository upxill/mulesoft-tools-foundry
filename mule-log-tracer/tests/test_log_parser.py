import json
import os
import tempfile

import pytest

from mule_log_tracer.log_parser import (
    ParseStats,
    parse_line,
    parse_log_file,
)


def test_parse_jsonl_valid_line():
    line = json.dumps(
        {
            "timestamp": "2026-09-13T09:00:00.000Z",
            "correlationId": "abc-123",
            "flowName": "orders-process-flow",
            "level": "info",
            "message": "hello",
        }
    )
    event = parse_line(line, 1)
    assert event.correlation_id == "abc-123"
    assert event.flow_name == "orders-process-flow"
    assert event.level == "INFO"  # normalized to uppercase
    assert event.message == "hello"
    assert event.timestamp.year == 2026


def test_parse_jsonl_optional_fields():
    line = json.dumps(
        {
            "timestamp": "2026-09-13T09:00:00.000Z",
            "correlationId": "abc-123",
            "flowName": "orders-process-flow",
            "level": "INFO",
            "message": "hello",
            "processorPath": "orders-process-flow/processors/0",
            "event": "flow-start",
        }
    )
    event = parse_line(line, 1)
    assert event.processor_path == "orders-process-flow/processors/0"
    assert event.event == "flow-start"


def test_parse_jsonl_missing_required_field_raises():
    line = json.dumps({"timestamp": "2026-09-13T09:00:00.000Z", "flowName": "f", "level": "INFO", "message": "m"})
    with pytest.raises(ValueError):
        parse_line(line, 1)


def test_parse_jsonl_not_an_object_raises():
    with pytest.raises(ValueError):
        parse_line("[1, 2, 3]", 1)


def test_parse_jsonl_invalid_json_raises():
    with pytest.raises(Exception):
        parse_line("{not valid json", 1)


def test_parse_plaintext_valid_line():
    line = "2026-09-13T09:00:00.000Z INFO [orders-process-flow] correlationId=req-100 - Flow started"
    event = parse_line(line, 1)
    assert event.correlation_id == "req-100"
    assert event.flow_name == "orders-process-flow"
    assert event.level == "INFO"
    assert event.message == "Flow started"


def test_parse_plaintext_without_message_still_parses():
    line = "2026-09-13T09:00:00.000Z ERROR [payment-process-flow] correlationId=req-200"
    event = parse_line(line, 1)
    assert event.correlation_id == "req-200"
    assert event.level == "ERROR"
    assert event.message == ""


def test_parse_plaintext_missing_correlation_id_raises():
    line = "2026-09-13T09:00:00.000Z INFO [orders-process-flow] - no correlation id here"
    with pytest.raises(ValueError):
        parse_line(line, 1)


def test_parse_plaintext_garbage_raises():
    with pytest.raises(ValueError):
        parse_line("this is not a log line at all", 1)


def test_parse_log_file_handles_malformed_lines_gracefully(tmp_path):
    content = "\n".join(
        [
            json.dumps(
                {
                    "timestamp": "2026-09-13T09:00:00.000Z",
                    "correlationId": "abc-1",
                    "flowName": "f1",
                    "level": "INFO",
                    "message": "ok line",
                }
            ),
            "{this is broken json",
            "2026-09-13T09:00:01.000Z INFO [f2] correlationId=abc-2 - a plaintext line",
            "totally unparseable garbage line",
            "",  # blank line (a real one: an extra newline appears after this join)
            "",
        ]
    )
    log_file = tmp_path / "mixed.log"
    log_file.write_text(content)

    events, stats = parse_log_file(str(log_file))

    assert isinstance(stats, ParseStats)
    assert len(events) == 2
    assert stats.parsed_json == 1
    assert stats.parsed_plaintext == 1
    # Both the broken-JSON line and the garbage line (no correlationId=,
    # doesn't start with "{") are skipped -- neither crashes the run.
    assert stats.skipped == 2
    assert stats.blank_lines == 1
    assert stats.total_lines == 5
    assert len(stats.skipped_reasons) == 2
