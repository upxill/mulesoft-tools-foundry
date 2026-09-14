"""LLM-powered findings, grounded in the deterministic parse.

The Anthropic call follows the current (2026) Messages API structured-output
pattern: ``client.messages.parse(..., output_format=ReviewReport)`` on a
Pydantic model, with ``thinking={"type": "adaptive"}``. See README.md for
where this pattern was confirmed.

Every finding the LLM is asked to produce must be grounded in something the
deterministic scan (xml_parser.py) actually found -- missing error handlers,
missing reconnection strategies, secret-shaped literals, and repeated
collection scans in DataWeave. The prompt hands the model that structural
summary *and* the raw XML, and instructs it not to invent issues outside of
what's actually present in the files.
"""

from __future__ import annotations

import json
import os
import re
from typing import Dict, List, Literal, Optional

from pydantic import BaseModel

DEFAULT_MODEL = "claude-opus-5"


def _secret_property_name(location: str) -> str:
    """Best-effort extraction of a sensible property-placeholder name from a
    SecretCandidate.location string, e.g. 'attribute:db:my-sql-connection@password'
    -> 'password', or 'element-text:http:headers[apiKey]' -> 'apiKey'.
    """
    m = re.search(r"\[([^\]]+)\]$", location)
    if m:
        return m.group(1)
    if "@" in location:
        return location.rsplit("@", 1)[-1]
    return "credential"


class Finding(BaseModel):
    severity: Literal["info", "medium", "high"]
    category: str
    file: str
    flow_name: Optional[str] = None
    explanation: str
    recommendation: str


class ReviewReport(BaseModel):
    findings: List[Finding]
    summary: str


REVIEW_PROMPT_TEMPLATE = """\
You are an experienced MuleSoft/Mule 4 solutions architect performing an \
architectural and operational review of a Mule application. You are NOT a \
linter -- do not comment on XML formatting, tag ordering, naming style, or \
anything a schema validator would already catch.

You have been given two things below:

1. A STRUCTURAL SUMMARY, produced by deterministic XML parsing (not by you). \
It lists every flow/sub-flow, whether each has an error-handler anywhere in \
its subtree, every connector operation call and whether its connection has a \
reconnection strategy configured, every flow-ref edge between flows, every \
hardcoded-secret-looking value already found by a regex scan, and every \
DataWeave transform where the same collection is mapped/filtered/reduced \
more than once (a possible repeated-scan inefficiency).

2. The RAW XML of every file in the application.

Your job: produce a structured list of real architectural/operational \
findings. Ground every single finding in something concretely present in \
the structural summary or the raw XML -- cite the actual file and, where \
applicable, the actual flow name. Do NOT invent issues that aren't reflected \
in the data given to you, and do NOT flag things the structural summary \
already shows are fine (e.g. a connector call whose `has_reconnection` is \
`true`, or a secret-shaped value that is actually a `${...}` property \
placeholder -- those have already been excluded from the secrets list).

Focus areas, in priority order:
- Flows that call external systems (HTTP, DB, JMS, etc.) but have no \
error-handler anywhere in their subtree -- explain the operational blast \
radius (e.g. unhandled connectivity/timeout errors surfacing as raw 500s to \
callers, no rollback/compensation).
- Connector calls with `has_reconnection` false or null -- explain what \
happens on a transient network blip without one.
- Every entry in the secrets list -- explain why a literal credential in \
source control is a real risk and what the fix looks like (a \
`${secure::...}` placeholder backed by a secure properties/vault mechanism).
- Every entry in the dataweave_issues list -- explain the performance \
implication of repeatedly scanning the same collection (e.g. O(n^2) \
behavior on large payloads) and suggest a single-pass alternative (e.g. \
`groupBy`, an index built once, `reduce`).
- Any other *architectural* issue you can concretely ground in the raw XML \
(e.g. a flow with a connector call with no config-ref, or a flow-ref that \
targets a name absent from the structural summary's flow list).

If the application looks genuinely clean, say so in `summary` and return few \
or no findings -- do not manufacture high-severity findings on a clean app. \
Minor, honest nitpicks are fine at `info` severity, but never invent a \
`high` or `medium` finding that isn't grounded in the data above.

Assign `severity`:
- `high`: missing error handling or missing reconnection on a call to an \
external system, or any hardcoded secret.
- `medium`: DataWeave inefficiency, or a secondary/less-critical structural \
issue.
- `info`: stylistic or minor observations.

STRUCTURAL SUMMARY (JSON):
%%STRUCTURAL_SUMMARY_JSON%%

RAW XML FILES:
%%RAW_XML_SECTION%%
"""


def build_prompt(structural_summary: dict, raw_excerpts: Dict[str, str]) -> str:
    # Plain token substitution (not str.format) because the prompt text
    # above legitimately contains literal "{" / "}" (e.g. "${...}",
    # "${secure::...}") that would otherwise be misparsed as format fields.
    raw_xml_section = "\n\n".join(
        f"--- {path} ---\n{content}" for path, content in sorted(raw_excerpts.items())
    )
    prompt = REVIEW_PROMPT_TEMPLATE.replace(
        "%%STRUCTURAL_SUMMARY_JSON%%", json.dumps(structural_summary, indent=2)
    )
    prompt = prompt.replace("%%RAW_XML_SECTION%%", raw_xml_section)
    return prompt


