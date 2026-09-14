"""Deterministic, testable parsing of Mule 4 flow XML.

No LLM involved anywhere in this module. Everything here is derived by
walking the real XML tree with ``xml.etree.ElementTree`` from the standard
library.

XML element shapes this module understands were confirmed on 2026-09-13
against the current MuleSoft documentation (see README.md "Mule 4 / MUnit
XML schema research" section for the full citation list): <flow>/<sub-flow>,
<flow-ref>, connector operation elements such as <http:request>/<db:select>,
and the <ee:transform> DataWeave-in-XML shape (which is deliberately NOT
treated as an outbound call -- see the detection heuristic below).

Detection heuristic for "outbound connector call" (documented here and in
README.md, since this is the load-bearing judgment call the whole tool
depends on):

  1. The element's namespace must NOT be the Mule core namespace
     (``http://www.mulesoft.org/schema/mule/core``) and must NOT be an
     EE/DataWeave transform namespace (``.../ee/core``). Core flow-control
     elements (<choice>, <when>, <otherwise>, <try>, <foreach>, <logger>,
     <flow-ref>, <set-payload>, <set-variable>, <error-handler>, ...) and
     in-process DataWeave transforms (<ee:transform>) are never I/O by
     themselves -- they don't leave the Mule runtime -- so they are
     correctly excluded regardless of how deeply we look inside them.
  2. The element's local (namespace-stripped) tag name must match a
     curated set of verb-shaped operation names that denote outbound I/O
     on a connector: request/select/insert/update/upsert/delete and the
     bulk-* variants, query, publish, consume, publish-consume, send,
     execute-ddl, execute-script, stored-procedure. This mirrors (and
     extends slightly) the connector-call detector already verified in
     the sibling project mule-flow-doctor.
  3. Message *sources* (listeners/triggers/schedulers) are excluded by
     construction: none of their local names match the operation-verb set
     above (e.g. "listener", "scheduler" are not in it), so they are never
     mistaken for outbound calls even though they also sit on a
     non-core/non-ee namespace.

This is a heuristic, not a semantic model of every connector's operation
catalog -- see the "Detection heuristic (honest limitations)" section of
README.md.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# --------------------------------------------------------------------------
# Data model
# --------------------------------------------------------------------------


@dataclass
class OutboundCall:
    """A single outbound connector operation invocation found inside a flow."""

    file: str
    flow_name: str
    operation: str  # e.g. "http:request", "db:select"
    config_ref: Optional[str]
    doc_name: Optional[str]

    def dedupe_key(self) -> Tuple[str, Optional[str], Optional[str]]:
        """The key used to decide whether two calls are the "same" outbound
        call for mocking purposes: same operation, and, if present, the
        same config-ref (the only thing MUnit's mock-when can actually
        filter on at runtime) or -- failing that -- the same doc:name
        (a source-XML-identity disambiguator; see README's honest caveat
        about its real disambiguation power).
        """
        return (self.operation, self.config_ref, self.doc_name if self.config_ref is None else None)


@dataclass
class FlowInfo:
    name: str
    kind: str  # "flow" or "sub-flow"
    file: str
    is_entry_point: bool = False
    entry_point_type: Optional[str] = None
    flow_refs: List[str] = field(default_factory=list)
    outbound_calls: List[OutboundCall] = field(default_factory=list)


@dataclass
class ParsedApp:
    files: List[str]
    flows: "Dict[str, FlowInfo]"  # insertion-ordered: first-seen order in the source


# --------------------------------------------------------------------------
# Constants
# --------------------------------------------------------------------------

CORE_NS = "http://www.mulesoft.org/schema/mule/core"
EE_NS = "http://www.mulesoft.org/schema/mule/ee/core"

# Local (namespace-stripped) tag names treated as outbound connector
# *operations* -- i.e. calls that perform real I/O against something
# outside the Mule runtime and therefore need a MUnit mock.
_OPERATION_LOCAL_NAMES = {
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


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _namespace_uri(tag: str) -> Optional[str]:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") else None


def _qualified_display_name(elem: ET.Element, ns_map: Dict[str, str]) -> str:
    """Return e.g. 'http:request' for a {http-uri}request element."""
    if "}" in elem.tag:
        uri, local = elem.tag[1:].split("}", 1)
        prefix = ns_map.get(uri)
        return f"{prefix}:{local}" if prefix else local
    return elem.tag


def _build_ns_map(raw_text: str) -> Dict[str, str]:
    """Map namespace URI -> prefix by scanning xmlns:* declarations in the raw text."""
    ns_map: Dict[str, str] = {}
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', raw_text):
        prefix, uri = m.group(1), m.group(2)
        ns_map[uri] = prefix
    return ns_map


def _is_outbound_operation(elem: ET.Element) -> bool:
    uri = _namespace_uri(elem.tag)
    if uri is None or uri == CORE_NS or uri == EE_NS:
        return False
    local = _local_name(elem.tag)
    return local in _OPERATION_LOCAL_NAMES


def _is_source_element(elem: ET.Element) -> bool:
    local = _local_name(elem.tag)
    return local in _SOURCE_EXACT_NAMES or any(local.endswith(s) for s in _SOURCE_SUFFIXES)


def _collect_xml_files(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    return sorted(p for p in path.rglob("*.xml"))


def parse_app(path: Path) -> ParsedApp:
    """Parse every ``*.xml`` file under ``path`` (or just ``path`` if it's a
    single file) into a :class:`ParsedApp`.
    """
    path = Path(path)
    xml_files = _collect_xml_files(path)
    if not xml_files:
        raise ValueError(f"No .xml files found under {path}")

    flows: "Dict[str, FlowInfo]" = {}

    for f in xml_files:
        raw_text = f.read_text(encoding="utf-8")
        try:
            tree = ET.parse(f)
        except ET.ParseError as exc:
            raise ValueError(f"Failed to parse {f}: {exc}") from exc
        root = tree.getroot()
        ns_map = _build_ns_map(raw_text)
        rel_file = str(f)

        flow_elements: Dict[ET.Element, str] = {}
        for elem in root.iter():
            local = _local_name(elem.tag)
            if local in ("flow", "sub-flow"):
                name = elem.get("name")
                if not name:
                    continue
                flow_elements[elem] = name
                flows[name] = FlowInfo(name=name, kind=local, file=rel_file)

        for flow_elem, flow_name in flow_elements.items():
            info = flows[flow_name]

            for child in list(flow_elem):
                if _is_source_element(child):
                    info.is_entry_point = True
                    info.entry_point_type = _qualified_display_name(child, ns_map)
                    break

            for d in flow_elem.iter():
                if _local_name(d.tag) == "flow-ref":
                    target = d.get("name")
                    if target:
                        info.flow_refs.append(target)

            for d in flow_elem.iter():
                if d is flow_elem:
                    continue
                if _is_outbound_operation(d):
                    op_name = _qualified_display_name(d, ns_map)
                    call = OutboundCall(
                        file=rel_file,
                        flow_name=flow_name,
                        operation=op_name,
                        config_ref=d.get("config-ref"),
                        doc_name=d.get("{http://www.mulesoft.org/schema/mule/documentation}name")
                        or d.get("doc:name"),
                    )
                    info.outbound_calls.append(call)

    return ParsedApp(files=[str(f) for f in xml_files], flows=flows)


def get_flow_raw_xml(flow: FlowInfo) -> str:
    """Re-parses ``flow.file`` and returns the serialized XML of just the
    <flow>/<sub-flow> element named ``flow.name`` -- used only by
    ``enrich.py`` to give the model the real flow body as grounding, never
    by the deterministic scaffold path.
    """
    tree = ET.parse(flow.file)
    for elem in tree.getroot().iter():
        if _local_name(elem.tag) in ("flow", "sub-flow") and elem.get("name") == flow.name:
            return ET.tostring(elem, encoding="unicode")
    return ""


def dedupe_calls(calls: "List[OutboundCall]") -> "List[OutboundCall]":
    """Collapse calls that are indistinguishable to MUnit's mock-when (same
    dedupe key) into a single representative call, keeping first-seen order.
    This is the "distinct outbound call" count the whole tool is built
    around -- see xml_parser module docstring and README.md.
    """
    seen: Dict[Tuple[str, Optional[str], Optional[str]], OutboundCall] = {}
    for call in calls:
        key = call.dedupe_key()
        if key not in seen:
            seen[key] = call
    return list(seen.values())
