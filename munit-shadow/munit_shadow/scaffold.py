"""Building blocks for MUnit XML text: both a brand-new test file (the
"no test yet" case) and the individual `<munit-tools:mock-when>` /
`<munit:test>` text fragments the incremental splice engine (splice.py)
inserts into an EXISTING file.

Two very different write paths share this module:

  1. ``render_new_test_suite_text()`` -- used only when a flow file has NO
     corresponding test file at all yet. This is the one place munit-shadow
     is allowed to write a whole file from scratch, and it does so with
     ``xml.etree.ElementTree`` + ``ET.indent`` (safe here -- there is no
     existing developer content to damage).
  2. ``render_mock_when_lines()`` / ``render_new_test_lines()`` -- return
     plain text LINES (no XML tree involved) that splice.py inserts,
     byte-for-byte, into an existing file it never re-serializes. This is
     the path used on every subsequent sync once a test file exists.

MUnit XML shapes used below match the same citations as xml_parser.py's
module docstring (MUnit Test Structure Fundamentals, Mock Event Processor /
MUnit Tools Module Reference).
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional

from .xml_parser import CORE_NS, DOC_NS, MUNIT_NS, MUNIT_TOOLS_NS, FlowInfo, OutboundCall, dedupe_calls

_SCHEMA_LOCATION = (
    "\n        ".join(
        [
            "",
            f"{CORE_NS} {CORE_NS}/current/mule.xsd",
            f"{MUNIT_NS} {MUNIT_NS}/current/mule-munit.xsd",
            f"{MUNIT_TOOLS_NS} {MUNIT_TOOLS_NS}/current/mule-munit-tools.xsd",
        ]
    )
    + "\n    "
)


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def sanitize_comment_text(text: str) -> str:
    """XML forbids "--" inside a comment (and a comment may not end in "-").
    Collapse any run of 2+ hyphens down to a single hyphen so every
    generated ``<!-- ... -->`` is guaranteed well-formed regardless of what
    free-text note is passed in.
    """
    text = re.sub(r"-{2,}", "-", text)
    return text.rstrip("-")


def placeholder_payload_expr(call: OutboundCall) -> str:
    """A deterministic, structurally-plausible placeholder response payload.
    ``--enrich`` (enrich.py) is what produces something smarter, grounded in
    the flow's real XML; this is the always-available fallback.
    """
    op_label = call.operation.replace(":", "_").replace("-", "_")
    return (
        "#[{ "
        f"mocked: true, "
        f"operation: '{call.operation}', "
        f"note: 'TODO({op_label}): replace with a realistic mock response payload' "
        "}]"
    )


# --------------------------------------------------------------------------
# Text-line rendering (used by splice.py for in-place, byte-preserving edits)
# --------------------------------------------------------------------------


def render_mock_when_lines(
    call: OutboundCall, then_return_value: Optional[str] = None, include_doc_name: bool = True
) -> List[str]:
    """Return plain text lines (no leading indentation, no trailing
    newlines, no surrounding whitespace) that render one
    ``<munit-tools:mock-when>`` block for ``call`` -- preceded by an
    advisory comment when there's no ``config-ref`` to disambiguate it.
    ``splice.py`` is responsible for indenting and joining these lines
    when inserting them into an existing file's exact byte stream.

    ``include_doc_name`` MUST be false when splicing into an existing file
    that doesn't already declare the ``doc`` XML namespace prefix -- a
    generated ``doc:name="..."`` attribute would otherwise reference an
    unbound prefix and break well-formedness. Callers doing a text splice
    into an existing file (splice.py) check this first; callers building a
    brand-new file from scratch (render_new_test_suite_text, which declares
    every namespace it uses at the root) don't need to.
    """
    lines: List[str] = []

    doc_bits = [f"Mock {call.operation}"]
    if call.config_ref:
        doc_bits.append(f"config-ref={call.config_ref}")
    elif call.doc_name:
        doc_bits.append(f"call={call.doc_name}")
    doc_name_value = " - ".join(doc_bits)

    if not call.config_ref:
        comment_text = sanitize_comment_text(
            f"NOTE: this {call.operation} call has no config-ref, so this mock "
            "cannot be scoped to a specific connector configuration; it will "
            "apply to ALL such calls in this flow. Consider adding a "
            "config-ref to disambiguate."
        )
        lines.append(f"<!-- {comment_text} -->")

    value = then_return_value if then_return_value else placeholder_payload_expr(call)
    value_escaped = (
        value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )
    doc_name_escaped = (
        doc_name_value.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
    )

    doc_attr = f' doc:name="{doc_name_escaped}"' if include_doc_name else ""
    lines.append(f'<munit-tools:mock-when processor="{call.operation}"{doc_attr}>')
    if call.config_ref:
        config_ref_escaped = (
            call.config_ref.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;").replace(">", "&gt;")
        )
        lines.append("    <munit-tools:with-attributes>")
        lines.append(
            '        <munit-tools:with-attribute attributeName="config-ref" '
            f"whereValue=\"#['{config_ref_escaped}']\"/>"
        )
        lines.append("    </munit-tools:with-attributes>")
    lines.append("    <munit-tools:then-return>")
    lines.append(f'        <munit-tools:payload value="{value_escaped}" mediaType="application/json"/>')
    lines.append("    </munit-tools:then-return>")
    lines.append("</munit-tools:mock-when>")
    return lines


def render_behavior_block_lines(
    calls: List[OutboundCall], then_return_values: Optional[dict] = None, include_doc_name: bool = True
) -> List[str]:
    """A full ``<munit:behavior>...</munit:behavior>`` block (as text
    lines) containing one mock-when per call -- used when splicing a brand
    new behavior section into an existing ``<munit:test>`` that doesn't
    have one yet. See ``render_mock_when_lines`` re: ``include_doc_name``.
    """
    then_return_values = then_return_values or {}
    lines = ["<munit:behavior>"]
    for call in calls:
        value = then_return_values.get(call.identity_key())
        for line in render_mock_when_lines(call, value, include_doc_name=include_doc_name):
            lines.append(f"    {line}")
    lines.append("</munit:behavior>")
    return lines


def render_new_test_lines(
    flow: FlowInfo, then_return_values: Optional[dict] = None, include_doc_name: bool = True
) -> List[str]:
    """A full ``<munit:test>...</munit:test>`` block (as text lines) for a
    flow that has no matching test in the file yet -- used when the test
    *file* already exists (for other flows defined in the same source file)
    but this particular flow doesn't have its own ``<munit:test>``. See
    ``render_mock_when_lines`` re: ``include_doc_name``.
    """
    then_return_values = then_return_values or {}
    distinct_calls = dedupe_calls(flow.outbound_calls)

    lines = [
        f'<munit:test name="test-{flow.name}" description="Auto-generated by munit-shadow for flow '
        f"'{flow.name}'. Fill in real assertions before relying on this test.\">",
    ]
    if distinct_calls:
        lines.append("    <munit:behavior>")
        for call in distinct_calls:
            value = then_return_values.get(call.identity_key())
            for line in render_mock_when_lines(call, value, include_doc_name=include_doc_name):
                lines.append(f"        {line}")
        lines.append("    </munit:behavior>")

    flow_ref_doc = f' doc:name="Call {flow.name}"' if include_doc_name else ""
    lines.append("    <munit:execution>")
    lines.append("        <munit:set-event>")
    lines.append('            <munit:payload value="#[{}]" mediaType="application/json"/>')
    lines.append("        </munit:set-event>")
    lines.append(f'        <flow-ref name="{flow.name}"{flow_ref_doc}/>')
    lines.append("    </munit:execution>")
    lines.append("    <munit:validation>")
    lines.append(
        f"        <!-- {sanitize_comment_text('TODO: placeholder assertion only -- replace with a real assertion for this flow')} -->"
    )
    lines.append('        <munit-tools:assert-that expression="#[payload]" is="#[MunitTools::notNullValue()]"/>')
    lines.append("    </munit:validation>")
    lines.append("</munit:test>")
    return lines


# --------------------------------------------------------------------------
# Whole-file generation (only for the "no test file yet" case)
# --------------------------------------------------------------------------


def _comment(text: str) -> ET.Element:
    return ET.Comment(f" {sanitize_comment_text(text)} ")


def build_test_for_flow(flow: FlowInfo) -> ET.Element:
    test = ET.Element(_q(MUNIT_NS, "test"))
    test.set("name", f"test-{flow.name}")
    test.set(
        "description",
        f"Auto-generated by munit-shadow for flow '{flow.name}'. Fill in real "
        "assertions before relying on this test.",
    )

    distinct_calls = dedupe_calls(flow.outbound_calls)

    if distinct_calls:
        behavior = ET.SubElement(test, _q(MUNIT_NS, "behavior"))
        for call in distinct_calls:
            if not call.config_ref:
                behavior.append(
                    _comment(
                        f"NOTE: this {call.operation} call has no config-ref, so this "
                        "mock cannot be scoped to a specific connector configuration; "
                        "it will apply to ALL such calls in this flow. Consider adding "
                        "a config-ref to disambiguate."
                    )
                )
            mock = ET.SubElement(behavior, _q(MUNIT_TOOLS_NS, "mock-when"))
            mock.set("processor", call.operation)
            doc_bits = [f"Mock {call.operation}"]
            if call.config_ref:
                doc_bits.append(f"config-ref={call.config_ref}")
            elif call.doc_name:
                doc_bits.append(f"call={call.doc_name}")
            mock.set(_q(DOC_NS, "name"), " - ".join(doc_bits))
            if call.config_ref:
                with_attrs = ET.SubElement(mock, _q(MUNIT_TOOLS_NS, "with-attributes"))
                with_attr = ET.SubElement(with_attrs, _q(MUNIT_TOOLS_NS, "with-attribute"))
                with_attr.set("attributeName", "config-ref")
                with_attr.set("whereValue", f"#['{call.config_ref}']")
            then_return = ET.SubElement(mock, _q(MUNIT_TOOLS_NS, "then-return"))
            payload = ET.SubElement(then_return, _q(MUNIT_TOOLS_NS, "payload"))
            payload.set("value", placeholder_payload_expr(call))
            payload.set("mediaType", "application/json")

    execution = ET.SubElement(test, _q(MUNIT_NS, "execution"))
    execution.append(
        _comment(
            "TODO: replace this placeholder payload with a realistic request "
            "for this flow."
        )
    )
    set_event = ET.SubElement(execution, _q(MUNIT_NS, "set-event"))
    set_payload = ET.SubElement(set_event, _q(MUNIT_NS, "payload"))
    set_payload.set("value", "#[{}]")
    set_payload.set("mediaType", "application/json")
    flow_ref = ET.SubElement(execution, _q(CORE_NS, "flow-ref"))
    flow_ref.set("name", flow.name)
    flow_ref.set(_q(DOC_NS, "name"), f"Call {flow.name}")

    validation = ET.SubElement(test, _q(MUNIT_NS, "validation"))
    validation.append(
        _comment(
            "TODO: this is a placeholder assertion only -- it proves the flow "
            "ran and produced a non-null payload, nothing about correctness."
        )
    )
    assert_that = ET.SubElement(validation, _q(MUNIT_TOOLS_NS, "assert-that"))
    assert_that.set("expression", "#[payload]")
    assert_that.set("is", "#[MunitTools::notNullValue()]")

    return test


def render_new_test_suite_text(flows: List[FlowInfo]) -> str:
    """Full ``<mule>``-rooted MUnit test-suite XML text for a brand new test
    file (the "no test file yet" case) -- one ``<munit:test>`` per flow.
    """
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("munit", MUNIT_NS)
    ET.register_namespace("munit-tools", MUNIT_TOOLS_NS)
    ET.register_namespace("doc", DOC_NS)
    ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

    root = ET.Element(_q(CORE_NS, "mule"))
    root.set("{http://www.w3.org/2001/XMLSchema-instance}schemaLocation", _SCHEMA_LOCATION)

    config = ET.SubElement(root, _q(MUNIT_NS, "config"))
    config.set("name", "munit-shadow-generated")

    for flow in flows:
        root.append(build_test_for_flow(flow))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    xml_bytes = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    return xml_bytes.decode("utf-8") + "\n"
