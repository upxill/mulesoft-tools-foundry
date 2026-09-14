"""Static structural validation for DataWeave 2.0 scripts.

This is deliberately NOT a full DataWeave parser. It's a set of pragmatic,
mechanical structural checks that catch the most common ways an
LLM-generated ``.dwl`` script can be broken, before it is ever executed or
shown to a user:

- missing/malformed ``%dw <version>`` header
- missing ``---`` header/body separator (or header appearing after it)
- missing ``output <mimeType>`` directive
- unbalanced ``()``/``[]``/``{}`` (ignoring brackets inside string
  literals and comments)
- dangling trailing selectors (e.g. a truncated ``foo.`` at the end of a
  line or right before a closing bracket)

It is used unconditionally, regardless of whether real DataWeave execution
(see ``dw_runtime.py``) is available, as a fast first-pass sanity check.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

_DW_HEADER_RE = re.compile(r"^\s*%dw\s+(\d+\.\d+)\s*$", re.MULTILINE)
_OUTPUT_RE = re.compile(r"^\s*output\s+[\w./+*-]+", re.MULTILINE)
_SEPARATOR_RE = re.compile(r"^\s*---\s*$", re.MULTILINE)

_PAIRS = {")": "(", "]": "[", "}": "{"}
_OPENERS = set(_PAIRS.values())
_CLOSERS = set(_PAIRS.keys())


class ValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def __bool__(self) -> bool:
        return self.valid

    def summary(self) -> str:
        lines = ["VALID (static structural checks passed)" if self.valid
                 else "INVALID (static structural checks failed)"]
        lines += [f"  ERROR: {e}" for e in self.errors]
        lines += [f"  WARNING: {w}" for w in self.warnings]
        return "\n".join(lines)


def _strip_strings_and_comments(source: str) -> str:
    """Blank out string-literal and comment content so bracket-balance
    checks aren't confused by brackets that appear inside them."""
    result: list[str] = []
    i = 0
    n = len(source)
    while i < n:
        ch = source[i]
        if ch == "/" and i + 1 < n and source[i + 1] == "/":
            while i < n and source[i] != "\n":
                i += 1
            continue
        if ch == "/" and i + 1 < n and source[i + 1] == "*":
            i += 2
            while i + 1 < n and not (source[i] == "*" and source[i + 1] == "/"):
                i += 1
            i += 2
            continue
        if ch in ("'", '"'):
            quote = ch
            result.append(" ")
            i += 1
            while i < n and source[i] != quote:
                if source[i] == "\\" and i + 1 < n:
                    i += 2
                    continue
                i += 1
            i += 1
            continue
        result.append(ch)
        i += 1
    return "".join(result)


def check_balanced_brackets(cleaned_source: str) -> list[str]:
    errors: list[str] = []
    stack: list[tuple[str, int]] = []
    for idx, ch in enumerate(cleaned_source):
        if ch in _OPENERS:
            stack.append((ch, idx))
        elif ch in _CLOSERS:
            if not stack:
                errors.append(f"Unexpected closing '{ch}' with no matching opener (offset {idx}).")
                continue
            open_ch, _ = stack.pop()
            if _PAIRS[ch] != open_ch:
                errors.append(f"Mismatched bracket: '{open_ch}' closed by '{ch}' (offset {idx}).")
    for open_ch, idx in stack:
        errors.append(f"Unclosed '{open_ch}' (offset {idx}) with no matching closer found.")
    return errors


def check_dangling_selectors(cleaned_source: str) -> list[str]:
    """Flag the unambiguous case of a truncated selector: a lone '.'
    (not '..' and not a decimal point in a number) immediately followed
    by end-of-source or a closing bracket."""
    warnings: list[str] = []
    for m in re.finditer(r"(?<!\.)\.(?!\.)(\s*)(\)|\]|\}|$)", cleaned_source, re.MULTILINE):
        start = m.start()
        if start > 0 and cleaned_source[start - 1].isdigit():
            continue
        warnings.append(f"Possible dangling selector '.' near offset {start}.")
    return warnings


def validate(source: str) -> ValidationResult:
    if not source or not source.strip():
        return ValidationResult(valid=False, errors=["Script is empty."])

    errors: list[str] = []
    warnings: list[str] = []

    header_match = _DW_HEADER_RE.search(source)
    sep_match = _SEPARATOR_RE.search(source)

    if not header_match:
        errors.append("Missing or malformed '%dw <version>' header directive (expected e.g. '%dw 2.0').")
    elif not header_match.group(1).startswith("2."):
        warnings.append(
            f"Header declares DataWeave version {header_match.group(1)}, not the 2.x family this tool targets."
        )

    if not sep_match:
        errors.append("Missing '---' header/body separator.")

    if not _OUTPUT_RE.search(source):
        errors.append("Missing 'output <mimeType>' directive (e.g. 'output application/json').")

    if header_match and sep_match and header_match.start() > sep_match.start():
        errors.append("'%dw' header appears after the '---' separator.")

    cleaned = _strip_strings_and_comments(source)
    errors.extend(check_balanced_brackets(cleaned))
    warnings.extend(check_dangling_selectors(cleaned))

    return ValidationResult(valid=(len(errors) == 0), errors=errors, warnings=warnings)
