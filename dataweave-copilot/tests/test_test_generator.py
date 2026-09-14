"""Offline tests for test_generator.py's XML well-formedness and coverage
logic. No API/network calls -- these test synthetic MUnit XML strings."""

from dataweave_copilot import test_generator as testgen

VALID_MUNIT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
      xmlns:munit="http://www.mulesoft.org/schema/mule/munit"
      xmlns:munit-tools="http://www.mulesoft.org/schema/mule/munit-tools"
      xmlns="http://www.mulesoft.org/schema/mule/core">
  <munit:config name="orders_to_invoice-suite" />
  <munit:test name="null_customer_name_test" description="handles a null customer name">
    <munit:execution>
      <flow-ref name="transformFlow"/>
    </munit:execution>
    <munit:validation>
      <munit-tools:assert-that expression="#[payload.customerName]" is="#[MunitTools::nullValue()]" />
    </munit:validation>
  </munit:test>
  <munit:test name="empty_line_items_test" description="handles an empty line items array">
    <munit:execution>
      <flow-ref name="transformFlow"/>
    </munit:execution>
    <munit:validation>
      <munit-tools:assert-that expression="#[payload.subtotal]" is="#[MunitTools::equalTo(0)]" />
    </munit:validation>
  </munit:test>
  <munit:test name="type_mismatch_quantity_test" description="quantity provided as a string instead of a number">
    <munit:execution>
      <flow-ref name="transformFlow"/>
    </munit:execution>
    <munit:validation>
      <munit-tools:assert-that expression="#[payload]" is="#[MunitTools::notNullValue()]" />
    </munit:validation>
  </munit:test>
  <munit:test name="max_boundary_quantity_test" description="a boundary maximum quantity value">
    <munit:execution>
      <flow-ref name="transformFlow"/>
    </munit:execution>
    <munit:validation>
      <munit-tools:assert-that expression="#[payload.total]" is="#[MunitTools::notNullValue()]" />
    </munit:validation>
  </munit:test>
</mule>
"""

# Deliberately broken: unclosed <munit:test> tag.
INVALID_MUNIT_XML = """\
<?xml version="1.0" encoding="UTF-8"?>
<mule xmlns:munit="http://www.mulesoft.org/schema/mule/munit">
  <munit:test name="broken_test">
    <munit:execution>
      <flow-ref name="transformFlow"/>
    </munit:execution>
</mule>
"""


def test_valid_xml_is_well_formed():
    ok, err = testgen.check_well_formed(VALID_MUNIT_XML)
    assert ok is True
    assert err is None


def test_invalid_xml_is_not_well_formed():
    ok, err = testgen.check_well_formed(INVALID_MUNIT_XML)
    assert ok is False
    assert err is not None


def test_extract_xml_strips_fence():
    raw = f"```xml\n{VALID_MUNIT_XML}```"
    extracted = testgen.extract_xml(raw)
    ok, _ = testgen.check_well_formed(extracted)
    assert ok is True


def test_extract_xml_passes_through_unfenced():
    assert testgen.extract_xml(VALID_MUNIT_XML) == VALID_MUNIT_XML.strip()


def test_analyze_coverage_counts_tests():
    count, covered = testgen.analyze_coverage(VALID_MUNIT_XML)
    assert count == 4


def test_analyze_coverage_detects_all_four_categories():
    _, covered = testgen.analyze_coverage(VALID_MUNIT_XML)
    assert set(covered) == set(testgen.EDGE_CASE_CATEGORIES)


def test_analyze_coverage_on_invalid_xml_returns_zero():
    count, covered = testgen.analyze_coverage(INVALID_MUNIT_XML)
    assert count == 0
    assert covered == []


def test_analyze_coverage_missing_category():
    xml = """\
<mule xmlns:munit="http://www.mulesoft.org/schema/mule/munit">
  <munit:test name="basic_happy_path" description="normal confirmed order" />
</mule>
"""
    count, covered = testgen.analyze_coverage(xml)
    assert count == 1
    assert covered == []
