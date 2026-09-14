"""Offline tests for report.md/report.json rendering from synthetic Finding
objects. No LLM calls."""

import json

from mule_flow_doctor.report import render_json, render_markdown, write_report
from mule_flow_doctor.reviewer import Finding, ReviewReport


def _synthetic_report() -> ReviewReport:
    return ReviewReport(
        findings=[
            Finding(
                severity="high",
                category="missing-error-handler",
                file="orders-flow.xml",
                flow_name="orders-process-flow",
                explanation="No error-handler around external calls.",
                recommendation="Add an error-handler.",
            ),
            Finding(
                severity="medium",
                category="inefficient-dataweave",
                file="billing-subflow.xml",
                flow_name="billing-calculate-subflow",
                explanation="payload.orders scanned twice.",
                recommendation="Use groupBy once.",
            ),
            Finding(
                severity="info",
                category="style",
                file="simple-flow.xml",
                flow_name=None,
                explanation="Minor naming nitpick.",
                recommendation="Consider renaming.",
            ),
        ],
        summary="2 real issues and 1 nitpick found.",
    )


def test_render_markdown_groups_by_severity_high_first():
    report = _synthetic_report()
    md = render_markdown(report)

    high_idx = md.index("## High severity")
    medium_idx = md.index("## Medium severity")
    info_idx = md.index("## Info severity")
    assert high_idx < medium_idx < info_idx

    assert "missing-error-handler" in md
    assert "orders-process-flow" in md
    assert "inefficient-dataweave" in md
    assert "2 real issues and 1 nitpick found." in md
    assert "**3 finding(s)**" in md


def test_render_markdown_empty_findings():
    report = ReviewReport(findings=[], summary="Clean app, nothing to report.")
    md = render_markdown(report)
    assert "No findings." in md
    assert "**0 finding(s)**" in md


def test_render_json_round_trips():
    report = _synthetic_report()
    data = json.loads(render_json(report))
    assert len(data["findings"]) == 3
    assert data["findings"][0]["severity"] == "high"
    assert data["summary"] == "2 real issues and 1 nitpick found."


def test_write_report_creates_both_files(tmp_path):
    report = _synthetic_report()
    md_path, json_path = write_report(report, tmp_path / "out")

    assert md_path.exists()
    assert json_path.exists()
    assert md_path.name == "report.md"
    assert json_path.name == "report.json"

    data = json.loads(json_path.read_text())
    assert len(data["findings"]) == 3
