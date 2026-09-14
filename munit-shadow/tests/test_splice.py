import difflib
from pathlib import Path

import pytest

from munit_shadow import splice
from munit_shadow.xml_parser import FlowInfo, OutboundCall

EXAMPLE_TEST_FILE = (
    Path(__file__).resolve().parent.parent
    / "examples"
    / "order_management_app"
    / "src"
    / "test"
    / "munit"
    / "orders-flow-test.xml"
)


def _flow(calls):
    flow = FlowInfo(name="orders-process-flow", kind="flow", is_entry_point=True, entry_point_type="http:listener")
    flow.outbound_calls = calls
    return flow


HTTP_CALL = OutboundCall(flow_name="orders-process-flow", operation="http:request", config_ref="inventoryServiceConfig", doc_name=None)
DB_CALL = OutboundCall(flow_name="orders-process-flow", operation="db:select", config_ref="ordersDbConfig", doc_name=None)


def assert_pure_insertion(original: bytes, new: bytes):
    """The single most important guarantee in this whole project: the new
    content must be reachable from the original by INSERTIONS only -- never
    a 'replace' or 'delete' opcode -- proving every original byte survives
    unchanged and in the same order. difflib.SequenceMatcher over lines
    gives us exactly that as a real diff check, not just well-formedness.
    """
    original_lines = original.decode("utf-8").splitlines(keepends=True)
    new_lines = new.decode("utf-8").splitlines(keepends=True)
    sm = difflib.SequenceMatcher(a=original_lines, b=new_lines, autojunk=False)
    bad_ops = [op for op in sm.get_opcodes() if op[0] not in ("equal", "insert")]
    assert bad_ops == [], f"non-insertion diff opcodes found (byte preservation violated): {bad_ops}"


def test_new_mock_added_and_existing_content_byte_preserved():
    original = EXAMPLE_TEST_FILE.read_bytes()

    outcome = splice.sync_existing_test_file(original, [_flow([HTTP_CALL, DB_CALL])])

    assert outcome.changed is True
    assert [c.operation for c in outcome.new_mocks] == ["db:select"]

    assert_pure_insertion(original, outcome.new_bytes)

    new_text = outcome.new_bytes.decode("utf-8")
    original_text = original.decode("utf-8")

    # Every single line of the hand-authored original -- the mixed
    # indentation, the human comment, and the genuine custom assertion --
    # must appear byte-identical in the new file.
    for line in original_text.splitlines():
        assert line in new_text, f"original line lost or altered: {line!r}"

    # And specifically the load-bearing hand-written bits:
    assert "<!-- Srinivas: mocking the inventory check so this test never hits the real staging endpoint -->" in new_text
    assert '<munit-tools:assert-that expression="#[payload.quantityAvailable]" is="#[MunitTools::equalTo(42)]"/>' in new_text
    assert '<munit:payload value="#[{ sku: \'SKU-1\' }]" mediaType="application/json"/>' in new_text

    # The new mock is really there, inside the behavior section.
    assert 'processor="db:select"' in new_text
    assert "ordersDbConfig" in new_text.split('processor="db:select"')[1].split("</munit-tools:mock-when>")[0]


def test_sync_is_idempotent_when_nothing_changed():
    original = EXAMPLE_TEST_FILE.read_bytes()
    first = splice.sync_existing_test_file(original, [_flow([HTTP_CALL, DB_CALL])])
    second = splice.sync_existing_test_file(first.new_bytes, [_flow([HTTP_CALL, DB_CALL])])

    assert second.changed is False
    assert second.new_mocks == []
    assert second.new_bytes == first.new_bytes


def test_stale_call_is_flagged_not_deleted():
    original = EXAMPLE_TEST_FILE.read_bytes()
    # First, sync in the db:select mock so we have two mocks present.
    with_two_mocks = splice.sync_existing_test_file(original, [_flow([HTTP_CALL, DB_CALL])]).new_bytes

    # Now the flow "shrinks" back down to just the http:request call.
    outcome = splice.sync_existing_test_file(with_two_mocks, [_flow([HTTP_CALL])])

    assert outcome.changed is True
    assert [m.operation for m in outcome.newly_flagged_stale] == ["db:select"]

    assert_pure_insertion(with_two_mocks, outcome.new_bytes)
    new_text = outcome.new_bytes.decode("utf-8")

    # The mock-when itself must still be there, never deleted.
    assert 'processor="db:select"' in new_text
    # ... immediately preceded by the stale marker comment.
    assert "munit-shadow: this outbound call was not found in the flow" in new_text
    marker_idx = new_text.index("munit-shadow: this outbound call was not found in the flow")
    mock_idx = new_text.index('processor="db:select"')
    assert marker_idx < mock_idx

    # The original http:request mock and all hand-written content survive.
    assert 'processor="http:request"' in new_text
    assert '<munit-tools:assert-that expression="#[payload.quantityAvailable]" is="#[MunitTools::equalTo(42)]"/>' in new_text


