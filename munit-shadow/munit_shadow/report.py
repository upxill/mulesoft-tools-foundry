"""Renders the ambient, "shadow"-style CLI status lines.

Ambient means quiet: a flow that produced no change prints nothing at all
(unless ``--verbose``), and a flow that did change prints exactly one
concise line summarizing what happened -- never a wall of XML diff output.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional


@dataclass
class FlowSyncResult:
    flow_file: Path
    test_file: Path
    test_file_existed: bool
    new_file_created: bool = False
    changed: bool = False
    new_mock_operations: List[str] = field(default_factory=list)
    newly_flagged_stale_operations: List[str] = field(default_factory=list)
    still_flagged_stale_count: int = 0
    new_tests_added: List[str] = field(default_factory=list)
    error: Optional[str] = None


def format_status_line(result: FlowSyncResult, verbose: bool = False) -> Optional[str]:
    """Returns the one-line status string for this flow's sync result, or
    ``None`` if nothing happened and the caller isn't in ``--verbose`` mode
    (in which case the ambient agent should print nothing for this flow).
    """
    flow_label = result.flow_file.name

    if result.error:
        return f"{flow_label}: error -- {result.error}"

    if result.new_file_created:
        return f"{flow_label} -> created {result.test_file} (no test file existed yet)"

    if not result.changed:
        if verbose:
            return f"{flow_label}: no changes"
        return None

    parts = []
    if result.new_mock_operations:
        ops = ", ".join(result.new_mock_operations)
        parts.append(f"+{len(result.new_mock_operations)} new mock ({ops})")
    if result.newly_flagged_stale_operations:
        ops = ", ".join(result.newly_flagged_stale_operations)
        n = len(result.newly_flagged_stale_operations)
        plural = "mock" if n == 1 else "mocks"
        parts.append(f"{n} stale {plural} flagged ({ops} no longer in flow)")
    if result.new_tests_added:
        names = ", ".join(result.new_tests_added)
        parts.append(f"+{len(result.new_tests_added)} new test block ({names})")
    if verbose and result.still_flagged_stale_count:
        parts.append(f"{result.still_flagged_stale_count} already-flagged stale mock(s) unchanged")

    detail = ", ".join(parts) if parts else "no visible change"
    return f"{flow_label} changed -> updated {result.test_file}: {detail}"


def format_summary(results: List[FlowSyncResult]) -> str:
    """A one-shot `sync` run's closing summary line."""
    changed = [r for r in results if r.changed or r.new_file_created]
    errors = [r for r in results if r.error]
    total = len(results)
    lines = [f"munit-shadow: synced {total} flow file(s), {len(changed)} test file(s) updated."]
    if errors:
        lines.append(f"{len(errors)} flow(s) failed to sync -- see errors above.")
    return "\n".join(lines)
