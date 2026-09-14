"""Builds the generated MUnit test-suite XML tree.

No LLM involved anywhere in this module -- this is the deterministic base
scaffold described in README.md. ``enrich.py`` is the only module that ever
calls out to Claude, and only when ``--enrich`` is passed.

MUnit XML element/attribute shapes used below were confirmed on 2026-09-13
against the current MuleSoft documentation -- see README.md's "Mule 4 /
MUnit XML schema research" section for the full citation list and, notably,
one place where the obvious guess would have been WRONG: the set-event
processor lives in the ``munit`` namespace (``<munit:set-event>``), not
``munit-tools`` as its "tools"-ish name might suggest.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import List, Optional

from .xml_parser import CORE_NS, FlowInfo, OutboundCall, ParsedApp, dedupe_calls

MUNIT_NS = "http://www.mulesoft.org/schema/mule/munit"
MUNIT_TOOLS_NS = "http://www.mulesoft.org/schema/mule/munit-tools"
DOC_NS = "http://www.mulesoft.org/schema/mule/documentation"
XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

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


def _placeholder_payload_expr(call: OutboundCall) -> str:
    """A deterministic, structurally-plausible placeholder response payload.
    Not a real guess at the connector's actual response shape -- just a
    non-null object an engineer can immediately see and replace. --enrich
    is what produces something smarter, grounded in the real flow XML.
    """
    op_label = call.operation.replace(":", "_").replace("-", "_")
    return (
        "#[{ "
        f"mocked: true, "
        f"operation: '{call.operation}', "
        f"note: 'TODO({op_label}): replace with a realistic mock response payload' "
        "}]"
    )


def _sanitize_comment_text(text: str) -> str:
    """XML forbids "--" inside a comment (and a comment may not end in "-").
    Collapse any run of 2+ hyphens down to a single hyphen so every
    generated <!-- ... --> is guaranteed well-formed regardless of what
    free-text note is passed in.
    """
    text = re.sub(r"-{2,}", "-", text)
    return text.rstrip("-")


def _comment(text: str) -> ET.Element:
    return ET.Comment(f" {_sanitize_comment_text(text)} ")


def build_mock_when(call: OutboundCall) -> List[ET.Element]:
    """Returns a list of sibling elements to append into a <munit:behavior>:
    an optional advisory comment, followed by the <munit-tools:mock-when>.
    """
    elems: List[ET.Element] = []

    mock = ET.Element(_q(MUNIT_TOOLS_NS, "mock-when"))
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
    else:
        elems.append(
            _comment(
                f"NOTE: this {call.operation} call has no config-ref, so this mock "
                "cannot be scoped to a specific connector configuration. If this "
                "flow calls the same operation more than once without a "
                "distinguishing config-ref, MUnit's mock-when cannot tell those "
                "call sites apart at runtime (it matches by processor + message "
                "attributes, not by doc:name or XML position) -- this mock will "
                "apply to ALL such calls in this flow. Consider adding a "
                "config-ref, or a with-attributes filter on a real request "
                "attribute, to disambiguate."
            )
        )

    then_return = ET.SubElement(mock, _q(MUNIT_TOOLS_NS, "then-return"))
    payload = ET.SubElement(then_return, _q(MUNIT_TOOLS_NS, "payload"))
    payload.set("value", _placeholder_payload_expr(call))
    payload.set("mediaType", "application/json")

    elems.append(mock)
    return elems


def build_test_for_flow(flow: FlowInfo) -> ET.Element:
    test = ET.Element(_q(MUNIT_NS, "test"))
    test.set("name", f"test-{flow.name}")
    test.set(
        "description",
        f"Auto-generated skeleton for flow '{flow.name}'. Fill in real mock "
        "payloads and assertions before relying on this test.",
    )

    distinct_calls = dedupe_calls(flow.outbound_calls)

    if distinct_calls:
        behavior = ET.SubElement(test, _q(MUNIT_NS, "behavior"))
        for call in distinct_calls:
            for elem in build_mock_when(call):
                behavior.append(elem)

    execution = ET.SubElement(test, _q(MUNIT_NS, "execution"))

    if flow.entry_point_type and flow.entry_point_type.split(":")[-1] == "scheduler":
        execution.append(
            _comment(
                "This flow is triggered by a scheduler (no meaningful inbound "
                "payload/attributes in production) -- this set-event stub may "
                "not be needed; remove it if the flow ignores its inbound event."
            )
        )
    else:
        execution.append(
            _comment(
                "TODO: replace this placeholder payload with a realistic "
                "request for this flow (e.g. the JSON/XML body an inbound "
                "http:listener would actually receive)."
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
            "ran and produced a non-null payload, nothing about correctness. "
            "Replace with real assertions about the actual expected payload/"
            "attributes/variables for this flow."
        )
    )
    assert_that = ET.SubElement(validation, _q(MUNIT_TOOLS_NS, "assert-that"))
    assert_that.set("expression", "#[payload]")
    assert_that.set("is", "#[MunitTools::notNullValue()]")

    return test


def build_test_suite(parsed: ParsedApp, include_subflows: bool = False) -> ET.ElementTree:
    ET.register_namespace("", CORE_NS)
    ET.register_namespace("munit", MUNIT_NS)
    ET.register_namespace("munit-tools", MUNIT_TOOLS_NS)
    ET.register_namespace("doc", DOC_NS)
    ET.register_namespace("xsi", XSI_NS)

    root = ET.Element(_q(CORE_NS, "mule"))
    root.set(_q(XSI_NS, "schemaLocation"), _SCHEMA_LOCATION)

    config = ET.SubElement(root, _q(MUNIT_NS, "config"))
    config.set("name", "generated-test-suite")

    for flow in parsed.flows.values():
        if flow.kind == "sub-flow" and not include_subflows:
            continue
        root.append(build_test_for_flow(flow))

    tree = ET.ElementTree(root)
    ET.indent(tree, space="    ")
    return tree


def write_test_suite(tree: ET.ElementTree, out_path) -> None:
    tree.write(out_path, encoding="UTF-8", xml_declaration=True)


def flows_in_scope(parsed: ParsedApp, include_subflows: bool) -> List[FlowInfo]:
    return [
        f
        for f in parsed.flows.values()
        if include_subflows or f.kind == "flow"
    ]
