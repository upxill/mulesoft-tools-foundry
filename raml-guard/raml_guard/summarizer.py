"""Optional LLM-powered release-note summarizer.

This is the ONE part of raml-guard that talks to a network/LLM, and it is
never invoked unless the caller explicitly passes `--summarize` to `diff` or
runs the `explain` command. The core `diff` command's exit code (the thing
CI actually gates on) is computed entirely by `diff_engine.py` before this
module is even imported -- see `cli.py`'s `_run_diff`.

Nothing in `raml_parser.py` or `diff_engine.py` imports this module or the
`anthropic` package. If `anthropic` isn't installed at all, `diff` (without
`--summarize`) still works perfectly, with zero errors and zero network
activity.
"""

from __future__ import annotations

import os
from typing import Any, Optional

DEFAULT_MODEL = "claude-opus-5"


class SummarizerError(Exception):
    """Raised when --summarize/explain can't produce a summary (missing package,
    missing credentials, or an API-level failure). Callers must treat this as a
    soft failure -- it must never change `diff`'s pass/fail exit code."""


def _changes_to_dicts(changes: list) -> list:
    out = []
    for c in changes:
        if isinstance(c, dict):
            out.append(c)
        else:
            out.append({"category": c.category, "location": c.location, "kind": c.kind, "message": c.message})
    return out


def _build_prompt(change_dicts: list) -> str:
    breaking = [c for c in change_dicts if c.get("category") == "breaking"]
    non_breaking = [c for c in change_dicts if c.get("category") == "non-breaking"]
    informational = [c for c in change_dicts if c.get("category") == "informational"]

    def fmt(items: list) -> str:
        return "\n".join(f"- {i.get('location')}: {i.get('message')}" for i in items) or "(none)"

    return (
        "You are writing a short release-note paragraph for an internal API "
        "governance report about a RAML spec version bump. Given the following "
        "changes -- already classified deterministically as breaking, "
        "non-breaking, or informational by a separate tool -- write a concise "
        "3-6 sentence plain-English paragraph summarizing what changed. Call "
        "out breaking changes clearly and first. Do not invent any change not "
        "listed below, and do not re-classify a change into a different "
        "severity than given.\n\n"
        f"BREAKING:\n{fmt(breaking)}\n\n"
        f"NON-BREAKING:\n{fmt(non_breaking)}\n\n"
        f"INFORMATIONAL:\n{fmt(informational)}\n"
    )


def _call_claude(change_dicts: list, model: Optional[str]) -> str:
    try:
        import anthropic
    except ImportError as exc:
        raise SummarizerError(
            "the 'anthropic' package is not installed; install raml-guard with the "
            "'llm' extra (pip install -e '.[llm]') to use --summarize/explain"
        ) from exc

    resolved_model = model or os.environ.get("ANTHROPIC_MODEL") or DEFAULT_MODEL
    prompt = _build_prompt(change_dicts)

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=resolved_model,
            max_tokens=1024,
            thinking={"type": "adaptive"},
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:  # noqa: BLE001 - surface any SDK/auth/network error uniformly
        raise SummarizerError(f"Anthropic API call failed: {exc}") from exc

    text_parts = [block.text for block in response.content if getattr(block, "type", None) == "text"]
    if not text_parts:
        raise SummarizerError("Anthropic API returned no text content")
    return "\n".join(text_parts).strip()


def summarize_changes(changes: list, model: Optional[str] = None) -> str:
    """Turn a list of `diff_engine.Change` objects into a release-note paragraph."""
    return _call_claude(_changes_to_dicts(changes), model)


def summarize_changes_from_report(report: dict, model: Optional[str] = None) -> str:
    """Same as `summarize_changes`, but from an already-loaded JSON report dict
    (as produced by `raml-guard diff --format json`)."""
    return _call_claude(report.get("changes", []), model)


__all__ = ["DEFAULT_MODEL", "SummarizerError", "summarize_changes", "summarize_changes_from_report"]