def test_stale_marker_not_duplicated_on_repeated_sync():
    original = EXAMPLE_TEST_FILE.read_bytes()
    with_two_mocks = splice.sync_existing_test_file(original, [_flow([HTTP_CALL, DB_CALL])]).new_bytes
    once_stale = splice.sync_existing_test_file(with_two_mocks, [_flow([HTTP_CALL])]).new_bytes

    outcome = splice.sync_existing_test_file(once_stale, [_flow([HTTP_CALL])])

    assert outcome.changed is False
    assert outcome.newly_flagged_stale == []
    assert [m.operation for m in outcome.still_flagged_stale] == ["db:select"]
    marker_text = "munit-shadow: this outbound call was not found in the flow"
    assert outcome.new_bytes.decode("utf-8").count(marker_text) == 1


NO_BEHAVIOR_TEST_FILE = b"""<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:munit="http://www.mulesoft.org/schema/mule/munit"
      xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools">
    <munit:test name="test-billing-flow" description="hand-written, no mocks wired up yet">
        <munit:execution>
            <flow-ref name="billing-flow"/>
        </munit:execution>
        <munit:validation>
            <munit-tools:assert-that expression="#[payload]" is="#[MunitTools::notNullValue()]"/>
        </munit:validation>
    </munit:test>
</mule>
"""


def test_creates_behavior_section_when_missing():
    flow = FlowInfo(name="billing-flow", kind="flow")
    flow.outbound_calls = [
        OutboundCall(flow_name="billing-flow", operation="db:select", config_ref="ordersDbConfig", doc_name=None)
    ]
    outcome = splice.sync_existing_test_file(NO_BEHAVIOR_TEST_FILE, [flow])

    assert outcome.changed is True
    assert_pure_insertion(NO_BEHAVIOR_TEST_FILE, outcome.new_bytes)
    new_text = outcome.new_bytes.decode("utf-8")
    assert "<munit:behavior>" in new_text
    assert 'processor="db:select"' in new_text
    # original hand-written content untouched
    assert '<flow-ref name="billing-flow"/>' in new_text
    assert '<munit-tools:assert-that expression="#[payload]" is="#[MunitTools::notNullValue()]"/>' in new_text


MULTI_FLOW_ONE_MISSING_TEST_FILE = b"""<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:munit="http://www.mulesoft.org/schema/mule/munit"
      xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools">
    <munit:config name="suite"/>
    <munit:test name="test-flow-a">
        <munit:execution>
            <flow-ref name="flow-a"/>
        </munit:execution>
        <munit:validation>
            <munit-tools:assert-that expression="#[payload]" is="#[MunitTools::notNullValue()]"/>
        </munit:validation>
    </munit:test>
</mule>
"""


def test_appends_whole_new_test_for_a_flow_with_no_existing_test():
    flow_a = FlowInfo(name="flow-a", kind="flow")
    flow_b = FlowInfo(name="flow-b", kind="flow")
    flow_b.outbound_calls = [
        OutboundCall(flow_name="flow-b", operation="http:request", config_ref="svcConfig", doc_name=None)
    ]

    outcome = splice.sync_existing_test_file(MULTI_FLOW_ONE_MISSING_TEST_FILE, [flow_a, flow_b])

    assert outcome.changed is True
    assert outcome.new_tests_added == ["flow-b"]
    assert_pure_insertion(MULTI_FLOW_ONE_MISSING_TEST_FILE, outcome.new_bytes)

    new_text = outcome.new_bytes.decode("utf-8")
    assert 'name="test-flow-b"' in new_text
    assert 'processor="http:request"' in new_text
    # flow-a's existing test is completely untouched
    assert '<flow-ref name="flow-a"/>' in new_text


def test_not_well_formed_existing_file_raises_splice_error_and_never_written():
    with pytest.raises(splice.SpliceError):
        splice.sync_existing_test_file(b"<mule><munit:test></mule>", [_flow([HTTP_CALL])])
