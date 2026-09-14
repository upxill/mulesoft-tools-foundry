"""Offline tests for generator.py's prompt-building and script-extraction
logic. No API/network calls -- these test pure string assembly."""

from dataweave_copilot import generator


def test_build_prompt_includes_spec():
    prompt = generator.build_prompt("Convert Celsius to Fahrenheit")
    assert "Convert Celsius to Fahrenheit" in prompt
    assert "%dw 2.0" in prompt


def test_build_prompt_includes_input_sample():
    prompt = generator.build_prompt(
        "Sum an array",
        input_sample=('{"values": [1, 2, 3]}', "json"),
    )
    assert "SAMPLE INPUT" in prompt
    assert '{"values": [1, 2, 3]}' in prompt
    assert "format: json" in prompt


def test_build_prompt_includes_output_sample():
    prompt = generator.build_prompt(
        "Sum an array",
        output_sample=('{"total": 6}', "json"),
    )
    assert "EXPECTED SAMPLE OUTPUT" in prompt
    assert '{"total": 6}' in prompt


def test_build_prompt_includes_both_samples_together():
    prompt = generator.build_prompt(
        "Sum an array",
        input_sample=('{"values": [1, 2, 3]}', "json"),
        output_sample=('{"total": 6}', "json"),
    )
    assert prompt.index("SAMPLE INPUT") < prompt.index("EXPECTED SAMPLE OUTPUT")


def test_build_prompt_without_samples_omits_sample_sections():
    prompt = generator.build_prompt("Sum an array")
    assert "SAMPLE INPUT" not in prompt
    assert "EXPECTED SAMPLE OUTPUT" not in prompt


def test_build_prompt_includes_feedback_when_present():
    prompt = generator.build_prompt("Sum an array", feedback="Missing output directive.")
    assert "Missing output directive." in prompt
    assert "previous attempt was rejected" in prompt


def test_build_prompt_omits_feedback_section_on_first_attempt():
    prompt = generator.build_prompt("Sum an array")
    assert "previous attempt was rejected" not in prompt


def test_extract_script_strips_dataweave_fence():
    raw = "```dataweave\n%dw 2.0\noutput application/json\n---\npayload\n```"
    assert generator.extract_script(raw) == "%dw 2.0\noutput application/json\n---\npayload"


def test_extract_script_strips_plain_fence():
    raw = "```\n%dw 2.0\noutput application/json\n---\npayload\n```"
    assert generator.extract_script(raw) == "%dw 2.0\noutput application/json\n---\npayload"


def test_extract_script_passes_through_unfenced_text():
    raw = "%dw 2.0\noutput application/json\n---\npayload"
    assert generator.extract_script(raw) == raw


def test_diff_json_empty_when_equal():
    assert generator.diff_json({"a": 1}, {"a": 1}) == ""


def test_diff_json_nonempty_when_different():
    diff = generator.diff_json({"a": 1}, {"a": 2})
    assert diff != ""
    assert "-  \"a\": 1" in diff or '"a": 1' in diff


def test_read_sample_detects_json_format(tmp_path):
    p = tmp_path / "in.json"
    p.write_text('{"x": 1}')
    content, fmt = generator.read_sample(str(p))
    assert fmt == "json"
    assert content == '{"x": 1}'


def test_read_sample_detects_csv_format(tmp_path):
    p = tmp_path / "in.csv"
    p.write_text("a,b\n1,2\n")
    content, fmt = generator.read_sample(str(p))
    assert fmt == "csv"


def test_read_sample_returns_none_when_no_path_given():
    assert generator.read_sample(None) is None
