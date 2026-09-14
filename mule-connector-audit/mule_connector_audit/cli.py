"""mule-connector-audit CLI.

    mule-connector-audit scan path/to/pom.xml --out report.md [--major-behind-only] [--timeout 10]

The core `scan` command never touches an LLM or requires any API key --
it's a deterministic pom.xml parse plus a real HTTP call to the public
Maven Central Search API. `--explain` is a separate, optional enhancement
layer (see explain.py).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .audit import run_audit
from .maven_client import MavenCentralClient, MavenNetworkError
from .pom_parser import PomParseError
from .report import render_markdown, write_report


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule-connector-audit",
        description=(
            "Audit a Mule 4 project's pom.xml for stale MuleSoft connector "
            "dependencies, using the real Maven Central Search API. No "
            "Anthropic API key or LLM required for the core audit."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="Scan a pom.xml and write a staleness report.")
    scan.add_argument("pom_path", help="Path to the project's pom.xml")
    scan.add_argument(
        "--out", default="report.md", help="Path to write the Markdown report (default: report.md)"
    )
    scan.add_argument(
        "--json-out",
        default=None,
        help="Optional path to also write a machine-readable JSON report.",
    )
    scan.add_argument(
        "--major-behind-only",
        action="store_true",
        help="Only include connectors that are a MAJOR version behind in the report.",
    )
    scan.add_argument(
        "--timeout",
        type=float,
        default=10.0,
        help="Per-request timeout in seconds for Maven Central lookups (default: 10).",
    )
    scan.add_argument(
        "--abandoned-days",
        type=int,
        default=730,
        help="Flag a connector as possibly abandoned if its latest Maven Central "
        "release is older than this many days (default: 730, ~2 years).",
    )
    scan.add_argument(
        "--print",
        dest="print_stdout",
        action="store_true",
        help="Also print the Markdown report to stdout.",
    )
    scan.add_argument(
        "--explain",
        action="store_true",
        help="Optional: print an LLM-generated plain-English summary after the "
        "scan (falls back to a disclosed scripted dry run with no API calls if "
        "ANTHROPIC_API_KEY is not set). Never required for the core audit.",
    )
    scan.add_argument(
        "--explain-model",
        default="claude-opus-5",
        help="Model to use for --explain (default: claude-opus-5).",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "scan":
        return _cmd_scan(args)

    parser.print_help()
    return 1


def _cmd_scan(args: argparse.Namespace) -> int:
    pom_path = Path(args.pom_path)

    client = MavenCentralClient(timeout=args.timeout)

    try:
        report = run_audit(pom_path, client=client, abandoned_days=args.abandoned_days)
    except PomParseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    except MavenNetworkError as exc:
        print(
            "Error: could not complete the audit because a request to the real "
            "Maven Central Search API (search.maven.org) failed.\n"
            f"  {exc}\n"
            "Check your network connection (or try again / raise --timeout) and re-run.",
            file=sys.stderr,
        )
        return 2

    write_report(report, args.out, json_out_path=args.json_out, major_behind_only=args.major_behind_only)
    print(
        f"Scanned {report.total_dependencies} dependencies, "
        f"{len(report.entries)} MuleSoft connector(s) found."
    )
    print(f"Wrote {args.out}")
    if args.json_out:
        print(f"Wrote {args.json_out}")

    if args.print_stdout:
        print()
        print(render_markdown(report, major_behind_only=args.major_behind_only))

    if args.explain:
        from .explain import explain_report, has_anthropic_credentials

        print()
        if not has_anthropic_credentials():
            print(
                "--explain set: no ANTHROPIC_API_KEY found, falling back to a "
                "disclosed scripted dry run (no API call made)."
            )
        else:
            print(f"--explain set: calling the Anthropic API (model={args.explain_model})...")
        try:
            print(explain_report(report, model=args.explain_model))
        except RuntimeError as exc:
            print(f"--explain failed: {exc}", file=sys.stderr)
            return 3

    counts = report.counts()
    if counts["major-behind"] > 0:
        return 0  # informational tool; a stale finding is not itself a hard failure
    return 0


if __name__ == "__main__":
    sys.exit(main())
