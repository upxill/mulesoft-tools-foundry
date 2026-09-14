"""Optional LLM enrichment of the deterministic scaffold.

This module is the ONLY place in the project that ever calls the Anthropic
API, and it is only ever invoked when the caller passes ``--enrich`` on the
CLI. Everything in ``xml_parser.py`` and ``scaffold.py`` works, and is
tested, with zero network access and no API key -- see README.md.

When enabled, for each flow this asks Claude to look at the flow's actual
XML and suggest:
  - a more realistic ``then-return`` mock payload for each distinct
    outbound call (instead of the generic ``{mocked: true, ...}`` stub),
  - a more meaningful ``assert-that`` expression/matcher for the test's
    validation block (instead of the generic "payload is not null" stub).

The base scaffold's structure (which calls get mocked, how many
mock-when blocks, the flow-ref/set-event/assert-that skeleton) is never
changed by enrichment -- only the *content* of the placeholder payloads
and the final assertion, and only for flows/calls it's given.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional

from .scaffold import DOC_NS, MUNIT_NS, MUNIT_TOOLS_NS
from .xml_parser import FlowInfo, dedupe_calls

DEFAULT_MODEL = "claude-opus-5"

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _q(ns: str, local: str) -> str:
    return f"{{{ns}}}{local}"


def _build_prompt(flow: FlowInfo, flow_xml: str) -> str:
    calls = dedupe_calls(flow.outbound_calls)
    call_lines = []
    for c in calls:
        ref = f" config-ref={c.config_ref!r}" if c.config_ref else ""
        call_lines.append(f"  - operation={c.operation!r}{ref}")
    calls_desc = "\n".join(call_lines) if call_lines else "  (none)"

    return f"""You are helping fill in a generated MUnit 3 test skeleton for a Mule 4 flow.

Here is the real XML of the flow named "{flow.name}":

```xml
{flow_xml}
```

This flow has the following distinct outbound connector calls, each of
which will be mocked with a munit-tools:mock-when / then-return block:
{calls_desc}

Respond with ONLY a single JSON object (no prose, no markdown fences) of
this exact shape:

{{
  "mocks": [
    {{
      "operation": "<the operation string exactly as given above, e.g. 'http:request'>",
      "config_ref": "<the config-ref exactly as given above, or null if none>",
      "payload_expression": "<a Mule DataWeave inline expression string starting with #[ and ending with ], producing a realistic mock JSON response for this specific call, based on what the flow does with its result>",
      "media_type": "application/json"
    }}
  ],
  "assertion": {{
    "expression": "<a Mule DataWeave inline expression string starting with #[ , evaluated against the flow's final output>",
    "is": "<a MunitTools matcher expression string starting with #[MunitTools:: that is meaningful for this flow's actual output shape, not just notNullValue()>"
  }}
}}

Keep every DataWeave expression syntactically valid Mule DataWeave 2.0.
Base the payload shapes and assertion on what the XML actually does with
the data (field names it reads/writes, choices it makes), not a generic guess.
"""


def enrich_test_suite(
    tree: ET.ElementTree,
    flows: List[FlowInfo],
    flow_xml_by_name: Dict[str, str],
    model: Optional[str] = None,
) -> List[str]:
    """Mutates ``tree`` in place, replacing placeholder payloads/assertions
    with model-suggested ones for each flow. Returns a list of human-readable
    notes about what was (or wasn't) enriched, for the CLI summary.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "--enrich requires the 'anthropic' package. Install it with "
            "`pip install -e '.[enrich]'` (or `.[dev]`)."
        ) from exc

    client = anthropic.Anthropic()
    model = model or DEFAULT_MODEL

    root = tree.getroot()
    notes: List[str] = []

    tests_by_flow = {
        t.get("name"): t for t in root.findall(_q(MUNIT_NS, "test"))
    }

    for flow in flows:
        test_name = f"test-{flow.name}"
        test_elem = tests_by_flow.get(test_name)
        if test_elem is None:
            continue

        flow_xml = flow_xml_by_name.get(flow.name, "")
        prompt = _build_prompt(flow, flow_xml)

        try:
            response = client.messages.create(
                model=model,
                max_tokens=8000,
                thinking={"type": "adaptive"},
                messages=[{"role": "user", "content": prompt}],
            )
            text_parts = [
                block.text for block in response.content if getattr(block, "type", None) == "text"
            ]
            raw_text = "".join(text_parts)
            match = _JSON_BLOCK_RE.search(raw_text)
            if not match:
                raise ValueError(f"no JSON object found in model response: {raw_text[:200]!r}")
            data = json.loads(match.group(0))
        except Exception as exc:  # noqa: BLE001 - surface any failure as a note, don't crash the run
            notes.append(f"  - {flow.name}: enrichment failed ({exc}); kept deterministic placeholders")
            continue

        applied_mocks = _apply_mock_payloads(test_elem, data.get("mocks", []))
        applied_assertion = _apply_assertion(test_elem, data.get("assertion"))
        notes.append(
            f"  - {flow.name}: enriched {applied_mocks} mock payload(s)"
            + (", assertion updated" if applied_assertion else "")
        )

    return notes


def _apply_mock_payloads(test_elem: ET.Element, mocks: list) -> int:
    behavior = test_elem.find(_q(MUNIT_NS, "behavior"))
    if behavior is None:
        return 0

    by_key = {}
    for m in mocks:
        if not isinstance(m, dict) or "operation" not in m:
            continue
        by_key[(m.get("operation"), m.get("config_ref"))] = m

    applied = 0
    for mock_when in behavior.findall(_q(MUNIT_TOOLS_NS, "mock-when")):
        operation = mock_when.get("processor")
        config_ref = None
        with_attrs = mock_when.find(_q(MUNIT_TOOLS_NS, "with-attributes"))
        if with_attrs is not None:
            for attr in with_attrs.findall(_q(MUNIT_TOOLS_NS, "with-attribute")):
                if attr.get("attributeName") == "config-ref":
                    where = attr.get("whereValue", "")
                    inner = where.strip()
                    if inner.startswith("#['") and inner.endswith("']"):
                        config_ref = inner[3:-2]

        suggestion = by_key.get((operation, config_ref))
        if suggestion is None:
            continue
        payload_elem = mock_when.find(f"{_q(MUNIT_TOOLS_NS, 'then-return')}/{_q(MUNIT_TOOLS_NS, 'payload')}")
        expr = suggestion.get("payload_expression")
        if payload_elem is not None and isinstance(expr, str) and expr.strip().startswith("#["):
            payload_elem.set("value", expr.strip())
            if suggestion.get("media_type"):
                payload_elem.set("mediaType", str(suggestion["media_type"]))
            applied += 1
    return applied


def _apply_assertion(test_elem: ET.Element, assertion: Optional[dict]) -> bool:
    if not isinstance(assertion, dict):
        return False
    validation = test_elem.find(_q(MUNIT_NS, "validation"))
    if validation is None:
        return False
    assert_elem = validation.find(_q(MUNIT_TOOLS_NS, "assert-that"))
    if assert_elem is None:
        return False
    expr = assertion.get("expression")
    is_matcher = assertion.get("is")
    if isinstance(expr, str) and expr.strip().startswith("#["):
        assert_elem.set("expression", expr.strip())
    if isinstance(is_matcher, str) and is_matcher.strip().startswith("#[MunitTools::"):
        assert_elem.set("is", is_matcher.strip())
        return True
    return False
