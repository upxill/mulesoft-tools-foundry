from datetime import datetime, timedelta, timezone
from pathlib import Path

from mule_connector_audit.audit import run_audit
from mule_connector_audit.report import render_json, render_markdown

from .fake_maven_client import FakeMavenClient

EXAMPLE_POM = Path(__file__).parent.parent / "examples" / "pom.xml"
NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _build_report():
    old = _ts_ms(NOW - timedelta(days=365 * 5))
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", old),
        "org.mule.connectors:mule-db-connector": ("0.9.0", old),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", old),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", old),
    }
    client = FakeMavenClient(catalog)
    return run_audit(EXAMPLE_POM, client=client, now=NOW)


def test_render_markdown_contains_all_connectors():
    report = _build_report()
    md = render_markdown(report)
    assert "mule-http-connector" in md
    assert "mule-salesforce-connector" in md
    assert "0.8.0-BETA.4" in md
    assert "Not found on Maven Central" in md
    assert "# Mule Connector Audit" in md


def test_render_markdown_major_behind_only_filter():
    report = _build_report()
    md = render_markdown(report, major_behind_only=True)
    # Nothing in this fixture is a full major version behind.
    assert "No connectors match the filter." in md


def test_render_json_is_valid_and_complete():
    import json

    report = _build_report()
    payload = json.loads(render_json(report))
    assert payload["connector_count"] == 5
    artifact_ids = {c["artifact_id"] for c in payload["connectors"]}
    assert "mule-http-connector" in artifact_ids
    salesforce = next(c for c in payload["connectors"] if c["artifact_id"] == "mule-salesforce-connector")
    assert salesforce["found_on_maven_central"] is False
    assert salesforce["staleness"] == "not-found"


def test_write_report_writes_both_files(tmp_path):
    from mule_connector_audit.report import write_report

    report = _build_report()
    md_path = tmp_path / "report.md"
    json_path = tmp_path / "report.json"
    write_report(report, md_path, json_out_path=json_path)
    assert md_path.exists()
    assert json_path.exists()
    assert "Mule Connector Audit" in md_path.read_text(encoding="utf-8")
