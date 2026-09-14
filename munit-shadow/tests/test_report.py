from pathlib import Path

from munit_shadow.report import FlowSyncResult, format_status_line, format_summary


def _result(**kwargs):
    defaults = dict(
        flow_file=Path("src/main/mule/orders-flow.xml"),
        test_file=Path("src/test/munit/orders-flow-test.xml"),
        test_file_existed=True,
    )
    defaults.update(kwargs)
    return FlowSyncResult(**defaults)


def test_no_change_prints_nothing_by_default():
    r = _result(changed=False)
    assert format_status_line(r) is None


def test_no_change_prints_in_verbose_mode():
    r = _result(changed=False)
    line = format_status_line(r, verbose=True)
    assert line is not None
    assert "no changes" in line


def test_new_mock_and_stale_mock_line_shape():
    r = _result(changed=True, new_mock_operations=["db:select"], newly_flagged_stale_operations=["http:request"])
    line = format_status_line(r)
    assert line.startswith("orders-flow.xml changed -> updated src/test/munit/orders-flow-test.xml:")
    assert "+1 new mock (db:select)" in line
    assert "1 stale mock flagged (http:request no longer in flow)" in line


def test_new_file_created_line():
    r = _result(test_file_existed=False, new_file_created=True, changed=True)
    line = format_status_line(r)
    assert "created" in line
    assert "no test file existed yet" in line


def test_error_line():
    r = _result(error="not well-formed")
    line = format_status_line(r)
    assert "error" in line
    assert "not well-formed" in line


def test_summary_counts_changed_and_errors():
    results = [
        _result(changed=True),
        _result(changed=False),
        _result(error="boom"),
    ]
    summary = format_summary(results)
    assert "3 flow file(s)" in summary
    assert "1 test file(s) updated" in summary
    assert "1 flow(s) failed" in summary
