"""Tests for raml_guard.cli: exit codes and --fail-on behavior.

The core promise being tested here is the CI-gating contract: `diff`'s exit
code must be driven purely by the deterministic diff engine, with no API key
or network access required.
"""

from __future__ import annotations

from pathlib import Path

from raml_guard.cli import main

EXAMPLES_DIR = Path(__file__).resolve().parent.parent / "examples"


def test_diff_exits_zero_for_safe_changes(tmp_path, capsys):
    out_file = tmp_path / "report.md"
    code = main(
        [
            "diff",
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
            str(EXAMPLES_DIR / "orders_api_v2_safe.raml"),
            "--out",
            str(out_file),
        ]
    )
    assert code == 0
    assert out_file.exists()
    assert "Breaking changes: 0" in out_file.read_text(encoding="utf-8")


def test_diff_exits_nonzero_for_breaking_changes(tmp_path):
    out_file = tmp_path / "report.md"
    code = main(
        [
            "diff",
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
            str(EXAMPLES_DIR / "orders_api_v2_breaking.raml"),
            "--out",
            str(out_file),
        ]
    )
    assert code == 1
    assert "Breaking changes:" in out_file.read_text(encoding="utf-8")


def test_fail_on_any_fails_on_safe_changes_too(tmp_path):
    out_file = tmp_path / "report.md"
    code = main(
        [
            "diff",
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
            str(EXAMPLES_DIR / "orders_api_v2_safe.raml"),
            "--out",
            str(out_file),
            "--fail-on",
            "any",
        ]
    )
    assert code == 1


def test_json_format_report(tmp_path):
    import json

    out_file = tmp_path / "report.json"
    code = main(
        [
            "diff",
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
            str(EXAMPLES_DIR / "orders_api_v2_breaking.raml"),
            "--out",
            str(out_file),
            "--format",
            "json",
        ]
    )
    assert code == 1
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data["summary"]["breaking"] >= 2
    assert isinstance(data["changes"], list)


def test_missing_file_exits_with_error_not_crash(tmp_path):
    code = main(
        [
            "diff",
            str(tmp_path / "does_not_exist.raml"),
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
        ]
    )
    assert code == 2


def test_diff_never_imports_anthropic_without_summarize(monkeypatch):
    """The core diff command must work with zero network/credentials -- this
    guards against a future regression that imports the `anthropic` package
    (or otherwise touches summarizer's network path) unconditionally."""
    import sys

    # Ensure a fresh import of raml_guard.cli doesn't drag in anthropic eagerly.
    for mod_name in list(sys.modules):
        if mod_name == "anthropic" or mod_name.startswith("anthropic."):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)
    monkeypatch.setitem(sys.modules, "anthropic", None)  # any accidental import raises

    code = main(
        [
            "diff",
            str(EXAMPLES_DIR / "orders_api_v1.raml"),
            str(EXAMPLES_DIR / "orders_api_v2_safe.raml"),
        ]
    )
    assert code == 0
