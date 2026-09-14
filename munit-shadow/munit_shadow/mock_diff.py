"""Diff a flow's currently-detected outbound calls against the mocks already
present in an existing MUnit test file.

This module only reads (via ``xml.etree.ElementTree``, read-only parsing --
never used to rewrite the file) to recover the set of already-mocked calls.
The actual file mutation happens in ``splice.py`` as a text-level insertion,
never a tree re-serialization -- see that module's docstring for why.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Set, Tuple
import xml.etree.ElementTree as ET

from .xml_parser import DOC_NS, MUNIT_TOOLS_NS, OutboundCall

STALE_MARKER_TEXT = (
    "munit-shadow: this outbound call was not found in the flow as of the "
    "last sync; consider removing this mock if it's no longer needed"
)
# Note: deliberately uses "; consider" rather than "-- consider" -- XML
# forbids a literal "--" inside a comment body, and this text is always
# written into a real <!-- ... --> comment (see splice.py). The CLI's
# human-readable status line (report.py) is free to use "--" since it is
# plain terminal text, not XML.


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


@dataclass
class ExistingMock:
    """One <munit-tools:mock-when> already present in a test file."""

    operation: str
    config_ref: Optional[str]
    already_flagged_stale: bool  # a munit-shadow stale-marker comment already precedes it

    def identity_key(self) -> Tuple[str, Optional[str]]:
        return (self.operation, self.config_ref)


def _config_ref_from_with_attributes(mock_when: ET.Element) -> Optional[str]:
    with_attrs = mock_when.find(_q(MUNIT_TOOLS_NS, "with-attributes"))
    if with_attrs is None:
        return None
    for attr in with_attrs.findall(_q(MUNIT_TOOLS_NS, "with-attribute")):
        if attr.get("attributeName") != "config-ref":
            continue
        where = (attr.get("whereValue") or "").strip()
        # Generated shape: #['configRefValue']
        if where.startswith("#['") and where.endswith("']"):
            return where[3:-2]
        if where.startswith('#["') and where.endswith('"]'):
            return where[3:-2]
    return None


def find_existing_mocks(test_file_text: str) -> List[ExistingMock]:
    """Parse ``test_file_text`` (the full text of an existing MUnit test
    file) and return every <munit-tools:mock-when> block found, anywhere in
    the document (regardless of which <munit:test> it lives in -- one flow
    file maps to one test file that may contain several <munit:test>s, and
    munit-shadow treats "is this call already mocked anywhere in this file"
    as the question that matters for the new-vs-stale diff).

    Raises ``xml.etree.ElementTree.ParseError`` if the text isn't well-formed
    XML -- callers should treat that as "can't safely diff this file".
    """
    # Plain ET.fromstring() silently DROPS comments -- which would make it
    # impossible to ever see a previously-applied stale marker. Use a
    # TreeBuilder configured to keep them (available since Python 3.8).
    parser = ET.XMLParser(target=ET.TreeBuilder(insert_comments=True))
    parser.feed(test_file_text)
    root = parser.close()
    mocks: List[ExistingMock] = []

    # Walk in document order so we can look at each mock-when's *preceding
    # sibling* to detect an already-applied stale marker comment.
    for parent in root.iter():
        children = list(parent)
        for idx, child in enumerate(children):
            if child.tag != _q(MUNIT_TOOLS_NS, "mock-when"):
                continue
            processor = child.get("processor")
            if not processor:
                continue
            config_ref = _config_ref_from_with_attributes(child)

            already_flagged = False
            if idx > 0:
                prev = children[idx - 1]
                if not isinstance(prev.tag, str):  # ET.Comment nodes have a callable .tag, not a str
                    comment_text = (prev.text or "").strip()
                    if STALE_MARKER_TEXT in comment_text:
                        already_flagged = True

            mocks.append(
                ExistingMock(
                    operation=processor,
                    config_ref=config_ref,
                    already_flagged_stale=already_flagged,
                )
            )
    return mocks


@dataclass
class DiffResult:
    new_calls: List[OutboundCall]
    stale_mocks: List[ExistingMock]  # mocked, no longer in the flow, not yet flagged
    already_flagged_stale: List[ExistingMock]  # mocked, no longer in the flow, already flagged
    unchanged: List[OutboundCall]  # currently in flow AND already mocked


def diff_calls(
    current_calls: List[OutboundCall],
    existing_mocks: List[ExistingMock],
) -> DiffResult:
    """Compute new / stale / unchanged calls by comparing the flow's current
    distinct outbound calls (``current_calls``, already deduped by
    ``xml_parser.dedupe_calls``) against ``existing_mocks`` parsed from the
    test file.
    """
    existing_by_key = {}
    for m in existing_mocks:
        existing_by_key.setdefault(m.identity_key(), m)

    current_keys: Set[Tuple[str, Optional[str]]] = set()
    new_calls: List[OutboundCall] = []
    unchanged: List[OutboundCall] = []

    for call in current_calls:
        key = call.identity_key()
        current_keys.add(key)
        if key in existing_by_key:
            unchanged.append(call)
        else:
            new_calls.append(call)

    stale_mocks: List[ExistingMock] = []
    already_flagged_stale: List[ExistingMock] = []
    for m in existing_mocks:
        if m.identity_key() in current_keys:
            continue
        if m.already_flagged_stale:
            already_flagged_stale.append(m)
        else:
            stale_mocks.append(m)

    return DiffResult(
        new_calls=new_calls,
        stale_mocks=stale_mocks,
        already_flagged_stale=already_flagged_stale,
        unchanged=unchanged,
    )