def review(
    structural_summary: dict,
    raw_excerpts: Dict[str, str],
    model: Optional[str] = None,
) -> ReviewReport:
    """Call Claude for structured architectural findings.

    Requires the ``anthropic`` package and Anthropic credentials to be
    available in the environment (``anthropic.Anthropic()`` picks these up
    automatically -- no custom key handling here per project convention).
    """
    import anthropic

    model = model or os.environ.get("ANTHROPIC_MODEL", DEFAULT_MODEL)
    client = anthropic.Anthropic()
    review_prompt = build_prompt(structural_summary, raw_excerpts)

    response = client.messages.parse(
        model=model,
        max_tokens=8000,
        thinking={"type": "adaptive"},
        messages=[{"role": "user", "content": review_prompt}],
        output_format=ReviewReport,
    )
    return response.parsed_output


def dry_run_review(structural_summary: dict) -> ReviewReport:
    """A scripted stand-in for the Anthropic call, used when no API
    credentials are available (see README.md's "LLM findings: live vs. dry
    run" section for full disclosure).

    This does NOT call any LLM. It deterministically turns the same
    structural summary that would be sent to Claude into Finding objects,
    with fixed (but structurally-grounded) explanation/recommendation text,
    so the report-rendering pipeline (report.py) can be exercised end to end
    without network access or API credentials. Every finding here is still
    grounded in something the parser actually found -- nothing is invented.
    """
    findings: List[Finding] = []

    for flow in structural_summary["flows"]:
        calls = flow["connector_calls"]
        if calls and not flow["has_error_handler"]:
            findings.append(
                Finding(
                    severity="high",
                    category="missing-error-handler",
                    file=flow["file"],
                    flow_name=flow["name"],
                    explanation=(
                        f"Flow '{flow['name']}' makes {len(calls)} outbound connector "
                        "call(s) but has no <error-handler> anywhere in its subtree "
                        "[DRY RUN: scripted from parsed structure, not from Claude]. "
                        "A connectivity failure, timeout, or 4xx/5xx from any of these "
                        "calls will propagate as an unhandled MuleException, likely "
                        "surfacing as a raw 500 to the flow's own caller with no "
                        "compensation or rollback."
                    ),
                    recommendation=(
                        "Wrap the flow body in an <error-handler> with "
                        "on-error-propagate/on-error-continue routes that match the "
                        "connector's error types (e.g. HTTP:CONNECTIVITY, "
                        "HTTP:TIMEOUT), and return a controlled error response."
                    ),
                )
            )
        for call in calls:
            if not call["has_reconnection"]:
                findings.append(
                    Finding(
                        severity="high",
                        category="missing-reconnection-strategy",
                        file=flow["file"],
                        flow_name=flow["name"],
                        explanation=(
                            f"The '{call['operation']}' call in flow '{flow['name']}' "
                            f"uses config-ref='{call['config_ref']}', whose connection "
                            "has no <reconnection> strategy configured "
                            "[DRY RUN: scripted from parsed structure, not from Claude]. "
                            "A transient network blip to the downstream system will "
                            "fail the call immediately instead of retrying."
                        ),
                        recommendation=(
                            "Add a <reconnection> strategy (e.g. "
                            '<reconnect count="3" frequency="2000"/>) to the '
                            f"connection referenced by '{call['config_ref']}'."
                        ),
                    )
                )

    for secret in structural_summary["secrets"]:
        findings.append(
            Finding(
                severity="high",
                category="hardcoded-secret",
                file=secret["file"],
                flow_name=secret["flow_name"],
                explanation=(
                    f"A literal value at {secret['location']} looks like a hardcoded "
                    f"credential ('{secret['value_preview']}') "
                    "[DRY RUN: scripted from parsed structure, not from Claude]. "
                    "Committing credentials to source control exposes them to anyone "
                    "with repo access and to the full git history even after rotation."
                ),
                recommendation=(
                    "Replace the literal with a property placeholder backed by secure "
                    "properties, e.g. ${secure::"
                    + _secret_property_name(secret["location"])
                    + "}, and rotate the exposed credential."
                ),
            )
        )

    for dw in structural_summary["dataweave_issues"]:
        findings.append(
            Finding(
                severity="medium",
                category="inefficient-dataweave",
                file=dw["file"],
                flow_name=dw["flow_name"],
                explanation=(
                    f"In {dw['element']}, the collection '{dw['collection']}' is "
                    f"traversed {dw['pass_count']} separate times "
                    "[DRY RUN: scripted from parsed structure, not from Claude]. "
                    "Re-scanning the same collection inside a map/filter over itself "
                    "is an O(n^2) pattern that degrades badly as payload size grows."
                ),
                recommendation=(
                    f"Pre-compute a lookup for '{dw['collection']}' once (e.g. via "
                    "groupBy) before the outer map/filter, instead of re-scanning it "
                    "on every iteration."
                ),
            )
        )

    if not findings:
        summary = (
            "[DRY RUN: no Anthropic API call made] Deterministic scan found no "
            "missing error handlers, missing reconnection strategies, hardcoded "
            "secrets, or repeated-scan DataWeave patterns in this application."
        )
    else:
        high = sum(1 for f in findings if f.severity == "high")
        medium = sum(1 for f in findings if f.severity == "medium")
        info = sum(1 for f in findings if f.severity == "info")
        summary = (
            f"[DRY RUN: no Anthropic API call made] Findings synthesized directly "
            f"from the deterministic structural scan: {high} high, {medium} medium, "
            f"{info} info."
        )

    return ReviewReport(findings=findings, summary=summary)
