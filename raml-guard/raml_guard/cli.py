"""raml-guard CLI.

    raml-guard diff old.raml new.raml --out report.md [--fail-on breaking|any] [--format md|json]
    raml-guard explain report.json --out notes.md

`diff`'s exit code is decided entirely by the deterministic diff engine
(`diff_engine.py`) -- no API key or network access is ever required to get a
pass/fail result. `--summarize` (on `diff`) and the `explain` command are the
only things in this CLI that call the Anthropic API, and they never affect
the exit code of `diff` itself (a failed/skipped summarization only prints a
warning to stderr).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Optional

from . import __version__
from .diff_engine import BREAKING, INFORMATIONAL, NON_BREAKING, diff_specs
from .raml_parser import RamlParseError, parse_raml_file
from .report import render_json, render_markdown
from .summarizer import SummarizerError, summarize_changes, summarize_changes_from_report

try:
    import argparse
except ImportError:  # pragma: no cover - argparse is stdlib
    raise


def _build_parser() -> "argparse.ArgumentParser":
    parser = argparse.ArgumentParser(
        prog="raml-guard",
        description="Deterministic RAML 1.0 diff / breaking-change CI gate for MuleSoft API governance.",
    )
    parser.add_argument("--version", action="version", version=f"raml-guard {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    diff_p = sub.add_parser("diff", help="Diff two RAML specs and classify every change.")
    diff_p.add_argument("old", help="Path to the old/baseline RAML file.")
    diff_p.add_argument("new", help="Path to the new/candidate RAML file.")
    diff_p.add_argument("--out", help="Write the report to this file (default: print to stdout).")
    diff_p.add_argument(
        "--fail-on",
        choices=["breaking", "any"],
        default="breaking",
        help="Exit non-zero if changes at/above this severity are found. "
        "'breaking' (default) fails only on breaking changes; "
        "'any' also fails on non-breaking/informational changes (strict mode).",
    )
    diff_p.add_argument("--format", choices=["md", "json"], default="md", help="Report output format.")
    diff_p.add_argument(
        "--summarize",
        action="store_true",
        help="Optional: also call the Anthropic API to append a plain-English release-note "
        "paragraph. Off by default. Requires ANTHROPIC_API_KEY and the 'anthropic' package; "
        "never affects diff's exit code.",
    )
    diff_p.add_argument(
        "--model",
        default=None,
        help="Override the Claude model used by --summarize (default: $ANTHROPIC_MODEL or claude-opus-5).",
    )

    explain_p = sub.add_parser(
        "explain",
        help="Turn a JSON diff report (from `diff --format json`) into a release-note paragraph via the Anthropic API.",
    )
    explain_p.add_argument("report", help="Path to a JSON report produced by `raml-guard diff --format json`.")
    explain_p.add_argument("--out", help="Write the summary to this file (default: print to stdout).")
    explain_p.add_argument("--model", default=None, help="Override the Claude model (default: $ANTHROPIC_MODEL or claude-opus-5).")

    return parser


def _run_diff(args) -> int:
    try:
        old_spec = parse_raml_file(args.old)
        new_spec = parse_raml_file(args.new)
    except (RamlParseError, FileNotFoundError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    changes = diff_specs(old_spec, new_spec)

    if args.format == "json":
        report_text = render_json(args.old, args.new, changes, old_spec, new_spec)
    else:
        report_text = render_markdown(args.old, args.new, changes, old_spec, new_spec)

    if args.summarize:
        try:
            summary_text = summarize_changes(changes, model=args.model)
        except SummarizerError as exc:
            print(f"warning: --summarize failed, continuing without it: {exc}", file=sys.stderr)
        else:
            if args.format == "md":
                report_text += "\n## AI-Generated Release Note\n\n" + summary_text + "\n"
            else:
                data = json.loads(report_text)
                data["ai_summary"] = summary_text
                report_text = json.dumps(data, indent=2) + "\n"

    if args.out:
        Path(args.out).write_text(report_text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(report_text)

    n_breaking = sum(1 for c in changes if c.category == BREAKING)
    n_non_breaking = sum(1 for c in changes if c.category == NON_BREAKING)
    n_informational = sum(1 for c in changes if c.category == INFORMATIONAL)
    print(
        f"raml-guard: breaking={n_breaking} non_breaking={n_non_breaking} informational={n_informational}",
        file=sys.stderr,
    )

    if args.fail_on == "breaking" and n_breaking > 0:
        return 1
    if args.fail_on == "any" and (n_breaking + n_non_breaking + n_informational) > 0:
        return 1
    return 0


def _run_explain(args) -> int:
    try:
        report = json.loads(Path(args.report).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: could not read report '{args.report}': {exc}", file=sys.stderr)
        return 2

    try:
        text = summarize_changes_from_report(report, model=args.model)
    except SummarizerError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        print(f"Wrote {args.out}")
    else:
        print(text)
    return 0


def main(argv: Optional[list] = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "diff":
        return _run_diff(args)
    if args.command == "explain":
        return _run_explain(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
