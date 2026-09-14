"""Offline tests for report.py's summary formatting."""

from __future__ import annotations

from pathlib import Path

from mule_munit_scaffold.report import build_summary
from mule_munit_scaffold.xml_parser import parse_app

CORE_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(CORE_HEADER + body + "\n</mule>\n", encoding="utf-8")
    return p


def test_summary_mentions_flows_and_calls(tmp_path: Path):
    xml = _write(
        tmp_path,
        "s.xml",
        """
        <flow name="a-flow">
            <http:request config-ref="cfg" path="/x" doc:name="Call"/>
        </flow>
        <sub-flow name="a-subflow">
            <http:request config-ref="cfg2" path="/y" doc:name="Inner call"/>
        </sub-flow>
        """,
    )
    parsed = parse_app(xml)

    summary_default = build_summary(parsed, include_subflows=False)
    assert "Flows processed: 1" in summary_default
    assert "a-flow" in summary_default
    assert "a-subflow" in summary_default  # mentioned in the skipped list
    assert "Sub-flows skipped" in summary_default

    summary_included = build_summary(parsed, include_subflows=True)
    assert "Flows processed: 2" in summary_included
    assert "Sub-flows skipped" not in summary_included
