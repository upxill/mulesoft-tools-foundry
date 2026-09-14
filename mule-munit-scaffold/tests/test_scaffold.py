"""Offline tests for scaffold.py: XML well-formedness of generated output,
and the completeness check that is this tool's actual value proposition
(exactly one mock-when per distinct outbound call detected in the source).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

from mule_munit_scaffold.scaffold import (
    MUNIT_NS,
    MUNIT_TOOLS_NS,
    build_test_suite,
    flows_in_scope,
)
from mule_munit_scaffold.xml_parser import dedupe_calls, parse_app

CORE_HEADER = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core"
      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">
"""


def _write(tmp_path: Path, name: str, body: str) -> Path:
    p = tmp_path / name
    p.write_text(CORE_HEADER + body + "\n</mule>\n", encoding="utf-8")
    return p


def _mock_whens_by_test(root: ET.Element):
    """Returns {test_name: [mock-when elements]} for every munit:test."""
    result = {}
    for test in root.findall(f"{{{MUNIT_NS}}}test"):
        behavior = test.find(f"{{{MUNIT_NS}}}behavior")
        mocks = behavior.findall(f"{{{MUNIT_TOOLS_NS}}}mock-when") if behavior is not None else []
        result[test.get("name")] = mocks
    return result


def _serialize_and_reparse(tree) -> ET.Element:
    xml_bytes = ET.tostring(tree.getroot(), encoding="UTF-8", xml_declaration=True)
    return ET.fromstring(xml_bytes)


