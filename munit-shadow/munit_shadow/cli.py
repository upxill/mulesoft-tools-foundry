from __future__ import annotations

import argparse
import os
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, List, Optional

from . import scaffold, splice
from .mock_diff import diff_calls
from .report import FlowSyncResult, format_status_line, format_summary
from .test_file_index import NamingConvention, find_flow_files, test_file_for_flow
from .xml_parser import dedupe_calls, get_flow_raw_xml, parse_flow_file


def _build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="munit-shadow",
        description=(
            "An ambient agent that watches a Mule 4 project's flow XML and keeps "
            "its MUnit tests incrementally, in-place, byte-preservingly in sync."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument("project_dir", help="Path to the root of a Mule Maven project (contains src/main/mule).")
        p.add_argument(
            "--test-dir",
            default="src/test/munit",
            help="Subdirectory (relative to project_dir) that MUnit test files live in (default: src/test/munit).",
        )
        p.add_argument(
            "--flow-dir",
            default="src/main/mule",
            help="Subdirectory (relative to project_dir) that flow XML files live in (default: src/main/mule).",
        )
        p.add_argument(
            "--test-suffix",
            default="-test",
            help="Suffix appended to a flow file's stem to derive its test filename (default: -test, "
            "e.g. orders-flow.xml -> orders-flow-test.xml).",
        )
        p.add_argument(
            "--enrich",
            action="store_true",
            help="Call Claude to suggest realistic then-return payloads for NEWLY added mocks. Off by default.",
        )
        p.add_argument(
            "--model",
            default=os.environ.get("ANTHROPIC_MODEL", "claude-opus-5"),
            help="Model to use with --enrich (default: $ANTHROPIC_MODEL or claude-opus-5).",
        )
        p.add_argument("--dry-run", action="store_true", help="Report what would change without writing any file.")
        p.add_argument("--verbose", action="store_true", help="Print a line for every flow, even unchanged ones.")

    sync_p = sub.add_parser("sync", help="Sync every flow's MUnit test file once, and exit.")
    add_common(sync_p)

    watch_p = sub.add_parser("watch", help="Run sync continuously, reacting to flow file changes, until Ctrl+C.")
    add_common(watch_p)
    watch_p.add_argument(
        "--poll-interval",
        type=float,
        default=2.0,
        help="Seconds between mtime polls in the default (dependency-free) watch mode (default: 2.0).",
    )
    watch_p.add_argument(
        "--use-watchdog",
        action="store_true",
        help="Use the optional 'watchdog' package for real OS-level filesystem events instead of mtime polling.",
    )

    return parser


def _convention_from_args(args) -> NamingConvention:
    return NamingConvention(flow_dir=args.flow_dir, test_dir=args.test_dir, test_suffix=args.test_suffix)


def _compute_new_calls_for_enrichment(original_bytes: bytes, flows) -> Dict[str, list]:
    """A lightweight pre-pass (mirrors splice.sync_existing_test_file's own
    diff, without applying anything) so --enrich knows which calls are
    NEW *before* rendering their mock-when blocks, since the payload must
    be baked into the text splice.sync_existing_test_file produces.
    """
    try:
        index = splice.build_index(original_bytes)
    except Exception:
        return {}

    new_calls_by_flow: Dict[str, list] = {}
    for flow in flows:
        test_name = f"test-{flow.name}"
        current_calls = dedupe_calls(flow.outbound_calls)
        if test_name not in index.tests:
            if current_calls:
                new_calls_by_flow[flow.name] = current_calls
            continue
        existing_for_test = [m for m in index.mocks if m.test_name == test_name]
        diff = diff_calls(current_calls, existing_for_test)
        if diff.new_calls:
            new_calls_by_flow[flow.name] = diff.new_calls
    return new_calls_by_flow


def _enrich_then_return_values(flow_file: Path, flows, original_bytes: bytes, model: str) -> Dict[tuple, str]:
    from . import enrich as enrich_module

    new_calls_by_flow = _compute_new_calls_for_enrichment(original_bytes, flows)
    if not new_calls_by_flow:
        return {}

    then_return_values: Dict[tuple, str] = {}
    for flow in flows:
        new_calls = new_calls_by_flow.get(flow.name)
        if not new_calls:
            continue
        flow_xml = get_flow_raw_xml(flow_file, flow.name)
        results = enrich_module.enrich_new_call_payloads(flow, flow_xml, new_calls, model=model)
        then_return_values.update(results)
    return then_return_values


def sync_one_flow_file(
    flow_file: Path,
    project_dir: Path,
    convention: NamingConvention,
    enrich: bool,
    model: str,
    dry_run: bool,
) -> FlowSyncResult:
    test_file = test_file_for_flow(flow_file, project_dir, convention)

    try:
        parsed = parse_flow_file(flow_file)
    except ET.ParseError as exc:
        return FlowSyncResult(flow_file=flow_file, test_file=test_file, test_file_existed=test_file.exists(), error=str(exc))

    flows = list(parsed.flows.values())

    if not flows:
        # A file with no <flow>/<sub-flow> at all (e.g. a global-config.xml
        # holding only *-config elements) defines nothing to test -- skip it
        # rather than scaffolding an empty test file for it.
        return FlowSyncResult(flow_file=flow_file, test_file=test_file, test_file_existed=test_file.exists())

    if not test_file.exists():
        text = scaffold.render_new_test_suite_text(flows)
        if not dry_run:
            test_file.parent.mkdir(parents=True, exist_ok=True)
            test_file.write_text(text, encoding="utf-8")
        all_calls = []
        for f in flows:
            all_calls.extend(dedupe_calls(f.outbound_calls))
        return FlowSyncResult(
            flow_file=flow_file,
            test_file=test_file,
            test_file_existed=False,
            new_file_created=True,
            changed=True,
            new_mock_operations=[c.operation for c in all_calls],
        )

    original_bytes = test_file.read_bytes()

    then_return_values: Dict[tuple, str] = {}
    if enrich:
        try:
            then_return_values = _enrich_then_return_values(flow_file, flows, original_bytes, model)
        except Exception:  # noqa: BLE001 - --enrich must never break the core sync
            then_return_values = {}

    try:
        outcome = splice.sync_existing_test_file(original_bytes, flows, then_return_values)
    except splice.SpliceError as exc:
        return FlowSyncResult(flow_file=flow_file, test_file=test_file, test_file_existed=True, error=str(exc))

    if outcome.changed and not dry_run:
        test_file.write_bytes(outcome.new_bytes)

    return FlowSyncResult(
        flow_file=flow_file,
        test_file=test_file,
        test_file_existed=True,
        changed=outcome.changed,
        new_mock_operations=[c.operation for c in outcome.new_mocks],
        newly_flagged_stale_operations=[m.operation for m in outcome.newly_flagged_stale],
        still_flagged_stale_count=len(outcome.still_flagged_stale),
        new_tests_added=outcome.new_tests_added,
    )


def run_sync(
    project_dir: Path,
    convention: NamingConvention,
    enrich: bool = False,
    model: str = "claude-opus-5",
    dry_run: bool = False,
    verbose: bool = False,
    print_fn=print,
) -> List[FlowSyncResult]:
    flow_files = find_flow_files(project_dir, convention)
    results = []
    for flow_file in flow_files:
        result = sync_one_flow_file(flow_file, project_dir, convention, enrich, model, dry_run)
        results.append(result)
        line = format_status_line(result, verbose=verbose)
        if line:
            print_fn(line)
    return results


def main(argv=None) -> int:
    parser = _build_arg_parser()
    args = parser.parse_args(argv)

    project_dir = Path(args.project_dir)
    if not project_dir.is_dir():
        print(f"error: {project_dir} is not a directory", file=sys.stderr)
        return 1

    convention = _convention_from_args(args)

    if args.command == "sync":
        results = run_sync(
            project_dir,
            convention,
            enrich=args.enrich,
            model=args.model,
            dry_run=args.dry_run,
            verbose=args.verbose,
        )
        print(format_summary(results))
        return 1 if any(r.error for r in results) else 0

    if args.command == "watch":
        from .watcher import watch

        try:
            watch(
                project_dir,
                convention,
                enrich=args.enrich,
                model=args.model,
                poll_interval=args.poll_interval,
                use_watchdog=args.use_watchdog,
                verbose=args.verbose,
            )
        except KeyboardInterrupt:
            print("munit-shadow: stopped.")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
