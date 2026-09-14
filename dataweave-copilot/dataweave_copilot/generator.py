"""``generate`` command: turn a plain-English spec (plus optional sample
input/output files) into a real DataWeave 2.0 script.

Verification strategy:

- If a ``--input`` sample was given and real DataWeave execution is
  available (see ``dw_runtime.py``, Option A), the generated script is
  actually run against it with the downloaded native DataWeave CLI. If a
  ``--output`` sample was also given, the real output is compared to it.
  On failure/mismatch, the loop regenerates with the real error or diff
  fed back into a fresh prompt, up to ``max_attempts`` tries.
- Otherwise, only the static structural validator (``dw_validator.py``)
  is run, and the report says so plainly -- no execution is claimed.

This module builds prompts as plain string assembly (``build_prompt``) so
that logic is unit-testable without any network/API calls.
"""

from __future__ import annotations

import difflib
import json
import re
import tempfile
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from . import dw_runtime, dw_validator

DEFAULT_MODEL = "claude-opus-5"
MAX_ATTEMPTS_DEFAULT = 3

SYSTEM_PROMPT = (
    "You are an expert MuleSoft DataWeave 2.0 engineer. You write correct, "
    "idiomatic DataWeave 2.0 (%dw 2.0) transformation scripts. Respond with "
    "ONLY the DataWeave script text -- no markdown code fences, no prose, "
    "no explanation before or after the code."
)


def _detect_format(path: Path) -> str:
    return {".json": "json", ".csv": "csv", ".xml": "xml"}.get(path.suffix.lower(), "json")


def read_sample(path: str | None) -> tuple[str, str] | None:
    """Read a sample file's raw text plus its detected format. Pure I/O
    helper, no API calls."""
    if not path:
        return None
    p = Path(path)
    return p.read_text(), _detect_format(p)


def build_prompt(
    spec: str,
    input_sample: tuple[str, str] | None = None,
    output_sample: tuple[str, str] | None = None,
    feedback: str | None = None,
) -> str:
    """Assemble the user-turn prompt text. Pure string logic -- no I/O, no
    network -- so it can be unit-tested offline."""
    parts = [
        "Write a DataWeave 2.0 (%dw 2.0) transformation script for this spec:",
        "",
        f"SPEC: {spec.strip()}",
    ]
    if input_sample:
        content, fmt = input_sample
        parts += ["", f"SAMPLE INPUT (format: {fmt}):", "```", content.strip(), "```"]
    if output_sample:
        content, fmt = output_sample
        parts += ["", f"EXPECTED SAMPLE OUTPUT (format: {fmt}):", "```", content.strip(), "```"]
    parts += [
        "",
        "Requirements:",
        "- Start with a '%dw 2.0' header line.",
        "- Include an explicit 'output <mimeType>' directive.",
        "- Reference the input payload as `payload`.",
        "- Return ONLY the raw DataWeave script text, nothing else.",
    ]
    if feedback:
        parts += ["", "IMPORTANT: your previous attempt was rejected. Fix it and try again.", feedback]
    return "\n".join(parts)


def extract_script(raw_text: str) -> str:
    """Strip markdown code fences if the model wrapped the script in them
    despite instructions not to."""
    text = raw_text.strip()
    m = re.match(r"^```(?:dataweave|dwl|dw)?\s*\n(.*)\n```\s*$", text, re.DOTALL)
    return m.group(1).strip() if m else text


def diff_json(expected: Any, actual: Any) -> str:
    e = json.dumps(expected, indent=2, sort_keys=True)
    a = json.dumps(actual, indent=2, sort_keys=True)
    if e == a:
        return ""
    return "\n".join(difflib.unified_diff(e.splitlines(), a.splitlines(), lineterm="", fromfile="expected", tofile="actual"))


class AttemptRecord(BaseModel):
    attempt: int
    script: str
    static_valid: bool
    static_errors: list[str] = Field(default_factory=list)
    executed: bool = False
    execution_success: bool | None = None
    execution_stdout: str = ""
    execution_stderr: str = ""
    output_matched: bool | None = None
    diff: str | None = None


