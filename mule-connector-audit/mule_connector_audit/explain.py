"""Optional, OFF-BY-DEFAULT LLM summary of an audit report.

This module is never imported or executed by the core audit path
(`cli.py`'s `scan` command works without it). It exists purely for the
`--explain` flag, which turns the deterministic findings already computed
by `audit.py` into a short plain-English summary for a human reader --
"here's what actually needs attention and why" -- using Claude.

If no Anthropic credentials are available (`ANTHROPIC_API_KEY` unset and no
`ant auth login` session), `explain_report()` automatically falls back to
`dry_run_explain()`, a scripted stand-in that builds the summary directly
from the same structured findings without calling any API -- and every
line it produces is prefixed so it's never mistaken for a real model
response, matching this author's other tools' disclosure convention
(see mule-flow-doctor's `--dry-run-llm`).
"""

from __future__ import annotations

import os

from .audit import AuditReport
from .versioning import Staleness

DRY_RUN_PREFIX = "[DRY RUN: scripted from audit findings, not from Claude]"


def has_anthropic_credentials() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY"))


def dry_run_explain(report: AuditReport) -> str:
    """Build a scripted, non-LLM summary from the same structured findings."""
    counts = report.counts()
    major = [e for e in report.entries if e.staleness == Staleness.MAJOR_BEHIND]
    abandoned = [e for e in report.entries if e.possibly_abandoned]
    not_found = [e for e in report.entries if e.staleness == Staleness.NOT_FOUND]

    lines = [DRY_RUN_PREFIX]
    if not report.entries:
        lines.append("No MuleSoft connector dependencies were found in this pom.xml.")
        return "\n".join(lines)

    lines.append(
        f"{len(report.entries)} connector(s) audited: "
        f"{counts[Staleness.UP_TO_DATE.value]} up to date, "
        f"{counts[Staleness.MINOR_BEHIND.value]} minor/patch behind, "
        f"{counts[Staleness.MAJOR_BEHIND.value]} a major version behind, "
        f"{counts[Staleness.NOT_FOUND.value]} not indexed on Maven Central."
    )
    if major:
        names = ", ".join(f"{e.group_id}:{e.artifact_id} ({e.pinned_version} -> {e.latest_version})" for e in major)
        lines.append(f"Highest priority (major version behind): {names}.")
    if abandoned:
        names = ", ".join(f"{e.group_id}:{e.artifact_id}" for e in abandoned)
        lines.append(f"No newer release in 2+ years on Maven Central: {names}.")
    if not_found:
        names = ", ".join(f"{e.group_id}:{e.artifact_id}" for e in not_found)
        lines.append(
            f"Not indexed on Maven Central (verify manually via Anypoint Exchange): {names}."
        )
    return "\n".join(lines)


def _build_prompt(report: AuditReport) -> str:
    rows = []
    for e in report.entries:
        rows.append(
            f"- {e.group_id}:{e.artifact_id} | pinned={e.pinned_version} | "
            f"latest={e.latest_version} | staleness={e.staleness.value} | "
            f"possibly_abandoned={e.possibly_abandoned} | last_release={e.release_date_str}"
        )
    table = "\n".join(rows) if rows else "(no connector dependencies found)"
    return (
        "You are summarizing a deterministic MuleSoft connector dependency audit "
        "for a team lead. The data below was computed by parsing a pom.xml and "
        "querying the real Maven Central Search API -- do not invent any facts "
        "beyond what's listed. Write a short (4-8 sentence) plain-English summary "
        "prioritizing what needs attention first and why, suitable to paste into "
        "a Slack message.\n\n"
        f"Audit results for {report.pom_path}:\n{table}"
    )


def live_explain(report: AuditReport, model: str = "claude-opus-5") -> str:
    """Call the real Anthropic API to summarize the audit findings.

    Raises RuntimeError with a clean message (never a raw SDK traceback) if
    the `anthropic` package isn't installed or the call fails.
    """
    try:
        import anthropic
    except ImportError as exc:
        raise RuntimeError(
            "The optional 'anthropic' package is not installed. Install it with "
            "`pip install mule-connector-audit[explain]` to use --explain."
        ) from exc

    try:
        client = anthropic.Anthropic()
        response = client.messages.create(
            model=model,
            max_tokens=1024,
            messages=[{"role": "user", "content": _build_prompt(report)}],
        )
        return "".join(
            block.text for block in response.content if getattr(block, "type", None) == "text"
        ).strip()
    except Exception as exc:  # noqa: BLE001 - surface any SDK/network error cleanly
        raise RuntimeError(f"Call to the Anthropic API failed: {exc}") from exc


def explain_report(report: AuditReport, model: str = "claude-opus-5", force_dry_run: bool = False) -> str:
    if force_dry_run or not has_anthropic_credentials():
        return dry_run_explain(report)
    return live_explain(report, model=model)
