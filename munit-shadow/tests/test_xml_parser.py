from pathlib import Path

import pytest

from munit_shadow.xml_parser import dedupe_calls, get_flow_raw_xml, parse_flow_file

FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:db="http://www.mulesoft.org/schema/mule/db"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core"
      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">

    <flow name="orders-process-flow">
        <http:listener config-ref="httpListenerConfig" path="/orders" doc:name="Orders Listener"/>
        <ee:transform doc:name="Normalize">
            <ee:message><ee:set-payload><![CDATA[%dw 2.0 --- payload]]></ee:set-payload></ee:message>
        </ee:transform>
        <choice doc:name="Choice">
            <when expression="#[true]">
                <http:request method="GET" config-ref="inventoryServiceConfig" path="/inventory" doc:name="Check Inventory"/>
            </when>
            <otherwise>
                <db:select config-ref="ordersDbConfig" doc:name="Lookup Customer">
                    <db:sql>SELECT * FROM customers</db:sql>
                </db:select>
            </otherwise>
        </choice>
        <flow-ref name="order-enrichment-subflow" doc:name="Enrich"/>
        <logger level="INFO" message="done" doc:name="Log"/>
    </flow>

    <sub-flow name="order-enrichment-subflow">
        <db:insert config-ref="ordersDbConfig" doc:name="Insert Order">
            <db:sql>INSERT INTO orders VALUES (:id)</db:sql>
        </db:insert>
    </sub-flow>

</mule>
"""

ZERO_CALL_FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:ee="http://www.mulesoft.org/schema/mule/ee/core">
    <flow name="noop-flow">
        <logger level="INFO" message="hi"/>
        <ee:transform><ee:message><ee:set-payload><![CDATA[payload]]></ee:set-payload></ee:message></ee:transform>
    </flow>
</mule>
"""

DEDUPE_FLOW_XML = """<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:http="http://www.mulesoft.org/schema/mule/http"
      xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">
    <flow name="dupe-flow">
        <http:request config-ref="svcA" doc:name="Call A"/>
        <http:request config-ref="svcA" doc:name="Call A again"/>
        <http:request config-ref="svcB" doc:name="Call B"/>
    </flow>
</mule>
"""


def _write(tmp_path: Path, name: str, content: str) -> Path:
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def test_detects_flow_and_subflow_and_entry_point(tmp_path):
    f = _write(tmp_path, "orders-flow.xml", FLOW_XML)
    parsed = parse_flow_file(f)

    assert set(parsed.flows.keys()) == {"orders-process-flow", "order-enrichment-subflow"}
    main = parsed.flows["orders-process-flow"]
    assert main.kind == "flow"
    assert main.is_entry_point is True
    assert main.entry_point_type == "http:listener"

    sub = parsed.flows["order-enrichment-subflow"]
    assert sub.kind == "sub-flow"
    assert sub.is_entry_point is False


def test_core_and_ee_elements_excluded(tmp_path):
    f = _write(tmp_path, "orders-flow.xml", FLOW_XML)
    parsed = parse_flow_file(f)
    ops = {c.operation for c in parsed.flows["orders-process-flow"].outbound_calls}
    # ee:transform, choice/when/otherwise, flow-ref, logger must never appear
    assert "ee:transform" not in ops
    assert "choice" not in ops
    assert "flow-ref" not in ops
    assert "logger" not in ops
    assert "http:listener" not in ops  # source, not an outbound call


def test_finds_real_outbound_calls_including_nested_in_choice(tmp_path):
    f = _write(tmp_path, "orders-flow.xml", FLOW_XML)
    parsed = parse_flow_file(f)
    calls = parsed.flows["orders-process-flow"].outbound_calls
    ops = {(c.operation, c.config_ref) for c in calls}
    assert ("http:request", "inventoryServiceConfig") in ops
    assert ("db:select", "ordersDbConfig") in ops

    sub_calls = parsed.flows["order-enrichment-subflow"].outbound_calls
    assert [(c.operation, c.config_ref) for c in sub_calls] == [("db:insert", "ordersDbConfig")]


def test_zero_outbound_calls(tmp_path):
    f = _write(tmp_path, "noop-flow.xml", ZERO_CALL_FLOW_XML)
    parsed = parse_flow_file(f)
    assert parsed.flows["noop-flow"].outbound_calls == []


def test_dedupe_by_operation_and_config_ref(tmp_path):
    f = _write(tmp_path, "dupe-flow.xml", DEDUPE_FLOW_XML)
    parsed = parse_flow_file(f)
    calls = parsed.flows["dupe-flow"].outbound_calls
    assert len(calls) == 3
    distinct = dedupe_calls(calls)
    assert len(distinct) == 2
    assert [(c.operation, c.config_ref) for c in distinct] == [
        ("http:request", "svcA"),
        ("http:request", "svcB"),
    ]


def test_get_flow_raw_xml_returns_just_that_flow(tmp_path):
    f = _write(tmp_path, "orders-flow.xml", FLOW_XML)
    raw = get_flow_raw_xml(f, "order-enrichment-subflow")
    # ElementTree re-serializes with its own generated namespace prefixes
    # (it doesn't preserve the source file's literal "db:" spelling), so
    # check by local element name + the real config-ref, not literal "db:".
    assert ":insert" in raw
    assert 'config-ref="ordersDbConfig"' in raw
    assert "orders-process-flow" not in raw  # only the requested flow's body


def test_malformed_xml_raises_parse_error(tmp_path):
    import xml.etree.ElementTree as ET

    f = _write(tmp_path, "broken.xml", "<mule><flow name=\"x\"></mule>")
    with pytest.raises(ET.ParseError):
        parse_flow_file(f)
