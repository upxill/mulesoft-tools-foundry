"""Offline tests for xml_parser.py: outbound-call detection on synthetic
Mule 4 flow XML. No network, no API calls.
"""

from __future__ import annotations

from pathlib import Path

import pytest

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


def test_flow_with_zero_outbound_calls(tmp_path: Path):
    xml = _write(
        tmp_path,
        "zero.xml",
        """
        <flow name="pure-transform-flow">
            <http:listener config-ref="cfg" path="/x" doc:name="Listener"/>
            <ee:transform doc:name="Transform">
                <ee:message>
                    <ee:set-payload><![CDATA[%dw 2.0
output application/json
---
payload
]]></ee:set-payload>
                </ee:message>
            </ee:transform>
            <choice doc:name="Choice">
                <when expression="#[true]">
                    <logger level="INFO" doc:name="Log" message="#['hi']"/>
                </when>
                <otherwise>
                    <set-payload value="#[{}]" doc:name="Set"/>
                </otherwise>
            </choice>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    assert set(parsed.flows.keys()) == {"pure-transform-flow"}
    flow = parsed.flows["pure-transform-flow"]
    assert flow.outbound_calls == []
    assert flow.is_entry_point is True
    assert flow.entry_point_type == "http:listener"


def test_flow_with_one_outbound_call(tmp_path: Path):
    xml = _write(
        tmp_path,
        "one.xml",
        """
        <flow name="single-call-flow">
            <http:request config-ref="myHttpConfig" path="/x" method="GET" doc:name="Call it"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    flow = parsed.flows["single-call-flow"]
    assert len(flow.outbound_calls) == 1
    call = flow.outbound_calls[0]
    assert call.operation == "http:request"
    assert call.config_ref == "myHttpConfig"
    assert call.doc_name == "Call it"


def test_flow_with_several_different_outbound_calls(tmp_path: Path):
    xml = _write(
        tmp_path,
        "several.xml",
        """
        <flow name="multi-call-flow">
            <http:request config-ref="httpConfig" path="/a" doc:name="HTTP call"/>
            <db:select config-ref="dbConfig" doc:name="DB select">
                <db:sql><![CDATA[SELECT 1]]></db:sql>
            </db:select>
            <db:insert config-ref="dbConfig" doc:name="DB insert">
                <db:sql><![CDATA[INSERT INTO t VALUES (1)]]></db:sql>
            </db:insert>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    flow = parsed.flows["multi-call-flow"]
    ops = sorted(c.operation for c in flow.outbound_calls)
    assert ops == ["db:insert", "db:select", "http:request"]


def test_core_and_ee_elements_are_not_outbound_calls(tmp_path: Path):
    """<ee:transform>, <logger>, <choice>, <flow-ref> etc. must never be
    mistaken for outbound connector calls."""
    xml = _write(
        tmp_path,
        "core.xml",
        """
        <sub-flow name="helper-subflow">
            <ee:transform doc:name="Transform">
                <ee:message>
                    <ee:set-payload><![CDATA[%dw 2.0
output application/json
---
payload map (item) -> item
]]></ee:set-payload>
                </ee:message>
            </ee:transform>
        </sub-flow>
        <flow name="caller-flow">
            <flow-ref name="helper-subflow" doc:name="Call helper"/>
            <logger level="INFO" doc:name="Log" message="#['done']"/>
            <try doc:name="Try">
                <error-handler>
                    <on-error-continue doc:name="Continue"/>
                </error-handler>
            </try>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    assert parsed.flows["helper-subflow"].outbound_calls == []
    caller = parsed.flows["caller-flow"]
    assert caller.outbound_calls == []
    assert caller.flow_refs == ["helper-subflow"]


def test_flow_ref_tracked_but_not_an_outbound_call(tmp_path: Path):
    xml = _write(
        tmp_path,
        "ref.xml",
        """
        <flow name="parent-flow">
            <flow-ref name="child-flow" doc:name="Go to child"/>
        </flow>
        <sub-flow name="child-flow">
            <http:request config-ref="c" path="/x" doc:name="Inner call"/>
        </sub-flow>
        """,
    )
    parsed = parse_app(xml)
    assert parsed.flows["parent-flow"].outbound_calls == []
    assert parsed.flows["parent-flow"].flow_refs == ["child-flow"]
    assert len(parsed.flows["child-flow"].outbound_calls) == 1


def test_scheduler_entry_point_detected(tmp_path: Path):
    xml = _write(
        tmp_path,
        "sched.xml",
        """
        <flow name="scheduled-flow">
            <scheduler doc:name="Every hour">
                <scheduling-strategy>
                    <fixed-frequency frequency="3600000"/>
                </scheduling-strategy>
            </scheduler>
            <db:select config-ref="dbConfig" doc:name="Select">
                <db:sql><![CDATA[SELECT 1]]></db:sql>
            </db:select>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    flow = parsed.flows["scheduled-flow"]
    assert flow.is_entry_point is True
    assert flow.entry_point_type == "scheduler"
    assert len(flow.outbound_calls) == 1


def test_dedupe_calls_collapses_identical_key(tmp_path: Path):
    xml = _write(
        tmp_path,
        "dupe.xml",
        """
        <flow name="dupe-flow">
            <http:request config-ref="sameConfig" path="/a" doc:name="First call"/>
            <http:request config-ref="sameConfig" path="/b" doc:name="Second call"/>
            <http:request config-ref="otherConfig" path="/c" doc:name="Third call"/>
        </flow>
        """,
    )
    parsed = parse_app(xml)
    flow = parsed.flows["dupe-flow"]
    assert len(flow.outbound_calls) == 3
    distinct = dedupe_calls(flow.outbound_calls)
    # Two calls share config-ref "sameConfig" -> MUnit cannot tell them apart
    # by config-ref alone, so they collapse to one distinct call; the third
    # has a different config-ref and stays separate.
    assert len(distinct) == 2
    assert {c.config_ref for c in distinct} == {"sameConfig", "otherConfig"}


def test_no_config_ref_disambiguated_by_doc_name(tmp_path: Path):
    header = CORE_HEADER.replace(
        'xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">',
        'xmlns:vm="http://www.mulesoft.org/schema/mule/vm"\n'
        '      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">',
    )
    body = """
        <flow name="publish-flow">
            <vm:publish queueName="q1" doc:name="Publish to q1"/>
            <vm:publish queueName="q2" doc:name="Publish to q2"/>
        </flow>
        """
    p = tmp_path / "vm.xml"
    p.write_text(header + body + "\n</mule>\n", encoding="utf-8")

    parsed = parse_app(p)
    flow = parsed.flows["publish-flow"]
    assert len(flow.outbound_calls) == 2
    distinct = dedupe_calls(flow.outbound_calls)
    # No config-ref on either call, but different doc:name -> kept distinct.
    assert len(distinct) == 2


def test_no_xml_files_raises(tmp_path: Path):
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    with pytest.raises(ValueError):
        parse_app(empty_dir)


def test_malformed_xml_raises(tmp_path: Path):
    p = tmp_path / "broken.xml"
    p.write_text("<mule><flow name='x'></mule>", encoding="utf-8")
    with pytest.raises(ValueError):
        parse_app(p)
