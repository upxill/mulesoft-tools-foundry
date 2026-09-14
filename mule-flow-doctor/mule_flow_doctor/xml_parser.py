"""Deterministic, testable parsing of Mule 4 application XML.

No LLM involved anywhere in this module. Everything here is derived by
walking the real XML tree with ``xml.etree.ElementTree`` from the standard
library. The extracted structure is what backs both the architecture
diagram (graph.py) and the grounding data fed to the LLM (reviewer.py).

XML element shapes this module understands were confirmed on 2026-09-13
against the current MuleSoft documentation (see README.md for the full
citation list): <flow>/<sub-flow>, <flow-ref>, <error-handler> and its
<on-error-continue>/<on-error-propagate> children, <*-config>/<*-connection>
pairs with a nested <reconnection><reconnect .../></reconnection> strategy,
and <ee:transform>/<ee:message>/<ee:set-payload> DataWeave bodies.
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
class ConnectorCall:
    """A single connector operation invocation found inside a flow/sub-flow."""

    file: str
    flow_name: Optional[str]
    operation: str  # e.g. "http:request", "db:select"
    config_ref: Optional[str]
    has_reconnection: Optional[bool]  # None = config-ref could not be resolved


@dataclass
class FlowInfo:
    name: str
    kind: str  # "flow" or "sub-flow"
    file: str
    has_error_handler: bool = False
    is_entry_point: bool = False
    entry_point_type: Optional[str] = None
    flow_refs: List[str] = field(default_factory=list)
    connector_calls: List[ConnectorCall] = field(default_factory=list)


@dataclass
class SecretCandidate:
    file: str
    flow_name: Optional[str]
    location: str  # e.g. "attribute:db:my-sql-connection@password"
    category: str  # "hardcoded-secret-attribute" | "hardcoded-secret-literal"
    value_preview: str


@dataclass
class DataWeaveIssue:
    file: str
    flow_name: Optional[str]
    element: str
    collection: str
    pass_count: int
    kind: str = "repeated-collection-scan"


@dataclass
class ParsedApp:
    files: List[str]
    flows: Dict[str, FlowInfo]
    connector_calls: List[ConnectorCall]
    secrets: List[SecretCandidate]
    dataweave_issues: List[DataWeaveIssue]
    edges: List[Tuple[str, str]]  # (source_flow, target_flow) from <flow-ref>
    unresolved_configs: List[str]  # config-ref names that reference no known config


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

CORE_NS = "http://www.mulesoft.org/schema/mule/core"

# Local (namespace-stripped) tag names that are treated as connector
# *operations* (calls), as opposed to sources/listeners or config elements.
_OPERATION_LOCAL_NAMES = {
    "request",
    "select",
    "insert",
    "update",
    "delete",
    "upsert",
    "publish",
    "consume",
    "query",
    "execute-ddl",
    "execute-script",
    "stored-procedure",
    "bulk-insert",
    "send",
    "publish-consume",
}

# Local tag-name suffixes/exact-names that mark a flow's message *source*
# (an inbound entry point into the app), as opposed to an internal step.
_SOURCE_SUFFIXES = ("listener", "trigger")
_SOURCE_EXACT_NAMES = {"scheduler"}

_SUSPICIOUS_ATTR_RE = re.compile(
    r"(?i)^(password|passwd|pwd|secret|api[_-]?key|apikey|access[_-]?key|"
    r"accesskey|client[_-]?secret|clientsecret|token|connection[_-]?string|"
    r"connectionstring|credential)s?$"
)

_PLACEHOLDER_RE = re.compile(r"^\$\{.*\}$|^#\[.*\]$", re.DOTALL)

# Well-known literal secret shapes that can show up as text content
# (e.g. inside a DataWeave/JSON literal in an <http:headers> CDATA body)
# as well as attribute values.
_LITERAL_SECRET_PATTERNS = [
    re.compile(r"sk_(?:live|test)_[A-Za-z0-9]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"xox[abp]-[A-Za-z0-9-]{10,}"),
]

# Matches a JSON/DW-style literal key:value pair whose key looks like a
# secret and whose value is a plain quoted string literal (not a property
# placeholder or expression).
_KEYED_LITERAL_RE = re.compile(
    r"""["']?(?P<key>[A-Za-z_][\w-]*)["']?\s*[:=]\s*["'](?P<val>[^"'$#][^"']{4,})["']"""
)

_DW_COLLECTION_OP_RE = re.compile(
    r"([A-Za-z_][\w.\[\]]*)\s*\.?\s*(?:map|filter|reduce)\s*\("
)


def _local_name(tag: str) -> str:
    return tag.split("}", 1)[1] if "}" in tag else tag


def _qualified_display_name(elem: ET.Element, ns_map: Dict[str, str]) -> str:
    """Return e.g. 'http:request' for a {http-uri}request element."""
    if "}" in elem.tag:
        uri, local = elem.tag[1:].split("}", 1)
        prefix = ns_map.get(uri)
        return f"{prefix}:{local}" if prefix else local
    return elem.tag


def _build_ns_map(root: ET.Element, raw_text: str) -> Dict[str, str]:
    """Map namespace URI -> prefix by scanning xmlns:* declarations in the raw text."""
    ns_map: Dict[str, str] = {}
    for m in re.finditer(r'xmlns:([A-Za-z0-9_.-]+)="([^"]+)"', raw_text):
        prefix, uri = m.group(1), m.group(2)
        ns_map[uri] = prefix
    return ns_map


def _build_parent_map(root: ET.Element) -> Dict[ET.Element, ET.Element]:
    return {child: parent for parent in root.iter() for child in parent}


def _enclosing_flow(
    elem: ET.Element,
    parent_map: Dict[ET.Element, ET.Element],
    flow_elements: Dict[ET.Element, str],
) -> Optional[str]:
    cur = elem
    while cur is not None:
        if cur in flow_elements:
            return flow_elements[cur]
        cur = parent_map.get(cur)
    return None


def _is_placeholder(value: str) -> bool:
    return bool(_PLACEHOLDER_RE.match(value.strip()))


def _redact(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return value[:2] + "..."
    return value[:6] + "..." + f"({len(value)} chars)"


# --------------------------------------------------------------------------
# Core parsing
# --------------------------------------------------------------------------


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

    trees: List[Tuple[Path, ET.ElementTree, ET.Element, Dict[str, str]]] = []
    for f in xml_files:
        raw_text = f.read_text(encoding="utf-8")
        try:
            tree = ET.parse(f)
        except ET.ParseError as exc:
            raise ValueError(f"Failed to parse {f}: {exc}") from exc
        root = tree.getroot()
        ns_map = _build_ns_map(root, raw_text)
        trees.append((f, tree, root, ns_map))

    # --- Pass 1: collect every "config"-shaped element (local name is
    # "config" or ends with "-config") and whether its subtree contains a
    # <reconnection> strategy. Config elements are addressed by `name` and
    # can be referenced from *any* file in the app (as in a real Mule app,
    # where global configs typically live in their own file).
    reconnection_by_config: Dict[str, bool] = {}
    for _f, _tree, root, _ns in trees:
        for elem in root.iter():
            local = _local_name(elem.tag)
            if local == "config" or local.endswith("-config"):
                name = elem.get("name")
                if not name:
                    continue
                has_reconnect = any(
                    _local_name(d.tag) == "reconnection" for d in elem.iter()
                )
                reconnection_by_config[name] = has_reconnect

    # --- Pass 2: walk each file's flows/sub-flows.
    flows: Dict[str, FlowInfo] = {}
    connector_calls: List[ConnectorCall] = []
    secrets: List[SecretCandidate] = []
    dataweave_issues: List[DataWeaveIssue] = []
    edges: List[Tuple[str, str]] = []
    unresolved_configs: List[str] = []

    for f, tree, root, ns_map in trees:
        rel_file = str(f)
        parent_map = _build_parent_map(root)

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

            # Error handler anywhere in the flow's subtree (inline, or
            # nested inside a <try> scope).
            info.has_error_handler = any(
                _local_name(d.tag) == "error-handler" for d in flow_elem.iter()
            )

            # Entry point: any direct child that looks like a message source.
            for child in list(flow_elem):
                local = _local_name(child.tag)
                if local in _SOURCE_EXACT_NAMES or any(
                    local.endswith(suf) for suf in _SOURCE_SUFFIXES
                ):
                    info.is_entry_point = True
                    info.entry_point_type = _qualified_display_name(child, ns_map)
                    break

            # flow-ref edges.
            for d in flow_elem.iter():
                if _local_name(d.tag) == "flow-ref":
                    target = d.get("name")
                    if target:
                        info.flow_refs.append(target)
                        edges.append((flow_name, target))

            # Connector operation calls + reconnection lookup.
            for d in flow_elem.iter():
                local = _local_name(d.tag)
                if local in _OPERATION_LOCAL_NAMES:
                    op_name = _qualified_display_name(d, ns_map)
                    config_ref = d.get("config-ref")
                    has_recon: Optional[bool]
                    if config_ref is None:
                        has_recon = None
                    elif config_ref in reconnection_by_config:
                        has_recon = reconnection_by_config[config_ref]
                    else:
                        has_recon = None
                        if config_ref not in unresolved_configs:
                            unresolved_configs.append(config_ref)
                    call = ConnectorCall(
                        file=rel_file,
                        flow_name=flow_name,
                        operation=op_name,
                        config_ref=config_ref,
                        has_reconnection=has_recon,
                    )
                    connector_calls.append(call)
                    info.connector_calls.append(call)

            # DataWeave inefficiency scan: any element text containing "%dw".
            for d in flow_elem.iter():
                text = d.text or ""
                if "%dw" in text:
                    counts: Dict[str, int] = {}
                    for m in _DW_COLLECTION_OP_RE.finditer(text):
                        ident = m.group(1)
                        counts[ident] = counts.get(ident, 0) + 1
                    for ident, count in counts.items():
                        if count >= 2:
                            dataweave_issues.append(
                                DataWeaveIssue(
                                    file=rel_file,
                                    flow_name=flow_name,
                                    element=_qualified_display_name(d, ns_map),
                                    collection=ident,
                                    pass_count=count,
                                )
                            )

        # --- Secret scan across the *entire* file (including elements
        # outside any flow, e.g. global <db:config>/<*-connection> blocks).
        for elem in root.iter():
            tag_display = _qualified_display_name(elem, ns_map)
            flow_ctx = _enclosing_flow(elem, parent_map, flow_elements)

            for attr_name, attr_value in elem.attrib.items():
                local_attr = _local_name(attr_name)
                if not attr_value or _is_placeholder(attr_value):
                    continue
                if _SUSPICIOUS_ATTR_RE.match(local_attr):
                    secrets.append(
                        SecretCandidate(
                            file=rel_file,
                            flow_name=flow_ctx,
                            location=f"attribute:{tag_display}@{local_attr}",
                            category="hardcoded-secret-attribute",
                            value_preview=_redact(attr_value),
                        )
                    )
                    continue
                for pat in _LITERAL_SECRET_PATTERNS:
                    if pat.search(attr_value):
                        secrets.append(
                            SecretCandidate(
                                file=rel_file,
                                flow_name=flow_ctx,
                                location=f"attribute:{tag_display}@{local_attr}",
                                category="hardcoded-secret-literal",
                                value_preview=_redact(attr_value),
                            )
                        )
                        break

            text = elem.text or ""
            if text.strip():
                for pat in _LITERAL_SECRET_PATTERNS:
                    m = pat.search(text)
                    if m:
                        secrets.append(
                            SecretCandidate(
                                file=rel_file,
                                flow_name=flow_ctx,
                                location=f"element-text:{tag_display}",
                                category="hardcoded-secret-literal",
                                value_preview=_redact(m.group(0)),
                            )
                        )
                for m in _KEYED_LITERAL_RE.finditer(text):
                    key = m.group("key")
                    val = m.group("val")
                    if _SUSPICIOUS_ATTR_RE.match(key) and not _is_placeholder(val):
                        secrets.append(
                            SecretCandidate(
                                file=rel_file,
                                flow_name=flow_ctx,
                                location=f"element-text:{tag_display}[{key}]",
                                category="hardcoded-secret-literal",
                                value_preview=_redact(val),
                            )
                        )

    # De-duplicate secrets: the generic literal-pattern detector and the
    # keyed-literal detector can both fire on the same underlying value
    # (e.g. a "sk_live_..." string inside a JSON key:value literal). Group
    # by (file, redacted value) and prefer the most specific location (the
    # one naming the actual key, e.g. "...[apiKey]") when both exist.
    groups: Dict[Tuple[str, str], List[SecretCandidate]] = {}
    for s in secrets:
        groups.setdefault((s.file, s.value_preview), []).append(s)
    deduped: List[SecretCandidate] = []
    for group in groups.values():
        keyed = [g for g in group if "[" in g.location]
        deduped.append(keyed[0] if keyed else group[0])

    return ParsedApp(
        files=[str(f) for f in xml_files],
        flows=flows,
        connector_calls=connector_calls,
        secrets=deduped,
        dataweave_issues=dataweave_issues,
        edges=edges,
        unresolved_configs=unresolved_configs,
    )


# --------------------------------------------------------------------------
# Summaries for downstream consumers (graph.py / reviewer.py)
# --------------------------------------------------------------------------


def summarize(parsed: ParsedApp) -> dict:
    """A JSON-serializable structural summary, used both for architecture.md
    and as grounding data in the LLM prompt.
    """
    return {
        "files": parsed.files,
        "flows": [
            {
                "name": info.name,
                "kind": info.kind,
                "file": info.file,
                "has_error_handler": info.has_error_handler,
                "is_entry_point": info.is_entry_point,
                "entry_point_type": info.entry_point_type,
                "flow_refs": info.flow_refs,
                "connector_calls": [
                    {
                        "operation": c.operation,
                        "config_ref": c.config_ref,
                        "has_reconnection": c.has_reconnection,
                    }
                    for c in info.connector_calls
                ],
            }
            for info in parsed.flows.values()
        ],
        "secrets": [
            {
                "file": s.file,
                "flow_name": s.flow_name,
                "location": s.location,
                "category": s.category,
                "value_preview": s.value_preview,
            }
            for s in parsed.secrets
        ],
        "dataweave_issues": [
            {
                "file": d.file,
                "flow_name": d.flow_name,
                "element": d.element,
                "collection": d.collection,
                "pass_count": d.pass_count,
                "kind": d.kind,
            }
            for d in parsed.dataweave_issues
        ],
        "edges": [{"from": a, "to": b} for a, b in parsed.edges],
        "unresolved_configs": parsed.unresolved_configs,
    }


def raw_excerpts(parsed: ParsedApp) -> Dict[str, str]:
    """Raw file contents keyed by file path, for feeding to the LLM alongside
    the structural summary.
    """
    return {f: Path(f).read_text(encoding="utf-8") for f in parsed.files}
