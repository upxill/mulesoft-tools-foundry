import xml.etree.ElementTree as ET

from munit_shadow import scaffold
from munit_shadow.xml_parser import FlowInfo, OutboundCall

MUNIT_TOOLS_NS = "http://www.mulesoft.org/schema/mule/munit-tools"
MUNIT_NS = "http://www.mulesoft.org/schema/mule/munit"


def _q(ns, local):
    return f"{{{ns}}}{local}"


def test_new_test_suite_is_well_formed_and_has_one_mock_per_distinct_call():
    flow = FlowInfo(name="orders-process-flow", kind="flow", is_entry_point=True, entry_point_type="http:listener")
    flow.outbound_calls = [
        OutboundCall(flow_name=flow.name, operation="http:request", config_ref="inventoryServiceConfig", doc_name=None),
        OutboundCall(flow_name=flow.name, operation="db:select", config_ref="ordersDbConfig", doc_name=None),
        OutboundCall(flow_name=flow.name, operation="http:request", config_ref="inventoryServiceConfig", doc_name=None),  # dupe
    ]

    text = scaffold.render_new_test_suite_text([flow])
    root = ET.fromstring(text)  # must not raise

    tests = root.findall(_q(MUNIT_NS, "test"))
    assert len(tests) == 1
    mocks = tests[0].findall(f"{_q(MUNIT_NS, 'behavior')}/{_q(MUNIT_TOOLS_NS, 'mock-when')}")
    assert len(mocks) == 2  # deduped


def test_special_characters_in_config_ref_and_doc_name_stay_well_formed():
    flow = FlowInfo(name="weird-flow", kind="flow")
    flow.outbound_calls = [
        OutboundCall(flow_name=flow.name, operation="http:request", config_ref="a&b<c>d\"e", doc_name=None),
    ]
    text = scaffold.render_new_test_suite_text([flow])
    ET.fromstring(text)  # must not raise


def test_zero_call_flow_produces_no_behavior_section():
    flow = FlowInfo(name="noop-flow", kind="flow")
    text = scaffold.render_new_test_suite_text([flow])
    root = ET.fromstring(text)
    test_elem = root.find(_q(MUNIT_NS, "test"))
    assert test_elem.find(_q(MUNIT_NS, "behavior")) is None


def test_sanitize_comment_text_collapses_double_hyphens():
    assert "--" not in scaffold.sanitize_comment_text("a -- b --- c")
    assert not scaffold.sanitize_comment_text("trailing--").endswith("-")


def test_render_mock_when_lines_produce_well_formed_fragment_when_wrapped():
    call = OutboundCall(flow_name="f", operation="db:insert", config_ref="ordersDbConfig", doc_name=None)
    lines = scaffold.render_mock_when_lines(call)
    wrapped = (
        '<root xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools" '
        'xmlns:doc="http://www.mulesoft.org/schema/mule/documentation">'
        + "\n".join(lines)
        + "</root>"
    )
    ET.fromstring(wrapped)  # must not raise
