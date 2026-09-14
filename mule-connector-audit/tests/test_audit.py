from datetime import datetime, timedelta, timezone
from pathlib import Path

from mule_connector_audit.audit import run_audit
from mule_connector_audit.versioning import Staleness

from .fake_maven_client import FakeMavenClient

EXAMPLE_POM = Path(__file__).parent.parent / "examples" / "pom.xml"

NOW = datetime(2026, 9, 13, tzinfo=timezone.utc)


def _ts_ms(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def test_full_audit_against_example_pom_with_fake_client():
    recent = _ts_ms(NOW - timedelta(days=30))
    old = _ts_ms(NOW - timedelta(days=365 * 5))  # 5 years old -> abandoned
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", old),
        "org.mule.connectors:mule-db-connector": ("0.9.0", old),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", old),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", old),
        # mule-salesforce-connector deliberately absent -> not found
    }
    client = FakeMavenClient(catalog)
    report = run_audit(EXAMPLE_POM, client=client, now=NOW)

    by_artifact = {e.artifact_id: e for e in report.entries}
    assert len(report.entries) == 5

    # pinned 0.8.0-BETA.4 vs latest 0.9.0 -> minor-behind, and abandoned
    # (latest release is 5 years old).
    http = by_artifact["mule-http-connector"]
    assert http.staleness == Staleness.MINOR_BEHIND
    assert http.possibly_abandoned is True

    db = by_artifact["mule-db-connector"]
    assert db.staleness == Staleness.MINOR_BEHIND

    # pinned exactly 0.9.0 -> up to date, but still abandoned by date.
    sockets = by_artifact["mule-sockets-connector"]
    assert sockets.staleness == Staleness.UP_TO_DATE
    assert sockets.possibly_abandoned is True

    objectstore = by_artifact["mule-objectstore-connector"]
    assert objectstore.staleness == Staleness.MINOR_BEHIND

    salesforce = by_artifact["mule-salesforce-connector"]
    assert salesforce.staleness == Staleness.NOT_FOUND
    assert salesforce.possibly_abandoned is False


def test_recent_release_is_not_flagged_abandoned():
    recent = _ts_ms(NOW - timedelta(days=30))
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", recent),
        "org.mule.connectors:mule-db-connector": ("0.9.0", recent),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", recent),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", recent),
    }
    client = FakeMavenClient(catalog)
    report = run_audit(EXAMPLE_POM, client=client, now=NOW)
    for e in report.entries:
        if e.lookup and e.lookup.found:
            assert e.possibly_abandoned is False


def test_abandoned_threshold_is_configurable():
    just_over_one_year = _ts_ms(NOW - timedelta(days=366))
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", just_over_one_year),
        "org.mule.connectors:mule-db-connector": ("0.9.0", just_over_one_year),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", just_over_one_year),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", just_over_one_year),
    }
    client = FakeMavenClient(catalog)

    # Default threshold (~2 years / 730 days): not abandoned yet.
    report_default = run_audit(EXAMPLE_POM, client=client, now=NOW)
    http_default = next(e for e in report_default.entries if e.artifact_id == "mule-http-connector")
    assert http_default.possibly_abandoned is False

    # Tightened threshold (300 days): now flagged.
    report_tight = run_audit(EXAMPLE_POM, client=client, abandoned_days=300, now=NOW)
    http_tight = next(e for e in report_tight.entries if e.artifact_id == "mule-http-connector")
    assert http_tight.possibly_abandoned is True


def test_unpinned_version_is_handled_gracefully(tmp_path):
    pom = tmp_path / "pom.xml"
    pom.write_text(
        """<?xml version="1.0"?>
<project xmlns="http://maven.apache.org/POM/4.0.0">
    <modelVersion>4.0.0</modelVersion>
    <groupId>g</groupId><artifactId>a</artifactId><version>1.0</version>
    <dependencies>
        <dependency>
            <groupId>org.mule.connectors</groupId>
            <artifactId>mule-http-connector</artifactId>
            <version>${not.declared}</version>
        </dependency>
    </dependencies>
</project>
""",
        encoding="utf-8",
    )
    client = FakeMavenClient({"org.mule.connectors:mule-http-connector": ("0.9.0", _ts_ms(NOW))})
    report = run_audit(pom, client=client, now=NOW)
    assert len(report.entries) == 1
    assert report.entries[0].staleness == Staleness.UNPINNED


def test_only_calls_maven_client_for_connector_dependencies():
    catalog = {
        "org.mule.connectors:mule-http-connector": ("0.9.0", _ts_ms(NOW)),
        "org.mule.connectors:mule-db-connector": ("0.9.0", _ts_ms(NOW)),
        "org.mule.connectors:mule-sockets-connector": ("0.9.0", _ts_ms(NOW)),
        "org.mule.connectors:mule-objectstore-connector": ("0.9.0", _ts_ms(NOW)),
    }
    client = FakeMavenClient(catalog)
    run_audit(EXAMPLE_POM, client=client, now=NOW)
    called_artifacts = {a for _, a in client.calls}
    assert "mule-tests-functional" not in called_artifacts
    assert len(client.calls) == 5