def test_generated_output_is_well_formed_for_a_realistic_flow(tmp_path: Path):
    xml = _write(
        tmp_path,
        "realistic.xml",
        """
        <flow name="orders-flow">
            <http:listener config-ref="l" path="/o" doc:name="Listener"/>
            <ee:transform doc:name="Transform">
                <ee:message>
                    <ee:set-payload><![CDATA[%dw 2.0
output application/json
---
payload
]]></ee:set-payload>
                </ee:message>
            </ee:transform>
            <http:request config-ref="inv" path="/check" doc:name="Check inventory"/>
            <db:select config-ref="db" doc:name="Look up customer">
                <db:sql><![CDATA[SELECT 1]]></db:sql>
            </db:select>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)

    root = _serialize_and_reparse(tree)
    assert root.tag == "{http://www.mulesoft.org/schema/mule/core}mule"


def test_generated_output_well_formed_with_zero_outbound_calls(tmp_path: Path):
    xml = _write(
        tmp_path,
        "empty_calls.xml",
        """
        <flow name="no-calls-flow">
            <logger level="INFO" doc:name="Log" message="#['hi']"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    root = _serialize_and_reparse(tree)
    tests = root.findall(f"{{{MUNIT_NS}}}test")
    assert len(tests) == 1
    # No <munit:behavior> at all when there's nothing to mock.
    assert tests[0].find(f"{{{MUNIT_NS}}}behavior") is None


def test_generated_output_well_formed_with_special_characters_in_names(tmp_path: Path):
    """Edge case: doc:name / config-ref values containing XML-special
    characters (&, <, >, quotes) must still round-trip through ET
    serialization without corrupting the document."""
    xml = _write(
        tmp_path,
        "special_chars.xml",
        """
        <flow name="weird-flow">
            <http:request config-ref="A&amp;B&lt;Config&gt;" path="/x" doc:name="Call &quot;the&quot; API &amp; retry"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    root = _serialize_and_reparse(tree)
    mock = root.find(f".//{{{MUNIT_TOOLS_NS}}}mock-when")
    assert mock is not None


def test_completeness_one_mock_when_per_distinct_outbound_call(tmp_path: Path):
    """The core value-proposition check: for every flow, the number of
    <munit-tools:mock-when> blocks in its generated test must equal the
    number of DISTINCT outbound calls (by dedupe key) found in that flow's
    real source XML -- not the raw count, and not off by one either way.
    """
    xml = _write(
        tmp_path,
        "completeness.xml",
        """
        <flow name="flow-a">
            <http:request config-ref="httpConfig" path="/a" doc:name="Call A"/>
            <db:select config-ref="dbConfig" doc:name="Select A">
                <db:sql><![CDATA[SELECT 1]]></db:sql>
            </db:select>
            <db:insert config-ref="dbConfig" doc:name="Insert A">
                <db:sql><![CDATA[INSERT INTO t VALUES (1)]]></db:sql>
            </db:insert>
        </flow>
        <flow name="flow-b">
            <http:request config-ref="sameConfig" path="/b1" doc:name="Call B1"/>
            <http:request config-ref="sameConfig" path="/b2" doc:name="Call B2"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    root = _serialize_and_reparse(tree)
    mocks_by_test = _mock_whens_by_test(root)

    for flow in parsed.flows.values():
        expected = len(dedupe_calls(flow.outbound_calls))
        actual = len(mocks_by_test[f"test-{flow.name}"])
        assert actual == expected, (
            f"{flow.name}: expected {expected} distinct outbound call(s) to "
            f"produce {expected} mock-when block(s), got {actual}"
        )

    # flow-a: 3 raw calls, all distinguishable (http+db-select+db-insert
    # all different operations) -> 3 distinct -> 3 mock-when.
    assert len(mocks_by_test["test-flow-a"]) == 3
    # flow-b: 2 raw calls, SAME operation + SAME config-ref -> MUnit cannot
    # tell them apart -> 1 distinct -> 1 mock-when.
    assert len(mocks_by_test["test-flow-b"]) == 1


def test_completeness_across_bundled_example_app():
    """Same completeness check, run against the real bundled example app
    under examples/order_management_app, not just synthetic fixtures."""
    examples_dir = Path(__file__).resolve().parent.parent / "examples" / "order_management_app"
    parsed = parse_app(examples_dir)
    tree = build_test_suite(parsed, include_subflows=True)
    root = _serialize_and_reparse(tree)
    mocks_by_test = _mock_whens_by_test(root)

    flows = flows_in_scope(parsed, include_subflows=True)
    assert len(flows) == 3  # orders-process-flow, order-enrichment-subflow, billing-scheduled-flow

    for flow in flows:
        expected = len(dedupe_calls(flow.outbound_calls))
        actual = len(mocks_by_test[f"test-{flow.name}"])
        assert actual == expected


def test_include_subflows_flag(tmp_path: Path):
    xml = _write(
        tmp_path,
        "subflow.xml",
        """
        <flow name="parent-flow">
            <flow-ref name="child-subflow" doc:name="Call child"/>
        </flow>
        <sub-flow name="child-subflow">
            <http:request config-ref="c" path="/x" doc:name="Inner call"/>
        </sub-flow>
        """,
    )
    parsed = parse_app(xml)

    tree_default = build_test_suite(parsed, include_subflows=False)
    root_default = _serialize_and_reparse(tree_default)
    names_default = {t.get("name") for t in root_default.findall(f"{{{MUNIT_NS}}}test")}
    assert names_default == {"test-parent-flow"}

    tree_included = build_test_suite(parsed, include_subflows=True)
    root_included = _serialize_and_reparse(tree_included)
    names_included = {t.get("name") for t in root_included.findall(f"{{{MUNIT_NS}}}test")}
    assert names_included == {"test-parent-flow", "test-child-subflow"}


def test_flow_ref_and_assertion_placeholder_present(tmp_path: Path):
    xml = _write(
        tmp_path,
        "assertion.xml",
        """
        <flow name="asserted-flow">
            <http:request config-ref="c" path="/x" doc:name="Call"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    tree = build_test_suite(parsed)
    root = _serialize_and_reparse(tree)

    flow_ref = root.find(".//{http://www.mulesoft.org/schema/mule/core}flow-ref")
    assert flow_ref is not None
    assert flow_ref.get("name") == "asserted-flow"

    assert_that = root.find(f".//{{{MUNIT_TOOLS_NS}}}assert-that")
    assert assert_that is not None
    assert assert_that.get("expression") == "#[payload]"
    assert assert_that.get("is") == "#[MunitTools::notNullValue()]"
