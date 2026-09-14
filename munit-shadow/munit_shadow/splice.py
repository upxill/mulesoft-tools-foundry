"""The text-level surgical XML insertion engine -- the trust-critical core
of munit-shadow.

Why this exists (do not "simplify" this into ElementTree round-tripping):
parsing an existing MUnit test file with ``xml.etree.ElementTree`` and then
calling ``ElementTree.write()`` (or ``ET.tostring()``) to save it back would
re-serialize the WHOLE document -- reordering/reformatting attributes,
collapsing or renumbering whitespace, and, in some corner cases, dropping
things ``ElementTree`` doesn't round-trip perfectly. That would silently
destroy a developer's own hand-written formatting, comments, and assertions
the very first time munit-shadow touched their file -- the opposite of the
trust guarantee this whole tool exists to provide.

Instead:
  1. We parse the existing file with ``xml.parsers.expat`` (stdlib) in a
     mode that reports the exact BYTE offset of every element/comment as it
     is encountered (``CurrentByteIndex``), operating on the file's raw
     bytes throughout (never decoded-then-reencoded text) so offsets are
     unambiguous. This never mutates anything -- it only builds an index of
     "where things are".
  2. New content (a new ``<munit-tools:mock-when>`` block, a stale-marker
     comment, or an entirely new ``<munit:test>`` block) is rendered as
     plain text lines by ``scaffold.py``.
  3. That new text is spliced in as a raw byte insertion at a computed
     offset -- always at the START of the line containing the relevant
     anchor (e.g. the line with an existing ``</munit:behavior>``, or the
     line with an existing ``<munit-tools:mock-when>`` that's gone stale).
     Every byte before that offset, and every byte from that offset onward
     in the ORIGINAL file, is preserved character-for-character; nothing is
     ever reformatted, reordered, or deleted.
  4. The RESULT is re-parsed with ``ET.fromstring`` to confirm it is still
     well-formed XML. If it isn't, ``SpliceError`` is raised and the caller
     must not write the file -- fail loudly, never write corrupt/partial
     output.

Known, documented limitation: this indexer assumes the file uses the
conventional ``munit``/``munit-tools`` XML prefixes (as used throughout
MuleSoft's own documentation and examples) rather than resolving namespace
URIs, and it does not specially handle a self-closing, empty
``<munit:behavior/>`` (real hand-written files always write behavior with
actual mock-when children when they use it at all). See README.md
Limitations.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import xml.parsers.expat as expat
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from . import scaffold
from .mock_diff import STALE_MARKER_TEXT, DiffResult, diff_calls
from .xml_parser import FlowInfo, OutboundCall, dedupe_calls

INDENT_UNIT = "    "


class SpliceError(RuntimeError):
    """Raised when the result of a splice would not be well-formed XML, or
    when the existing file's structure can't be safely located. The caller
    must not write the file when this is raised.
    """


# --------------------------------------------------------------------------
# Index: where things are in the existing file (read-only)
# --------------------------------------------------------------------------


@dataclass
class MockLocation:
    operation: str
    config_ref: Optional[str]
    start_offset: int
    comment_offset: Optional[int]  # offset of a pre-existing munit-shadow stale marker, if any
    test_name: Optional[str]

    def identity_key(self):
        return (self.operation, self.config_ref)

    @property
    def already_flagged_stale(self) -> bool:
        return self.comment_offset is not None


@dataclass
class TestLocation:
    name: str
    first_child_offset: Optional[int] = None
    behavior_close_offset: Optional[int] = None
    has_behavior: bool = False


@dataclass
class FileIndex:
    mocks: List[MockLocation] = field(default_factory=list)
    tests: Dict[str, TestLocation] = field(default_factory=dict)
    root_tag: Optional[str] = None
    root_close_offset: Optional[int] = None


class _Frame:
    __slots__ = (
        "tag",
        "start_offset",
        "first_child_offset",
        "is_test",
        "test_name",
        "is_behavior",
        "behavior_close_offset",
        "has_behavior",
        "processor",
        "config_ref",
        "comment_offset",
    )

    def __init__(self, tag: str, start_offset: int):
        self.tag = tag
        self.start_offset = start_offset
        self.first_child_offset: Optional[int] = None
        self.is_test = tag == "munit:test"
        self.test_name: Optional[str] = None
        self.is_behavior = tag == "munit:behavior"
        self.behavior_close_offset: Optional[int] = None
        self.has_behavior = False
        self.processor: Optional[str] = None
        self.config_ref: Optional[str] = None
        self.comment_offset: Optional[int] = None


def _parse_config_ref_where_value(where: str) -> Optional[str]:
    where = (where or "").strip()
    if where.startswith("#['") and where.endswith("']"):
        return where[3:-2]
    if where.startswith('#["') and where.endswith('"]'):
        return where[3:-2]
    return None


def build_index(raw_bytes: bytes) -> FileIndex:
    """Build a :class:`FileIndex` for an existing MUnit test file's raw
    bytes. Raises ``xml.parsers.expat.ExpatError`` if the file isn't
    well-formed (callers should treat that as "can't safely sync this
    file" and report it rather than guessing).
    """
    parser = expat.ParserCreate()
    index = FileIndex()
    stack: List[_Frame] = []
    pending_comment: Optional[tuple] = None  # (text, offset)

    def start_element(tag, attrs):
        nonlocal pending_comment
        offset = parser.CurrentByteIndex
        comment_here = pending_comment
        pending_comment = None

        if stack and stack[-1].first_child_offset is None:
            stack[-1].first_child_offset = offset

        frame = _Frame(tag, offset)
        if frame.is_test:
            frame.test_name = attrs.get("name")

        if tag == "munit-tools:mock-when":
            frame.processor = attrs.get("processor")
            if comment_here is not None and STALE_MARKER_TEXT in comment_here[0]:
                frame.comment_offset = comment_here[1]

        if tag == "munit-tools:with-attribute" and attrs.get("attributeName") == "config-ref":
            for ancestor in reversed(stack):
                if ancestor.tag == "munit-tools:mock-when":
                    ancestor.config_ref = _parse_config_ref_where_value(attrs.get("whereValue", ""))
                    break

        if index.root_tag is None:
            index.root_tag = tag

        stack.append(frame)

    def end_element(tag):
        nonlocal pending_comment
        pending_comment = None
        offset = parser.CurrentByteIndex
        frame = stack.pop()

        if frame.is_behavior and stack and stack[-1].is_test:
            stack[-1].behavior_close_offset = offset
            stack[-1].has_behavior = True

        if frame.tag == "munit-tools:mock-when":
            enclosing_test = next((f.test_name for f in reversed(stack) if f.is_test), None)
            index.mocks.append(
                MockLocation(
                    operation=frame.processor or "",
                    config_ref=frame.config_ref,
                    start_offset=frame.start_offset,
                    comment_offset=frame.comment_offset,
                    test_name=enclosing_test,
                )
            )

        if frame.is_test and frame.test_name:
            index.tests[frame.test_name] = TestLocation(
                name=frame.test_name,
                first_child_offset=frame.first_child_offset,
                behavior_close_offset=frame.behavior_close_offset,
                has_behavior=frame.has_behavior,
            )

        if not stack and tag == index.root_tag:
            index.root_close_offset = offset

    def comment_handler(text):
        nonlocal pending_comment
        pending_comment = (text, parser.CurrentByteIndex)

    parser.StartElementHandler = start_element
    parser.EndElementHandler = end_element
    parser.CommentHandler = comment_handler
    parser.buffer_text = True

    parser.Parse(raw_bytes, True)
    return index


# --------------------------------------------------------------------------
# Byte-offset text utilities
# --------------------------------------------------------------------------


def _has_doc_namespace(data: bytes) -> bool:
    """Whether the file already declares the ``doc`` XML namespace prefix
    (``xmlns:doc="http://www.mulesoft.org/schema/mule/documentation"``).
    Generated snippets must never emit a ``doc:name="..."`` attribute into a
    file that doesn't already bind that prefix -- that would itself break
    well-formedness, exactly the kind of damage this tool must never cause.
    A plain substring check on the raw bytes is enough: real Mule files
    always declare namespaces as literal ``xmlns:doc="..."`` attributes on
    the root element.
    """
    return b'xmlns:doc="http://www.mulesoft.org/schema/mule/documentation"' in data


def _line_start(data: bytes, offset: int) -> int:
    idx = data.rfind(b"\n", 0, offset)
    return idx + 1 if idx != -1 else 0


def _line_indent(data: bytes, offset: int) -> str:
    start = _line_start(data, offset)
    end = start
    while end < len(data) and data[end : end + 1] in (b" ", b"\t"):
        end += 1
    return data[start:end].decode("utf-8", errors="replace")


def _render_lines_at_indent(lines: List[str], base_indent: str) -> str:
    return "".join(f"{base_indent}{line}\n" for line in lines)


def apply_insertions(original: bytes, insertions: Dict[int, str]) -> bytes:
    """Insert each ``text`` at the START of the line containing ``offset``,
    for every ``(offset, text)`` pair, without disturbing any other byte.
    Applied in descending offset order so earlier (smaller) offsets are
    never shifted by a later insertion -- see module docstring.
    """
    result = original
    for offset in sorted(insertions.keys(), reverse=True):
        line_start = _line_start(result, offset)
        text_bytes = insertions[offset].encode("utf-8")
        result = result[:line_start] + text_bytes + result[line_start:]
    return result


# --------------------------------------------------------------------------
# High-level sync: flow calls + existing file bytes -> new file bytes
# --------------------------------------------------------------------------


@dataclass
class SyncOutcome:
    new_bytes: bytes
    changed: bool
    new_mocks: List[OutboundCall] = field(default_factory=list)
    newly_flagged_stale: List[MockLocation] = field(default_factory=list)
    still_flagged_stale: List[MockLocation] = field(default_factory=list)
    new_tests_added: List[str] = field(default_factory=list)


def sync_existing_test_file(
    original_bytes: bytes,
    flows: List[FlowInfo],
    then_return_values: Optional[Dict[tuple, str]] = None,
) -> SyncOutcome:
    """Compute (and apply) the minimal in-place edit to ``original_bytes``
    (the current content of an existing MUnit test file) needed to bring it
    in sync with ``flows`` (the flows/sub-flows currently defined in the
    corresponding Mule flow file). Never touches anything else in the file.

    Raises ``SpliceError`` if the existing file isn't well-formed, or if the
    result of the edit would not be well-formed (in which case nothing
    should be written).
    """
    then_return_values = then_return_values or {}

    try:
        index = build_index(original_bytes)
    except expat.ExpatError as exc:
        raise SpliceError(f"existing test file is not well-formed XML, refusing to touch it: {exc}") from exc

    insertions: Dict[int, str] = {}
    outcome = SyncOutcome(new_bytes=original_bytes, changed=False)
    include_doc_name = _has_doc_namespace(original_bytes)

    missing_test_flows: List[FlowInfo] = []

    for flow in flows:
        test_name = f"test-{flow.name}"
        current_calls = dedupe_calls(flow.outbound_calls)

        if test_name not in index.tests:
            if current_calls or True:  # a flow always gets a test, even with zero calls
                missing_test_flows.append(flow)
            continue

        test_loc = index.tests[test_name]
        existing_for_test = [m for m in index.mocks if m.test_name == test_name]
        diff: DiffResult = diff_calls(current_calls, existing_for_test)

        outcome.new_mocks.extend(diff.new_calls)
        outcome.newly_flagged_stale.extend(diff.stale_mocks)
        outcome.still_flagged_stale.extend(diff.already_flagged_stale)

        if diff.new_calls:
            block_lines: List[str] = []
            for call in diff.new_calls:
                block_lines.extend(
                    scaffold.render_mock_when_lines(
                        call, then_return_values.get(call.identity_key()), include_doc_name=include_doc_name
                    )
                )

            if test_loc.has_behavior and test_loc.behavior_close_offset is not None:
                base_indent = _line_indent(original_bytes, test_loc.behavior_close_offset)
                child_indent = base_indent + INDENT_UNIT
                text = _render_lines_at_indent(block_lines, child_indent)
                insertions[test_loc.behavior_close_offset] = insertions.get(test_loc.behavior_close_offset, "") + text
            elif test_loc.first_child_offset is not None:
                base_indent = _line_indent(original_bytes, test_loc.first_child_offset)
                behavior_lines = ["<munit:behavior>"] + [f"{INDENT_UNIT}{l}" for l in block_lines] + ["</munit:behavior>"]
                text = _render_lines_at_indent(behavior_lines, base_indent)
                insertions[test_loc.first_child_offset] = insertions.get(test_loc.first_child_offset, "") + text
            else:
                raise SpliceError(
                    f"cannot locate an insertion point inside <munit:test name=\"{test_name}\"> "
                    "(no behavior section and no child elements found) -- refusing to guess."
                )

        for stale in diff.stale_mocks:
            indent = _line_indent(original_bytes, stale.start_offset)
            text = f"{indent}<!-- {STALE_MARKER_TEXT} -->\n"
            insertions[stale.start_offset] = insertions.get(stale.start_offset, "") + text

    if missing_test_flows:
        if index.root_close_offset is None:
            raise SpliceError("could not locate the document root's closing tag; refusing to guess where to insert a new test.")
        base_indent = _line_indent(original_bytes, index.root_close_offset) + INDENT_UNIT
        text_blocks = []
        for flow in missing_test_flows:
            lines = scaffold.render_new_test_lines(flow, then_return_values, include_doc_name=include_doc_name)
            text_blocks.append(_render_lines_at_indent(lines, base_indent))
            outcome.new_tests_added.append(flow.name)
            outcome.new_mocks.extend(dedupe_calls(flow.outbound_calls))
        combined = "".join(text_blocks)
        insertions[index.root_close_offset] = insertions.get(index.root_close_offset, "") + combined

    if not insertions:
        return outcome

    new_bytes = apply_insertions(original_bytes, insertions)

    try:
        ET.fromstring(new_bytes)
    except ET.ParseError as exc:
        raise SpliceError(
            f"the computed edit would produce invalid XML ({exc}); aborting without writing the file."
        ) from exc

    outcome.new_bytes = new_bytes
    outcome.changed = True
    return outcome
