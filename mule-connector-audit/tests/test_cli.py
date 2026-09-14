from pathlib import Path

import pytest

from mule_connector_audit import cli
from mule_connector_audit.maven_client import MavenNetworkError

from .fake_maven_client import FakeMavenClient

EXAMPLE_POM = str(Path(__file__).parent.parent / "examples" / "pom.xml")


def _patch_client(monkeypatch, catalog):
    def _factory(timeout=10.0, rows=100):
        return FakeMavenClient(catalog)

    monkeypatch.setattr(cli, "MavenCentralClient", _factory)


def test_scan_happy_path(tmp_path, monkeypatch, capsys):
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", 1506613359000),
        "org.mule.connectors:mule-db-connector": ("0.9.0", 1506610832000),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", 1506611547000),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", 1506614190000),
    }
    _patch_client(monkeypatch, catalog)
    out_path = tmp_path / "report.md"
    rc = cli.main(["scan", EXAMPLE_POM, "--out", str(out_path)])
    assert rc == 0
    assert out_path.exists()
    captured = capsys.readouterr()
    assert "MuleSoft connector" in captured.out


def test_scan_missing_pom_exits_nonzero_with_clean_message(tmp_path, monkeypatch, capsys):
    _patch_client(monkeypatch, {})
    rc = cli.main(["scan", str(tmp_path / "nope.xml"), "--out", str(tmp_path / "report.md")])
    assert rc == 1
    captured = capsys.readouterr()
    assert "not found" in captured.err.lower()
    assert "Traceback" not in captured.err


def test_scan_network_failure_exits_nonzero_with_clean_message(tmp_path, monkeypatch, capsys):
    class _BoomClient:
        def get_latest_version(self, group_id, artifact_id):
            raise MavenNetworkError("simulated DNS failure: search.maven.org unreachable")

    monkeypatch.setattr(cli, "MavenCentralClient", lambda timeout=10.0, rows=100: _BoomClient())
    rc = cli.main(["scan", EXAMPLE_POM, "--out", str(tmp_path / "report.md")])
    assert rc == 2
    captured = capsys.readouterr()
    assert "simulated DNS failure" in captured.err
    assert "Traceback" not in captured.err


def test_scan_major_behind_only_flag(tmp_path, monkeypatch):
    # Fabricate a catalog where everything is a full major version ahead,
    # to exercise --major-behind-only end to end.
    catalog = {
        "org.mule.connectors:mule-http-connector": ("2.0.0", 1506613359000),
        "org.mule.connectors:mule-db-connector": ("2.0.0", 1506610832000),
        "org.mule.connectors:mule-sockets-connector": ("2.0.0", 1506611547000),
        "org.mule.connectors:mule-objectstore-connector": ("2.0.0", 1506614190000),
    }
    _patch_client(monkeypatch, catalog)
    out_path = tmp_path / "report.md"
    rc = cli.main(["scan", EXAMPLE_POM, "--out", str(out_path), "--major-behind-only"])
    assert rc == 0
    text = out_path.read_text(encoding="utf-8")
    assert "mule-http-connector" in text
    # salesforce is not-found, not major-behind, so should be filtered out
    # of the (filtered) table+details section, though it still counts in
    # the summary.
    assert "Filtered view" in text


def test_scan_explain_dry_run_when_no_api_key(tmp_path, monkeypatch, capsys):
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", 1506613359000),
        "org.mule.connectors:mule-db-connector": ("0.9.0", 1506610832000),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", 1506611547000),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", 1506614190000),
    }
    _patch_client(monkeypatch, catalog)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    out_path = tmp_path / "report.md"
    rc = cli.main(["scan", EXAMPLE_POM, "--out", str(out_path), "--explain"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "DRY RUN" in captured.out
    assert "no ANTHROPIC_API_KEY" in captured.out
