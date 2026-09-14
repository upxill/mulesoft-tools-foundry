"""Deterministic, self-contained parsing of Mule 4 flow XML.

This module is NOT imported from, and does not import, the sibling project
mule-munit-scaffold (a separate one-shot, blank-slate MUnit generator by the
same author). munit-shadow needs equivalent-quality outbound-call detection
because it solves an adjacent but different problem -- ambient, incremental,
in-place sync of an EXISTING test file rather than generating a new one from
scratch -- so the detector below is written fresh, though it deliberately
targets the same detection quality bar and cites the same kind of sources.

XML element shapes this module understands were checked against current
MuleSoft documentation on 2026-09-14:

  - Flow and Subflow Scopes (https://docs.mulesoft.com/mule-runtime/latest/flow-component)
    -- <flow name="...">, <sub-flow name="...">, <flow-ref name="..."/>.
  - HTTP Connector XML reference -- <http:listener>, <http:request>.
  - DB Connector reference -- <db:select>, <db:insert>, <db:update>, <db:delete>.
  - MUnit Test Structure Fundamentals (https://docs.mulesoft.com/munit/latest/munit-test-concept)
    -- confirms MUnit test suites live under src/test/munit while a Mule
    application's own flow XML lives under src/main/mule (the doc: "The base
    file in MUnit is the test suite file, an XML file located in the
    src/test/munit directory of your Mule application project.").
  - MUnit Tools Module Reference (https://docs.mulesoft.com/munit/latest/munit-tools-module-reference)
    and the Mock Event Processor page -- <munit-tools:mock-when
    processor="...">, <munit-tools:with-attributes>/<munit-tools:with-attribute
    attributeName="..." whereValue="..."/>, <munit-tools:then-return>/
    <munit-tools:payload value="..." mediaType="..."/>.

Detection heuristic for "outbound connector call" (documented here and in
README.md -- this is the load-bearing judgment call munit-shadow depends on,
same principle as mule-munit-scaffold's heuristic, re-derived independently):

  1. The element's namespace must NOT be the Mule core namespace
     (``http://www.mulesoft.org/schema/mule/core``) and must NOT be the
     EE/DataWeave transform namespace (``.../ee/core``). Core flow-control
     elements (<choice>, <when>, <otherwise>, <try>, <foreach>, <logger>,
     <flow-ref>, <set-payload>, <set-variable>, <error-handler>, ...) and
     in-process DataWeave transforms (<ee:transform>) never leave the Mule
     runtime by themselves, so they are excluded regardless of nesting depth.
  2. The element's local (namespace-stripped) tag name must be in a curated
     set of verb-shaped operation names that denote real outbound I/O:
     request, select, insert, update, upsert, delete, bulk-insert,
     bulk-update, bulk-delete, query, publish, consume, publish-consume,
     send, execute-ddl, execute-script, stored-procedure.
  3. Message *sources* (http:listener, <scheduler>, anything ending in
     -listener/-trigger) are excluded by construction: none of their local
     names are in the operation-verb set above.

This is a heuristic, not a full connector operation catalog -- see the
Limitations section of README.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Namespaces
# --------------------------------------------------------------------------

CORE_NS = "http://www.mulesoft.org/schema/mule/core"
EE_NS = "http://www.mulesoft.org/schema/mule/ee/core"
DOC_NS = "http://www.mulesoft.org/schema/mule/documentation"
MUNIT_NS = "http://www.mulesoft.org/schema/mule/munit"
MUNIT_TOOLS_NS = "http://www.mulesoft.org/schema/mule/munit-tools"

# Local (namespace-stripped) tag names treated as outbound connector
# *operations* -- real I/O against something outside the Mule runtime, and
# therefore something a MUnit test needs to mock.
OPERATION_LOCAL_NAMES = {
    "request",
    "select",
    "insert",
    "update",
    "upsert",
    "delete",
    "bulk-insert",
    "bulk-update",
    "bulk-delete",
    "query",
    "publish",
    "consume",
    "publish-consume",
    "send",
    "execute-ddl",
    "execute-script",
    "stored-procedure",
}

_SOURCE_SUFFIXES = ("listener", "trigger")
_SOURCE_EXACT_NAMES = {"scheduler"}


# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class OutboundCall:
    """A single outbound connector operation invocation found in a flow."""

    flow_name: str
    operation: str  # e.g. "http:request", "db:select"
    config_ref: Optional[str]
    doc_name: Optional[str]

    def identity_key(self) -> Tuple[str, Optional[str]]:
        """The key used to decide whether two calls are "the same" call for
        mocking purposes: same operation and same config-ref -- the only
        two things MUnit's mock-when can actually filter on at runtime
        (it has no notion of source-XML position or doc:name). This is
        deliberately simpler than "one mock per line of XML": two calls to
        the same operation sharing the same config-ref (or both lacking
        one) are indistinguishable to MUnit and are treated as one call.
        This also makes the key round-trippable: it is exactly what can be
        recovered by re-reading an existing <munit-tools:mock-when
        processor="..."> block's processor + with-attributes, which is what
        mock_diff.py needs to match freshly-detected calls against already-
        mocked ones in an existing test file. See README's "Detection
        heuristic" / "Mock disambiguation" sections for the honest ceiling
        this implies.
        """
        return (self.operation, self.config_ref)


@dataclass
class FlowInfo:
    name: str
    kind: str  # "flow" or "sub-flow"
    is_entry_point: bool = False
    entry_point_type: Optional[str] = None
    outbound_calls: List[OutboundCall] = field(default_factory=list)


@dataclass
class ParsedFlowFile:
    path: Path
    flows: "Dict[str, FlowInfo]"  # insertion order = first-seen order in source


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _namespace_uri(tag: str) -> Optional[str]:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else None


def _build_ns_map(raw_text: str) -> Dict[str, str]:
    """Map namespace URI -> prefix by scanning xmlns:* declarations in the
    raw source text (not the parsed tree, which loses the original prefix)."""
    ns_map: Dict[str, str] = {}
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', raw_text):
        prefix, uri = m.group(1), m.group(2)
        ns_map[uri] = prefix
    return ns_map


def _qualified_display_name(elem: ET.Element, ns_map: Dict[str, str]) -> str:
    """Return e.g. 'http:request' for a {http-uri}request element."""
    if "}" in elem.tag:
        uri, local = elem.tag[1:].split("}", 1)
        prefix = ns_map.get(uri)
        return f"{prefix}:{local}" if prefix else local
    return elem.tag


def _is_outbound_operation(elem: ET.Element) -> bool:
    uri = _namespace_uri(elem.tag)
    if uri is None or uri == CORE_NS or uri == EE_NS:
        return False
    local = _local_name(elem.tag)
    return local in OPERATION_LOCAL_NAMES


def _is_source_element(elem: ET.Element) -> bool:
    local = _local_name(elem.tag)
    return local in _SOURCE_EXACT_NAMES or any(local.endswith(s) for s in _SOURCE_SUFFIXES)


# --------------------------------------------------------------------------
# Public API
# --------------------------------------------------------------------------


def parse_flow_file(path: Path) -> ParsedFlowFile:
    """Parse a single Mule flow XML file (``src/main/mule/*.xml``) into its
    flows/sub-flows and their outbound connector calls.

    Raises ``ET.ParseError`` if the file isn't well-formed XML.
    """
    path = Path(path)
    raw_text = path.read_text(encoding="utf-8")
    tree = ET.parse(path)
    root = tree.getroot()
    ns_map = _build_ns_map(raw_text)

    flows: "Dict[str, FlowInfo]" = {}
    flow_elements: Dict[ET.Element, str] = {}

    for elem in root.iter():
        local = _local_name(elem.tag)
        if local in ("flow", "sub-flow"):
            name = elem.get("name")
            if not name:
                continue
            flow_elements[elem] = name
            flows[name] = FlowInfo(name=name, kind=local)

    for flow_elem, flow_name in flow_elements.items():
        info = flows[flow_name]

        for child in list(flow_elem):
            if _is_source_element(child):
                info.is_entry_point = True
                info.entry_point_type = _qualified_display_name(child, ns_map)
                break

        for d in flow_elem.iter():
            if d is flow_elem:
                continue
            if _is_outbound_operation(d):
                op_name = _qualified_display_name(d, ns_map)
                call = OutboundCall(
                    flow_name=flow_name,
                    operation=op_name,
                    config_ref=d.get("config-ref"),
                    doc_name=d.get(f"{{{DOC_NS}}}name") or d.get("doc:name"),
                )
                info.outbound_calls.append(call)

    return ParsedFlowFile(path=path, flows=flows)


def dedupe_calls(calls: List[OutboundCall]) -> List[OutboundCall]:
    """Collapse calls that are indistinguishable to MUnit's mock-when (same
    identity key) into a single representative call, keeping first-seen
    order. This is the set of "distinct outbound calls" munit-shadow mocks.
    """
    seen: Dict[Tuple[str, Optional[str]], OutboundCall] = {}
    for call in calls:
        key = call.identity_key()
        if key not in seen:
            seen[key] = call
    return list(seen.values())


def get_flow_raw_xml(path: Path, flow_name: str) -> str:
    """Re-parses ``path`` and returns the serialized XML of just the
    <flow>/<sub-flow> element named ``flow_name`` -- used only by
    ``enrich.py`` to give the model real grounding, never by the
    deterministic sync engine.
    """
    tree = ET.parse(path)
    for elem in tree.getroot().iter():
        if _local_name(elem.tag) in ("flow", "sub-flow") and elem.get("name") == flow_name:
            return ET.tostring(elem, encoding="unicode")
    return ""


def distinct_calls_for_file(parsed: ParsedFlowFile) -> List[OutboundCall]:
    """All distinct outbound calls across every flow/sub-flow in a file, in
    first-seen order. munit-shadow treats one flow file <-> one test file, so
    calls from every flow defined in that file are pooled together (matching
    how a hand-written test suite file usually has one <munit:test> per flow
    defined in the corresponding flow file).
    """
    all_calls: List[OutboundCall] = []
    for flow in parsed.flows.values():
        all_calls.extend(flow.outbound_calls)
    return dedupe_calls(all_calls)
