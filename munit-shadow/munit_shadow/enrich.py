"""Optional LLM enrichment of newly-added mock ``then-return`` payloads.

This is the ONLY module in munit-shadow that ever imports ``anthropic`` or
makes a network call, and it only runs when ``--enrich`` is passed. The core
incremental sync engine (``splice.py``, ``mock_diff.py``, ``xml_parser.py``)
works correctly, and is fully covered by ``tests/``, with zero API key and
zero network access.

When a NEW outbound call is detected (one that needs a brand new
``<munit-tools:mock-when>`` block), ``--enrich`` asks Claude, grounded in the
flow's real surrounding XML, to suggest a more realistic ``then-return``
payload than the generic ``{mocked: true, ...}`` placeholder
(``scaffold.placeholder_payload_expr``). It must never change anything a
developer already wrote -- it only ever supplies the *content* of a
placeholder for a call that is about to be newly inserted anyway.

Failure handling: any exception (missing package, no credentials, rate
limit, malformed JSON) is caught per-call and falls back to the
deterministic placeholder -- ``--enrich`` must never crash a sync/watch run.
"""

from __future__ import annotations

import json
import re
from typing import Dict, List, Optional

from .xml_parser import FlowInfo, OutboundCall

DEFAULT_MODEL = "claude-opus-5"

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)


def _build_prompt(flow: FlowInfo, flow_xml: str, calls: List[OutboundCall]) -> str:
    call_lines = []
    for c in calls:
        ref = f" config-ref={c.config_ref!r}" if c.config_ref else ""
        call_lines.append(f"  - operation={c.operation!r}{ref}")
    calls_desc = "\n".join(call_lines) if call_lines else "  (none)"

    return f"""You are helping fill in newly-added MUnit mock-when blocks for a Mule 4
flow, as part of an ambient tool that keeps an existing, hand-written MUnit
test file incrementally in sync with the flow's real XML. Only the payload
CONTENT you suggest below will be used -- the surrounding test structure and
every other existing assertion/mock in the file is untouched.

Here is the real XML of the flow named "{flow.name}":

```xml
{flow_xml}
```

These outbound connector calls were just newly detected in this flow (they
did not exist in it before, so no mock exists for them yet) and each needs a
``then-return`` mock payload:
{calls_desc}

Respond with ONLY a single JSON object (no prose, no markdown fences) of
this exact shape:

{{
  "mocks": [
    {{
      "operation": "<the operation string exactly as given above, e.g. 'http:request'>",
      "config_ref": "<the config-ref exactly as given above, or null if none>",
      "payload_expression": "<a Mule DataWeave inline expression string starting with #[ and ending with ], producing a realistic mock JSON response for this specific call, based on what the flow does with its result>"
    }}
  ]
}}

Keep every DataWeave expression syntactically valid Mule DataWeave 2.0. Base
the payload shape on what the XML actually does with the data (field names
it reads/writes, choices it makes), not a generic guess.
"""


def enrich_new_call_payloads(
    flow: FlowInfo,
    flow_xml: str,
    new_calls: List[OutboundCall],
    model: Optional[str] = None,
) -> Dict[tuple, str]:
    """Returns a ``{call.identity_key(): payload_expression}`` map for as
    many of ``new_calls`` as enrichment succeeds for. Any call not present
    in the returned dict should fall back to
    ``scaffold.placeholder_payload_expr`` -- this function never raises;
    all failures are swallowed and simply omitted from the result.
    """
    if not new_calls:
        return {}

    try:
        import anthropic
    except ImportError:
        return {}

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model or DEFAULT_MODEL,
            max_tokens=4000,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": _build_prompt(flow, flow_xml, new_calls)}],
        )
        text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
        raw_text = "".join(text_parts)
        match = _JSON_BLOCK_RE.search(raw_text)
        if not match:
            return {}
        data = json.loads(match.group(0))
    except Exception:  # noqa: BLE001 - any failure at all falls back to placeholders
        return {}

    results: Dict[tuple, str] = {}
    for m in data.get("mocks", []):
        if not isinstance(m, dict):
            continue
        op = m.get("operation")
        ref = m.get("config_ref")
        expr = m.get("payload_expression")
        if isinstance(op, str) and isinstance(expr, str) and expr.strip().startswith("#["):
            results[(op, ref)] = expr.strip()
    return results
