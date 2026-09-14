"""dataweave-copilot command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import dw_validator, explainer, generator
from . import test_generator as testgen


def _get_client():
    import anthropic

    return anthropic.Anthropic()


def _add_model_arg(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--model",
        default=os.environ.get("ANTHROPIC_MODEL", generator.DEFAULT_MODEL),
        help="Anthropic model id (default: %(default)s; overridable via ANTHROPIC_MODEL env var).",
    )


def cmd_generate(args: argparse.Namespace) -> int:
    client = _get_client()

    spec = args.spec
    if os.path.isfile(spec):
        spec = Path(spec).read_text()

    report = generator.generate(
        client=client,
        spec=spec,
        input_path=args.input,
        output_path=args.output,
        model=args.model,
        max_attempts=args.max_attempts,
    )

    Path(args.out).write_text(report.final_script)

    print(f"Wrote {args.out}")
    print(report.summary())

    if report.verification_mode == "static":
        print(
            "\nNOTE: verified via STATIC structural validation only. "
            "Real DataWeave execution was not attempted for this run "
            "(pass --input to enable it, if this platform's binary is "
            "available). The script has NOT been run against a real "
            "DataWeave engine to confirm its output."
        )
    elif report.succeeded:
        tail = " and matched --output exactly." if args.output else " (no --output given to compare against)."
        print("\nVerified via REAL DataWeave execution against --input" + tail)
    else:
        print(
            "\nWARNING: real DataWeave execution did not succeed/match after "
            f"{len(report.attempts)} attempt(s). The script written to --out "
            "is the last attempt made and may be broken or incorrect."
        )

    return 0 if report.succeeded else 1


def cmd_explain(args: argparse.Namespace) -> int:
    client = _get_client()
    explanation = explainer.explain(client, args.script, model=args.model)
    print(explanation)
    return 0


def cmd_gen_tests(args: argparse.Namespace) -> int:
    client = _get_client()
    report = testgen.generate_tests(client, args.script, model=args.model)

    Path(args.out).write_text(report.xml)
    print(f"Wrote {args.out}")
    print(f"Well-formed XML: {report.well_formed} (attempts: {report.attempts})")

    if report.well_formed:
        print(f"munit:test elements found: {report.test_count}")
        covered = ", ".join(report.covered_categories) or "none detected"
        print(f"Edge-case categories apparently covered (keyword heuristic): {covered}")
        missing = [c for c in testgen.EDGE_CASE_CATEGORIES if c not in report.covered_categories]
        if missing:
            print(f"Categories NOT clearly covered: {', '.join(missing)}")
    else:
        print(f"ERROR: generated XML was not well-formed: {report.parse_error}")

    print(
        "\nNOTE: this checks XML well-formedness and keyword-based structural "
        "coverage only. MUnit test EXECUTION is not verified -- no Mule "
        "runtime is available in this environment."
    )
    return 0 if report.well_formed else 1


def cmd_validate(args: argparse.Namespace) -> int:
    """Undocumented debugging helper: run just the static validator
    against a script with no API call."""
    content = Path(args.script).read_text()
    result = dw_validator.validate(content)
    print(result.summary())
    return 0 if result.valid else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="dataweave-copilot",
        description="An AI copilot for MuleSoft DataWeave 2.0.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_gen = sub.add_parser("generate", help="Generate a DataWeave script from a plain-English spec.")
    p_gen.add_argument("--spec", required=True, help="Plain-English spec text, or a path to a file containing it.")
    p_gen.add_argument("--input", help="Path to a sample input file (JSON or CSV).")
    p_gen.add_argument("--output", help="Path to a sample expected-output file (JSON).")
    p_gen.add_argument("--out", required=True, help="Path to write the generated .dwl script to.")
    p_gen.add_argument("--max-attempts", type=int, default=generator.MAX_ATTEMPTS_DEFAULT)
    _add_model_arg(p_gen)
    p_gen.set_defaults(func=cmd_generate)

    p_explain = sub.add_parser("explain", help="Explain what a DataWeave script does.")
    p_explain.add_argument("script", help="Path to the .dwl script to explain.")
    _add_model_arg(p_explain)
    p_explain.set_defaults(func=cmd_explain)

    p_tests = sub.add_parser("gen-tests", help="Generate an MUnit-style edge-case test suite for a script.")
    p_tests.add_argument("script", help="Path to the .dwl script to generate tests for.")
    p_tests.add_argument("--out", required=True, help="Path to write the generated MUnit XML to.")
    _add_model_arg(p_tests)
    p_tests.set_defaults(func=cmd_gen_tests)

    p_validate = sub.add_parser("validate", help="Run static structural validation only (no API call, no cost).")
    p_validate.add_argument("script", help="Path to the .dwl script to validate.")
    p_validate.set_defaults(func=cmd_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
