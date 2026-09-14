from munit_shadow.mock_diff import STALE_MARKER_TEXT, diff_calls, find_existing_mocks
from munit_shadow.xml_parser import OutboundCall

TEST_FILE_XML = f"""<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns="http://www.mulesoft.org/schema/mule/core"
      xmlns:munit="http://www.mulesoft.org/schema/mule/munit"
      xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools">
    <munit:test name="test-orders-process-flow">
        <munit:behavior>
            <munit-tools:mock-when processor="http:request">
                <munit-tools:with-attributes>
                    <munit-tools:with-attribute attributeName="config-ref" whereValue="#['inventoryServiceConfig']"/>
                </munit-tools:with-attributes>
            </munit-tools:mock-when>
            <!-- {STALE_MARKER_TEXT} -->
            <munit-tools:mock-when processor="db:select">
                <munit-tools:with-attributes>
                    <munit-tools:with-attribute attributeName="config-ref" whereValue="#['ordersDbConfig']"/>
                </munit-tools:with-attributes>
            </munit-tools:mock-when>
        </munit:behavior>
    </munit:test>
</mule>
"""


def test_find_existing_mocks_extracts_operation_and_config_ref():
    mocks = find_existing_mocks(TEST_FILE_XML)
    assert len(mocks) == 2
    assert mocks[0].operation == "http:request"
    assert mocks[0].config_ref == "inventoryServiceConfig"
    assert mocks[0].already_flagged_stale is False
    assert mocks[1].operation == "db:select"
    assert mocks[1].config_ref == "ordersDbConfig"
    assert mocks[1].already_flagged_stale is True


def test_diff_new_stale_and_unchanged():
    mocks = find_existing_mocks(TEST_FILE_XML)
    current_calls = [
        OutboundCall(flow_name="orders-process-flow", operation="http:request", config_ref="inventoryServiceConfig", doc_name=None),
        OutboundCall(flow_name="orders-process-flow", operation="db:insert", config_ref="ordersDbConfig", doc_name=None),
    ]
    result = diff_calls(current_calls, mocks)

    assert [c.operation for c in result.new_calls] == ["db:insert"]
    assert [c.operation for c in result.unchanged] == ["http:request"]
    # db:select mock is already flagged stale and still absent from the flow -> stays in already_flagged_stale
    assert [m.operation for m in result.already_flagged_stale] == ["db:select"]
    assert result.stale_mocks == []


def test_diff_flags_a_newly_stale_mock_not_previously_flagged():
    mocks = find_existing_mocks(TEST_FILE_XML)
    # Now http:request is ALSO removed from the flow -> should become newly stale
    current_calls = []
    result = diff_calls(current_calls, mocks)
    stale_ops = {m.operation for m in result.stale_mocks}
    assert stale_ops == {"http:request"}
    already_flagged_ops = {m.operation for m in result.already_flagged_stale}
    assert already_flagged_ops == {"db:select"}
