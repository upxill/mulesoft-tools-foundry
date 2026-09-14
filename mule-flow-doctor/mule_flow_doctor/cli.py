"""``mule-flow-doctor review PATH --out report/`` command-line entry point."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from mule_flow_doctor import graph, report, reviewer, xml_parser


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule-flow-doctor",
        description="AI-powered architectural reviewer for Mule 4 application XML.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    review_p = sub.add_parser(
        "review", help="Review a Mule application (a directory of .xml files, or a single .xml file)"
    )
    review_p.add_argument("path", help="Path to a Mule app directory or a single .xml file")
    review_p.add_argument(
        "--out", default="report", help="Output directory for architecture.md/report.md/report.json"
    )
    review_p.add_argument(
        "--model",
        default=None,
        help="Anthropic model id (default: $ANTHROPIC_MODEL or claude-opus-5)",
    )
    review_p.add_argument(
        "--skip-llm",
        action="store_true",
        help="Only run the deterministic parse + architecture diagram; skip the LLM findings step entirely.",
    )
    review_p.add_argument(
        "--dry-run-llm",
        action="store_true",
        help=(
            "Do not call the Anthropic API. Synthesize findings directly from the "
            "deterministic scan via a scripted stand-in, clearly labeled as a dry "
            "run in the output. Useful when no API credentials are configured."
        ),
    )
    return parser


def _cmd_review(args: argparse.Namespace) -> int:
    path = Path(args.path)
    if not path.exists():
        print(f"error: path does not exist: {path}", file=sys.stderr)
        return 1

    parsed = xml_parser.parse_app(path)

    print(f"Parsed {len(parsed.files)} file(s), {len(parsed.flows)} flow(s)/sub-flow(s).")

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    architecture_md = graph.render_architecture_markdown(parsed)
    architecture_path = out_dir / "architecture.md"
    architecture_path.write_text(architecture_md, encoding="utf-8")
    print(f"Wrote {architecture_path}")

    if args.skip_llm:
        print("--skip-llm set: skipping LLM findings step. No report.md/report.json written.")
        return 0

    structural_summary = xml_parser.summarize(parsed)

    if args.dry_run_llm:
        print("--dry-run-llm set: synthesizing findings from the deterministic scan "
              "WITHOUT calling the Anthropic API.")
        review_report = reviewer.dry_run_review(structural_summary)
    else:
        if not (os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")):
            print(
                "error: cannot call the live Anthropic API -- no ANTHROPIC_API_KEY or "
                "ANTHROPIC_AUTH_TOKEN is set in the environment.",
                file=sys.stderr,
            )
            print(
                "Set ANTHROPIC_API_KEY and try again, or pass --dry-run-llm to synthesize "
                "findings from the deterministic scan without calling the API, or --skip-llm "
                "to only run the deterministic parse + architecture diagram.",
                file=sys.stderr,
            )
            return 1
        model = args.model or os.environ.get("ANTHROPIC_MODEL", reviewer.DEFAULT_MODEL)
        print(f"Calling Anthropic API (model={model}) for LLM-powered findings...")
        excerpts = xml_parser.raw_excerpts(parsed)
        try:
            review_report = reviewer.review(structural_summary, excerpts, model=model)
        except Exception as e:  # noqa: BLE001 - surface a clean message, not a raw traceback
            print(f"error: the live Anthropic API call failed: {type(e).__name__}: {e}", file=sys.stderr)
            print(
                "Try --dry-run-llm to synthesize findings from the deterministic scan instead.",
                file=sys.stderr,
            )
            return 1

    md_path, json_path = report.write_report(review_report, out_dir)
    print(f"Wrote {md_path}")
    print(f"Wrote {json_path}")
    print("")
    print(review_report.summary)
    return 0


def main(argv=None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "review":
        return _cmd_review(args)
    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
