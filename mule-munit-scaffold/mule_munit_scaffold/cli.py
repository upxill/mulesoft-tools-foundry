from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import scaffold
from .report import build_summary
from .xml_parser import get_flow_raw_xml, parse_app


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="mule-munit-scaffold",
        description=(
            "Generate MUnit test skeletons -- with a mock already wired up "
            "for every outbound connector call -- from real Mule 4 flow XML."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    gen = sub.add_parser(
        "generate",
        help="Generate a MUnit test-suite XML file from a Mule flow XML file or app directory.",
    )
    gen.add_argument("path", help="Path to a single flow .xml file, or a directory containing Mule app XML.")
    gen.add_argument("--out", required=True, help="Path to write the generated MUnit test-suite XML to.")
    gen.add_argument(
        "--include-subflows",
        action="store_true",
        help="Also scaffold a test for each <sub-flow> (by default only top-level <flow> elements get tests).",
    )
    gen.add_argument(
        "--enrich",
        action="store_true",
        help=(
            "Call Claude to fill in smarter mock payloads and assertions based on the "
            "real flow XML. Off by default -- the base scaffold never calls an LLM."
        ),
    )
    gen.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
        help="Model to use with --enrich (default: $ANTHROPIC_MODEL or claude-opus-5).",
    )

    return parser


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    if args.command == "generate":
        return _run_generate(args)

    parser.print_help()
    return 1


def _run_generate(args) -> int:
    try:
        parsed = parse_app(Path(args.path))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    tree = scaffold.build_test_suite(parsed, include_subflows=args.include_subflows)

    enrich_notes = []
    if args.enrich:
        from . import enrich as enrich_module

        flows = scaffold.flows_in_scope(parsed, args.include_subflows)
        flow_xml_by_name = {f.name: get_flow_raw_xml(f) for f in flows}
        try:
            enrich_notes = enrich_module.enrich_test_suite(
                tree, flows, flow_xml_by_name, model=args.model
            )
        except RuntimeError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 1

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    scaffold.write_test_suite(tree, out_path)

    print(f"Parsed {len(parsed.files)} file(s), {len(parsed.flows)} flow(s)/sub-flow(s) total.")
    print(f"Wrote {out_path}")
    print()
    print(build_summary(parsed, args.include_subflows))

    if args.enrich:
        print()
        print(f"--enrich: called {args.model} to refine mock payloads/assertions.")
        if enrich_notes:
            print("\n".join(enrich_notes))
    else:
        print()
        print(
            "NOTE: this is the deterministic base scaffold (no LLM call was made). "
            "Pass --enrich to ask Claude to fill in smarter mock payloads and assertions."
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
