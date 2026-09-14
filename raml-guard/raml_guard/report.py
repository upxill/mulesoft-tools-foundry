"""Render a list of `diff_engine.Change` objects as Markdown or JSON."""

from __future__ import annotations

import json

from .diff_engine import BREAKING, INFORMATIONAL, NON_BREAKING


def _unresolved_includes(old_spec, new_spec) -> list:
    return sorted(set((old_spec.unresolved_includes or []) + (new_spec.unresolved_includes or [])))


def render_markdown(old_path: str, new_path: str, changes: list, old_spec, new_spec) -> str:
    breaking = [c for c in changes if c.category == BREAKING]
    non_breaking = [c for c in changes if c.category == NON_BREAKING]
    informational = [c for c in changes if c.category == INFORMATIONAL]

    lines = []
    lines.append("# raml-guard diff report")
    lines.append("")
    lines.append(f"- Old spec: `{old_path}`")
    lines.append(f"- New spec: `{new_path}`")
    lines.append(f"- **Breaking changes: {len(breaking)}**")
    lines.append(f"- Non-breaking changes: {len(non_breaking)}")
    lines.append(f"- Informational changes: {len(informational)}")
    lines.append("")

    unresolved = _unresolved_includes(old_spec, new_spec)
    if unresolved:
        lines.append("## Unresolved `!include` references")
        lines.append("")
        lines.append(
            "The following `!include` targets could not be resolved on disk (missing file, "
            "parse error, or nesting too deep). They were treated as opaque references: their "
            "internals were **not** diffed, to avoid fabricating findings about content that "
            "was never actually read."
        )
        lines.append("")
        for ref in unresolved:
            lines.append(f"- `{ref}`")
        lines.append("")

    def render_section(title: str, items: list) -> None:
        lines.append(f"## {title} ({len(items)})")
        lines.append("")
        if not items:
            lines.append("_None._")
            lines.append("")
            return
        for c in items:
            lines.append(f"- **{c.location}** -- {c.message} (`{c.kind}`)")
        lines.append("")

    render_section("Breaking", breaking)
    render_section("Non-Breaking", non_breaking)
    render_section("Informational", informational)

    return "\n".join(lines) + "\n"


def render_json(old_path: str, new_path: str, changes: list, old_spec, new_spec) -> str:
    payload = {
        "old_spec": old_path,
        "new_spec": new_path,
        "summary": {
            "breaking": sum(1 for c in changes if c.category == BREAKING),
            "non_breaking": sum(1 for c in changes if c.category == NON_BREAKING),
            "informational": sum(1 for c in changes if c.category == INFORMATIONAL),
        },
        "unresolved_includes": _unresolved_includes(old_spec, new_spec),
        "changes": [
            {"category": c.category, "location": c.location, "kind": c.kind, "message": c.message} for c in changes
        ],
    }
    return json.dumps(payload, indent=2) + "\n"


__all__ = ["render_markdown", "render_json"]