class GenerateReport(BaseModel):
    final_script: str
    attempts: list[AttemptRecord] = Field(default_factory=list)
    verification_mode: str = "static"  # "real-execution" or "static"
    succeeded: bool = False

    def summary(self) -> str:
        lines = [f"Verification mode: {self.verification_mode}", f"Attempts made: {len(self.attempts)}"]
        for a in self.attempts:
            lines.append(
                f"  attempt {a.attempt}: static_valid={a.static_valid} "
                f"executed={a.executed} execution_success={a.execution_success} "
                f"output_matched={a.output_matched}"
            )
        lines.append(f"Result: {'PASSED verification' if self.succeeded else 'did NOT fully verify'}")
        return "\n".join(lines)


def _call_model(client: Any, model: str, prompt: str) -> str:
    response = client.messages.create(
        model=model,
        max_tokens=4096,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = "".join(b.text for b in response.content if b.type == "text")
    return extract_script(text)


def generate(
    client: Any,
    spec: str,
    input_path: str | None = None,
    output_path: str | None = None,
    model: str = DEFAULT_MODEL,
    max_attempts: int = MAX_ATTEMPTS_DEFAULT,
) -> GenerateReport:
    input_sample = read_sample(input_path)
    output_sample = read_sample(output_path)

    expected_output: Any = None
    if output_path and _detect_format(Path(output_path)) == "json":
        expected_output = json.loads(Path(output_path).read_text())

    can_execute = bool(input_path) and dw_runtime.is_available(download=True)
    verification_mode = "real-execution" if can_execute else "static"

    report = GenerateReport(final_script="", verification_mode=verification_mode)
    feedback: str | None = None

    for attempt_num in range(1, max_attempts + 1):
        prompt = build_prompt(spec, input_sample, output_sample, feedback)
        script = _call_model(client, model, prompt)

        static_result = dw_validator.validate(script)
        record = AttemptRecord(
            attempt=attempt_num,
            script=script,
            static_valid=static_result.valid,
            static_errors=static_result.errors,
        )

        if not static_result.valid:
            feedback = (
                "Static validation failed with:\n" + "\n".join(static_result.errors)
                + "\n\nHere is the script you produced:\n" + script
            )
            report.attempts.append(record)
            continue

        if not can_execute:
            report.attempts.append(record)
            report.final_script = script
            report.succeeded = True
            break

        with tempfile.TemporaryDirectory() as tmp:
            script_file = Path(tmp) / "script.dwl"
            script_file.write_text(script)
            exec_result = dw_runtime.run_script(script_file, inputs={"payload": input_path})

        record.executed = True
        record.execution_success = exec_result.success
        record.execution_stdout = exec_result.stdout
        record.execution_stderr = exec_result.stderr

        if not exec_result.success:
            feedback = (
                "The script failed to execute against the real sample input. "
                f"Real DataWeave engine error:\n{exec_result.stderr}\n\n"
                f"Here is the script you produced:\n{script}"
            )
            report.attempts.append(record)
            continue

        if expected_output is None:
            report.attempts.append(record)
            report.final_script = script
            report.succeeded = True
            break

        try:
            actual_output = json.loads(exec_result.stdout)
        except json.JSONDecodeError:
            actual_output = None

        if actual_output == expected_output:
            record.output_matched = True
            report.attempts.append(record)
            report.final_script = script
            report.succeeded = True
            break

        record.output_matched = False
        record.diff = diff_json(expected_output, actual_output if actual_output is not None else {})
        feedback = (
            "The script ran successfully but its real output did not match the "
            f"expected sample output. Diff (expected vs actual):\n{record.diff}\n\n"
            f"Here is the script you produced:\n{script}"
        )
        report.attempts.append(record)

    if not report.final_script and report.attempts:
        report.final_script = report.attempts[-1].script

    return report
