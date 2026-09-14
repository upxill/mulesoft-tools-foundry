"""Offline tests for the static DataWeave structural validator. No network,
no API calls -- these run against literal .dwl snippets defined here."""

from dataweave_copilot import dw_validator

VALID_SIMPLE = """\
%dw 2.0
output application/json
---
payload
"""

VALID_WITH_LOGIC = """\
%dw 2.0
output application/json
---
do {
    var activeItems = payload.lineItems filter ($.status != "cancelled")
    ---
    {
        count: sizeOf(activeItems),
        items: activeItems map (item) -> { sku: item.sku }
    }
}
"""

VALID_WITH_STRING_CONTAINING_BRACES = """\
%dw 2.0
output application/json
---
{
    message: "unbalanced { in a string is fine }" ++ " and so is ) this ("
}
"""


def test_valid_simple_script_passes():
    result = dw_validator.validate(VALID_SIMPLE)
    assert result.valid, result.summary()
    assert result.errors == []


def test_valid_script_with_nested_logic_passes():
    result = dw_validator.validate(VALID_WITH_LOGIC)
    assert result.valid, result.summary()


def test_brackets_inside_string_literals_are_ignored():
    result = dw_validator.validate(VALID_WITH_STRING_CONTAINING_BRACES)
    assert result.valid, result.summary()


def test_missing_header_is_rejected():
    script = "output application/json\n---\npayload\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("header" in e.lower() for e in result.errors)


def test_missing_separator_is_rejected():
    script = "%dw 2.0\noutput application/json\npayload\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("separator" in e.lower() for e in result.errors)


def test_missing_output_directive_is_rejected():
    script = "%dw 2.0\n---\npayload\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("output" in e.lower() for e in result.errors)


def test_unbalanced_braces_are_rejected():
    script = "%dw 2.0\noutput application/json\n---\n{ a: payload.x\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("unclosed" in e.lower() for e in result.errors)


def test_mismatched_brackets_are_rejected():
    script = "%dw 2.0\noutput application/json\n---\n{ a: [1, 2) }\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("mismatched" in e.lower() for e in result.errors)


def test_unexpected_closing_bracket_is_rejected():
    script = "%dw 2.0\noutput application/json\n---\npayload) }\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("unexpected closing" in e.lower() for e in result.errors)


def test_header_after_separator_is_rejected():
    script = "---\n%dw 2.0\noutput application/json\npayload\n"
    result = dw_validator.validate(script)
    assert not result.valid
    assert any("after the" in e.lower() for e in result.errors)


def test_empty_script_is_rejected():
    result = dw_validator.validate("")
    assert not result.valid
    assert "Script is empty." in result.errors


def test_non_2x_version_is_a_warning_not_an_error():
    script = "%dw 1.0\noutput application/json\n---\npayload\n"
    result = dw_validator.validate(script)
    assert result.valid
    assert any("2.x" in w for w in result.warnings)


def test_dangling_selector_produces_warning_but_stays_valid_structurally():
    # A trailing '.' right before a closing brace is a real LLM failure
    # mode (truncated selector chain) -- flagged as a warning, since it
    # doesn't break bracket balance and might occasionally be intentional
    # inside odd string handling.
    script = '%dw 2.0\noutput application/json\n---\n{ a: payload.x. }\n'
    result = dw_validator.validate(script)
    assert any("dangling selector" in w.lower() for w in result.warnings)


def test_result_is_truthy_iff_valid():
    assert bool(dw_validator.validate(VALID_SIMPLE)) is True
    assert bool(dw_validator.validate("")) is False
