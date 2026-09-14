"""Render report.md (human-readable) and report.json (structured) from a
ReviewReport. Pure rendering -- no parsing, no LLM calls.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Tuple

from mule_flow_doctor.reviewer import Finding, ReviewReport

_SEVERITY_ORDER = ["high", "medium", "info"]
_SEVERITY_LABEL = {"high": "High", "medium": "Medium", "info": "Info"}


def _group_by_severity(findings: List[Finding]) -> List[Tuple[str, List[Finding]]]:
    groups = []
    for sev in _SEVERITY_ORDER:
        matching = [f for f in findings if f.severity == sev]
        if matching:
            groups.append((sev, matching))
    return groups


def render_markdown(report: ReviewReport) -> str:
    lines: List[str] = []
    lines.append("# Mule Flow Doctor -- Review Report")
    lines.append("")
    lines.append("## Summary")
    lines.append("")
    lines.append(report.summary)
    lines.append("")

    counts = {sev: sum(1 for f in report.findings if f.severity == sev) for sev in _SEVERITY_ORDER}
    lines.append(
        f"**{len(report.findings)} finding(s)** -- "
        f"{counts['high']} high, {counts['medium']} medium, {counts['info']} info."
    )
    lines.append("")

    groups = _group_by_severity(report.findings)
    if not groups:
        lines.append("No findings.")
        lines.append("")

    for sev, findings in groups:
        lines.append(f"## {_SEVERITY_LABEL[sev]} severity ({len(findings)})")
        lines.append("")
        for f in findings:
            flow_part = f" -- flow `{f.flow_name}`" if f.flow_name else ""
            lines.append(f"### {f.category}: `{f.file}`{flow_part}")
            lines.append("")
            lines.append(f"**Explanation:** {f.explanation}")
            lines.append("")
            lines.append(f"**Recommendation:** {f.recommendation}")
            lines.append("")

    return "\n".join(lines)


def render_json(report: ReviewReport) -> str:
    return json.dumps(report.model_dump(), indent=2)


def write_report(report: ReviewReport, out_dir: Path) -> Tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / "report.md"
    json_path = out_dir / "report.json"
    md_path.write_text(render_markdown(report), encoding="utf-8")
    json_path.write_text(render_json(report), encoding="utf-8")
    return md_path, json_path
