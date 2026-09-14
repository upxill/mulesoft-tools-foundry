"""``gen-tests`` command: read a real ``.dwl`` script from disk and ask
Claude to produce a MUnit-style XML test suite covering meaningful edge
cases for THAT script (nulls, empty arrays/objects, type mismatches,
boundary values).

The generated XML is validated for well-formedness with
``xml.etree.ElementTree`` -- a real, mechanical check that is run even
though no Mule/MUnit runtime is available in this environment to actually
*execute* the generated tests. That limitation is surfaced in the returned
report and restated by the CLI; MUnit execution itself is never attempted
or claimed.

Note on the module name: this file is intentionally named
``test_generator.py`` (matching the project's documented layout) even
though it lives in the ``dataweave_copilot`` package, not ``tests/`` --
pytest is scoped to ``tests/`` via ``[tool.pytest.ini_options]`` in
``pyproject.toml`` so this module is never mistaken for a pytest test file.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

DEFAULT_MODEL = "claude-opus-5"

EDGE_CASE_CATEGORIES = [
    "null values",
    "empty arrays/objects",
    "type mismatches",
    "boundary values",
]

_MUNIT_NS = "http://www.mulesoft.org/schema/mule/munit"
NS = {"munit": _MUNIT_NS}

SYSTEM_PROMPT = (
    "You are an expert MuleSoft test engineer. Given the real content of a "
    "DataWeave 2.0 script, generate an MUnit-style XML test suite covering "
    "meaningful edge cases for THAT SPECIFIC script: null values, empty "
    "arrays/objects, type mismatches, and boundary values, wherever they "
    "are relevant to what the script actually does. Use this XML shape:\n\n"
    "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
    "<mule xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"\n"
    "      xmlns:munit=\"http://www.mulesoft.org/schema/mule/munit\"\n"
    "      xmlns:munit-tools=\"http://www.mulesoft.org/schema/mule/munit-tools\"\n"
    "      xmlns=\"http://www.mulesoft.org/schema/mule/core\">\n"
    "  <munit:config name=\"...\" />\n"
    "  <munit:test name=\"...\" description=\"...\">\n"
    "    <munit:behavior> ... </munit:behavior>\n"
    "    <munit:execution> ... </munit:execution>\n"
    "    <munit:validation>\n"
    "      <munit-tools:assert-that expression=\"...\" is=\"#[...]\" />\n"
    "    </munit:validation>\n"
    "  </munit:test>\n"
    "  <!-- one munit:test per edge case, with descriptive name/description -->\n"
    "</mule>\n\n"
    "Respond with ONLY the raw XML -- no markdown fences, no prose."
)


class TestGenReport(BaseModel):
    xml: str
    well_formed: bool
    parse_error: str | None = None
    test_count: int = 0
    covered_categories: list[str] = Field(default_factory=list)
    attempts: int = 1


def extract_xml(raw_text: str) -> str:
    text = raw_text.strip()
    m = re.match(r"^```(?:xml)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def check_well_formed(xml_text: str) -> tuple[bool, str | None]:
    """The real, meaningful mechanical check available without a Mule
    runtime: does this parse as well-formed XML at all?"""
    try:
        ET.fromstring(xml_text)
        return True, None
    except ET.ParseError as exc:
        return False, str(exc)


def analyze_coverage(xml_text: str) -> tuple[int, list[str]]:
    """Structural coverage heuristic: count munit:test elements, and flag
    which of the four requested edge-case categories their combined
    name/description text seems to mention. This is a keyword heuristic,
    not proof the tests are correct or that they'd pass under a real Mule
    runtime -- disclosed as such in the README and CLI output."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return 0, []
    tests = root.findall(f".//{{{_MUNIT_NS}}}test")
    combined_text = " ".join(f"{t.get('name', '')} {t.get('description', '')}" for t in tests).lower()
    # MUnit test names are conventionally snake_case; normalize underscores
    # to spaces so multi-word keyword phrases (e.g. "type mismatch") match
    # across a name like "type_mismatch_quantity_test".
    combined_text = combined_text.replace("_", " ").replace("-", " ")
    keyword_map = {
        "null values": ["null"],
        "empty arrays/objects": ["empty"],
        "type mismatches": ["type mismatch", "wrong type", "invalid type", "type coercion"],
        "boundary values": ["boundary", "edge case", "min ", "max ", "minimum", "maximum", "limit"],
    }
    covered = [cat for cat, keywords in keyword_map.items() if any(k in combined_text for k in keywords)]
    return len(tests), covered


def build_prompt(script_content: str, filename: str) -> str:
    return (
        f"Here is the real content of the DataWeave script `{filename}`, "
        f"read directly from disk:\n\n```\n{script_content}\n```\n\n"
        "Generate the MUnit XML test suite now."
    )


def generate_tests(client: Any, script_path: str, model: str = DEFAULT_MODEL) -> TestGenReport:
    content = Path(script_path).read_text()
    prompt = build_prompt(content, Path(script_path).name)

    xml_text = ""
    parse_error: str | None = None
    for attempt in (1, 2):
        response = client.messages.create(
            model=model,
            max_tokens=4096,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": prompt}],
        )
        raw_text = "".join(b.text for b in response.content if b.type == "text")
        xml_text = extract_xml(raw_text)
        well_formed, parse_error = check_well_formed(xml_text)
        if well_formed:
            test_count, covered = analyze_coverage(xml_text)
            return TestGenReport(
                xml=xml_text, well_formed=True, test_count=test_count,
                covered_categories=covered, attempts=attempt,
            )
        prompt = (
            prompt + "\n\nYour previous response was not well-formed XML "
            f"(parser error: {parse_error}). Return ONLY corrected, well-formed XML."
        )

    return TestGenReport(xml=xml_text, well_formed=False, parse_error=parse_error, attempts=2)
